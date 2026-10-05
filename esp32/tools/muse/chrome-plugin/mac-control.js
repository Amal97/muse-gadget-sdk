import childProcess from "node:child_process";
import fs from "node:fs";
import { randomUUID } from "node:crypto";
import { homedir } from "node:os";
import { join } from "node:path";

const AUDIO_SCRIPT = `
function run(argv) {
  const app = Application.currentApplication();
  app.includeStandardAdditions = true;
  if (argv[0] === "volume") app.setVolume(null, {outputVolume: Number(argv[1])});
  if (argv[0] === "mute") app.setVolume(null, {outputMuted: argv[1] === "true"});
  return JSON.stringify(app.getVolumeSettings());
}`;
const APP_SCRIPT = `
ObjC.import("AppKit");
function run(argv) {
  const workspace = $.NSWorkspace.sharedWorkspace;
  const path = workspace.fullPathForApplication(argv[0]);
  if (!path) throw new Error("Requested application was not found.");
  const bundle = $.NSBundle.bundleWithPath(path);
  const bundleId = ObjC.unwrap(bundle.bundleIdentifier);
  const apps = workspace.runningApplications;
  let running = false;
  for (let i = 0; i < apps.count; i++) {
    if (ObjC.unwrap(apps.objectAtIndex(i).bundleIdentifier) === bundleId) running = true;
  }
  return JSON.stringify({application: argv[0], bundleId, path: ObjC.unwrap(path), running});
}`;
const FIELDS = {
  capabilities: [], audio_status: [], volume_set: ["volume"], mute_set: ["muted"],
  app_status: ["app"], app_open: ["app"],
  bluetooth_status: [], bluetooth_set: ["enabled"],
  wifi_status: ["networkInterface"], wifi_set: ["enabled", "networkInterface"],
  pending: [], confirm: ["confirmationId", "target"], cancel: ["confirmationId"],
};

export const MAC_GUIDANCE =
  "Use mac_control for Mac application, sound, Bluetooth and Wi-Fi requests, not shell scripts. " +
  "audio_status reads BOTH volume and mute; do not use mute_set for a status question. " +
  "Use normal_chrome for website actions. Report success only from verified tool results. " +
  "Turning Bluetooth or Wi-Fi off requires a separate user confirmation: explain the " +
  "disconnection warning and wait for a new user request. Then use pending and confirm " +
  "with that confirmationId and target. Never confirm in the initiating request or " +
  "invent consent. Cancelling a pending request needs NO confirmation: when the user " +
  "asks to cancel, immediately use pending and cancel with its confirmationId. " +
  "Do not ask for permission to cancel. Do not use sudo, change privacy permissions, or retry uncertain actions.";

export function runNative(executable, args, signal) {
  return new Promise((resolve, reject) => {
    childProcess.execFile(executable, args,
      { encoding: "utf8", timeout: 15000, maxBuffer: 65536, signal },
      (error, stdout, stderr) => {
        if (error) reject(new Error(stderr.trim() || error.message, { cause: error }));
        else resolve(stdout.trim());
      });
  });
}

export function findBlueutil() {
  return [
    "/opt/homebrew/bin/blueutil", "/usr/local/bin/blueutil",
    "/opt/homebrew/opt/blueutil/bin/blueutil", "/usr/local/opt/blueutil/bin/blueutil",
  ].find(path => fs.existsSync(path));
}

function parseObject(raw) {
  const value = JSON.parse(raw);
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Native Mac control returned an invalid object.");
  }
  return value;
}

export function createMacController({
  run = runNative, bluetoothBinary = findBlueutil,
  stateDir = join(homedir(), ".openclaw/muse-esp32/mac-control"),
  platform = process.platform, now = Date.now,
} = {}) {
  const pendingPath = join(stateDir, "pending.json");

  function readPending() {
    if (!fs.existsSync(pendingPath)) return null;
    const value = parseObject(fs.readFileSync(pendingPath, "utf8"));
    if (typeof value.id !== "string" || typeof value.sessionKey !== "string" ||
        !["bluetooth", "wifi"].includes(value.target) ||
        !["pending", "executing", "completed", "failed", "expired", "cancelled"].includes(value.state) ||
        !Number.isFinite(value.expiresAt) || value.enabled !== false ||
        (value.target === "wifi" && typeof value.networkInterface !== "string")) {
      throw new Error("Mac-control confirmation state is invalid; no action ran.");
    }
    return value;
  }

  function savePending(value) {
    fs.mkdirSync(stateDir, { recursive: true, mode: 0o700 });
    const temporary = join(stateDir, `${randomUUID()}.tmp`);
    fs.writeFileSync(temporary, JSON.stringify(value), { mode: 0o600, flag: "wx" });
    fs.renameSync(temporary, pendingPath);
  }

  async function audio(mode, value, signal) {
    const result = parseObject(await run("/usr/bin/osascript",
      ["-l", "JavaScript", "-e", AUDIO_SCRIPT, "--", mode, String(value)], signal));
    if (!Number.isInteger(result.outputVolume) || result.outputVolume < 0 ||
        result.outputVolume > 100 || typeof result.outputMuted !== "boolean") {
      throw new Error("Mac audio state could not be verified.");
    }
    return { volume: result.outputVolume, muted: result.outputMuted };
  }

  async function appInfo(app, signal) {
    if (typeof app !== "string" || !app.trim() || app.length > 200 ||
        app.includes("/") || /[\u0000-\u001f]/.test(app)) {
      throw new Error("Specify an installed application name, not a path or script.");
    }
    const result = parseObject(await run("/usr/bin/osascript",
      ["-l", "JavaScript", "-e", APP_SCRIPT, "--", app.trim()], signal));
    if (typeof result.bundleId !== "string" || !result.bundleId ||
        typeof result.path !== "string" || !result.path.startsWith("/") ||
        typeof result.running !== "boolean") {
      throw new Error("Requested application's identity and running state could not be verified.");
    }
    return result;
  }

  async function wifiInterface(requested, signal) {
    const ports = await run("/usr/sbin/networksetup", ["-listallhardwareports"], signal);
    const interfaces = [...ports.matchAll(/Hardware Port: (?:Wi-Fi|AirPort)\r?\nDevice: ([a-zA-Z0-9]+)(?:\r?\n|$)/g)]
      .map(match => match[1]);
    if (requested !== undefined) {
      if (!interfaces.includes(requested)) throw new Error("Requested interface is not an available Wi-Fi interface.");
      return requested;
    }
    if (interfaces.length !== 1) {
      throw new Error("Expected one Wi-Fi interface; specify networkInterface if multiple are available.");
    }
    return interfaces[0];
  }

  function bluetoothPath() {
    const executable = bluetoothBinary();
    if (!executable) {
      throw new Error("Bluetooth control needs blueutil. Install it with Homebrew and allow Bluetooth access if macOS requests it.");
    }
    return executable;
  }

  async function power(target, networkInterface, signal) {
    if (target === "bluetooth") {
      const output = parseObject(await run("/usr/sbin/system_profiler", ["SPBluetoothDataType", "-json"], signal));
      const controllers = output.SPBluetoothDataType;
      if (!Array.isArray(controllers) || controllers.length !== 1) {
        throw new Error("Expected one readable Bluetooth controller; power state could not be verified.");
      }
      const state = controllers[0].controller_properties?.controller_state;
      if (!["attrib_on", "attrib_off"].includes(state)) {
        throw new Error("Bluetooth power state could not be verified.");
      }
      return { target, enabled: state === "attrib_on" };
    }
    const device = await wifiInterface(networkInterface, signal);
    const output = await run("/usr/sbin/networksetup", ["-getairportpower", device], signal);
    const match = output.match(/^Wi-Fi Power \(([a-zA-Z0-9]+)\): (On|Off)$/);
    if (!match || match[1] !== device) throw new Error("Wi-Fi power state could not be verified.");
    return { target, enabled: match[2] === "On", networkInterface: device };
  }

  async function setPower(target, enabled, networkInterface, signal) {
    if (target === "bluetooth") {
      try {
        await run(bluetoothPath(), ["--power", enabled ? "1" : "0"], signal);
      } catch (error) {
        throw new Error("Bluetooth power change failed. Check macOS Bluetooth access for blueutil/the gateway; " +
          "do not bypass permissions. " + error.message, { cause: error });
      }
    } else {
      await run("/usr/sbin/networksetup", ["-setairportpower", networkInterface, enabled ? "on" : "off"], signal);
    }
    const result = await power(target, networkInterface, signal);
    if (result.enabled !== enabled) throw new Error(`${target} did not reach the requested power state.`);
    return { ...result, verified: true };
  }

  async function switchPower(args, sessionKey, signal) {
    if (typeof args.enabled !== "boolean") throw new Error("enabled must be true or false.");
    const target = args.command === "bluetooth_set" ? "bluetooth" : "wifi";
    const current = await power(target, args.networkInterface, signal);
    if (current.enabled === args.enabled) return { ...current, verified: true, changed: false };
    if (target === "bluetooth") bluetoothPath();
    if (args.enabled) return setPower(target, true, current.networkInterface, signal);
    if (typeof sessionKey !== "string" || !sessionKey) {
      throw new Error("A gadget session is required to request a disconnection confirmation.");
    }
    let pending = readPending();
    if (pending?.state === "executing") throw new Error("A Mac connectivity change is already executing.");
    if (pending?.state === "pending" && pending.expiresAt > now()) {
      if (pending.target !== target || pending.networkInterface !== current.networkInterface) {
        throw new Error("Confirm or cancel the existing connectivity request before preparing another.");
      }
    } else {
      pending = { id: randomUUID(), state: "pending", target, enabled: false,
        sessionKey, expiresAt: now() + 180000, ...(current.networkInterface
          ? { networkInterface: current.networkInterface } : {}) };
      savePending(pending);
    }
    return { state: "confirmation_required", confirmationId: pending.id, target,
      expiresAt: pending.expiresAt,
      warning: target === "bluetooth"
        ? "Turning Bluetooth off can disconnect your keyboard, mouse, headphones, and other Bluetooth devices."
        : "Turning Mac Wi-Fi off can disconnect the ESP32 bridge and internet. The gadget may not receive the final reply.",
      instruction: "Wait for a separate user confirmation request before calling confirm. No power change has run." };
  }

  return {
    async execute(args, sessionKey, signal) {
      if (platform !== "darwin") throw new Error("Native Mac controls require macOS.");
      if (!args || typeof args !== "object" || !Object.hasOwn(FIELDS, args.command) ||
          Object.keys(args).some(key => key !== "command" && !FIELDS[args.command].includes(key))) {
        throw new Error("Invalid mac_control arguments.");
      }
      switch (args.command) {
        case "capabilities":
          return { platform: "macOS", apps: true, audio: true, wifi: true,
            bluetooth: { status: true, powerControl: Boolean(bluetoothBinary()) },
            confirmationRequiredFor: ["bluetooth off", "wifi off"] };
        case "audio_status": return audio("status", "", signal);
        case "volume_set": {
          if (!Number.isInteger(args.volume) || args.volume < 0 || args.volume > 100) {
            throw new Error("volume must be an integer from 0 to 100.");
          }
          const result = await audio("volume", args.volume, signal);
          if (result.volume !== args.volume) throw new Error("Requested output volume could not be verified.");
          return { ...result, verified: true };
        }
        case "mute_set": {
          if (typeof args.muted !== "boolean") throw new Error("muted must be true or false.");
          const result = await audio("mute", args.muted, signal);
          if (result.muted !== args.muted) throw new Error("Requested mute state could not be verified.");
          return { ...result, verified: true };
        }
        case "app_status":
        case "app_open": {
          let result = await appInfo(args.app, signal);
          if (args.command === "app_open") {
            await run("/usr/bin/open", ["-a", result.path], signal);
            for (let attempt = 0; attempt < 10; attempt++) {
              result = await appInfo(args.app, signal);
              if (result.running) break;
            }
            if (!result.running) throw new Error("Application launch was requested, but running state could not be verified.");
          }
          return { application: result.application, bundleId: result.bundleId,
            running: result.running, verified: true };
        }
        case "bluetooth_status": return power("bluetooth", undefined, signal);
        case "wifi_status": return power("wifi", args.networkInterface, signal);
        case "bluetooth_set":
        case "wifi_set": return switchPower(args, sessionKey, signal);
        case "pending": {
          const pending = readPending();
          if (!pending) return { state: "none" };
          if (pending.state === "pending" && pending.expiresAt <= now()) {
            pending.state = "expired";
            savePending(pending);
          }
          const { sessionKey: _origin, ...result } = pending;
          return { ...result, confirmationId: pending.id };
        }
        case "cancel":
        case "confirm": {
          const pending = readPending();
          if (!pending || pending.id !== args.confirmationId || pending.state !== "pending") {
            throw new Error("No matching pending confirmation; no action ran.");
          }
          if (args.command === "cancel") {
            pending.state = "cancelled";
            savePending(pending);
            return { state: "cancelled", target: pending.target, changed: false };
          }
          if (pending.expiresAt <= now()) throw new Error("Connectivity confirmation expired; no action ran.");
          if (typeof sessionKey !== "string" || !sessionKey || sessionKey === pending.sessionKey) {
            throw new Error("Confirmation must come from a separate gadget request; no action ran.");
          }
          if (args.target !== pending.target) throw new Error("Confirmation target does not match the pending action.");
          pending.state = "executing";
          savePending(pending);
          try {
            const result = await setPower(pending.target, false, pending.networkInterface, signal);
            pending.state = "completed";
            savePending(pending);
            return result;
          } catch (error) {
            pending.state = "failed";
            savePending(pending);
            throw new Error("Connectivity action did not confirm success. Check current status before any new request. " +
              error.message, { cause: error });
          }
        }
      }
    },
  };
}

export function createMacTool(controller, sessionKey) {
  return {
    name: "mac_control", label: "Mac control", description: MAC_GUIDANCE,
    parameters: {
      type: "object", additionalProperties: false, required: ["command"],
      properties: {
        command: { type: "string", enum: Object.keys(FIELDS),
          description: "audio_status reads volume/mute; volume_set and mute_set change them. " +
            "app_status checks an app; app_open launches it. bluetooth_status/wifi_status read power. " +
            "bluetooth_set/wifi_set request changes. pending reads a confirmation; cancel cancels it without approval." },
        app: { type: "string", description: "Installed application name, for example Calculator." },
        volume: { type: "integer", minimum: 0, maximum: 100, description: "Output volume percentage." },
        muted: { type: "boolean" },
        enabled: { type: "boolean", description: "Requested Bluetooth or Wi-Fi power state." },
        networkInterface: { type: "string", description: "Optional actual Wi-Fi device identifier." },
        confirmationId: { type: "string", description: "Exact ID returned by pending or confirmation_required." },
        target: { type: "string", enum: ["bluetooth", "wifi"], description: "Explicitly confirmed target." },
      },
    },
    async execute(_id, args, signal) {
      const result = await controller.execute(args, sessionKey, signal);
      return { content: [{ type: "text", text: JSON.stringify(result) }], details: result };
    },
  };
}
