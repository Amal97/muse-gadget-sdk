import assert from "node:assert/strict";
import childProcess from "node:child_process";
import fs from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import register, { enabledForAgent } from "../index.js";
import { createMacController, createMacTool, runNative } from "../mac-control.js";

function fixture(t, overrides = {}) {
  const stateDir = fs.mkdtempSync(join(tmpdir(), "muse-mac-control-test-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true }));
  const state = { volume: 50, muted: false, bluetooth: true, wifi: true, running: false };
  const calls = [];
  const run = async (executable, args, signal) => {
    calls.push({ executable, args, signal });
    if (executable === "/usr/bin/osascript") {
      const mode = args.at(-2);
      if (args.includes("Calculator")) {
        return JSON.stringify({ application: "Calculator", bundleId: "com.apple.calculator",
          path: "/System/Applications/Calculator.app", running: state.running });
      }
      if (mode === "volume") state.volume = Number(args.at(-1));
      if (mode === "mute") state.muted = args.at(-1) === "true";
      return JSON.stringify({ outputVolume: state.volume, outputMuted: state.muted });
    }
    if (executable === "/usr/bin/open") { state.running = true; return ""; }
    if (executable === "/test/blueutil") {
      if (args.length === 2) state.bluetooth = args[1] === "1";
      return state.bluetooth ? "1" : "0";
    }
    if (executable === "/usr/sbin/system_profiler") {
      return JSON.stringify({ SPBluetoothDataType: [{
        controller_properties: { controller_state: state.bluetooth ? "attrib_on" : "attrib_off" },
      }] });
    }
    if (args[0] === "-listallhardwareports") return "Hardware Port: Wi-Fi\nDevice: en0\nEthernet Address: omitted\n";
    if (args[0] === "-setairportpower") { state.wifi = args[2] === "on"; return ""; }
    return `Wi-Fi Power (en0): ${state.wifi ? "On" : "Off"}`;
  };
  const controller = createMacController({
    run, bluetoothBinary: () => "/test/blueutil", stateDir, platform: "darwin", ...overrides,
  });
  return { controller, state, calls, stateDir, run };
}

test("Mac controls need an explicit tool opt-in and do not change Chrome or other agents", () => {
  const config = { agents: { list: [
    { id: "esp32", tools: { profile: "full", alsoAllow: ["normal_chrome"] } },
    { id: "main", tools: { profile: "full", alsoAllow: ["mac_control"] } },
  ] } };
  assert.equal(enabledForAgent(config, "esp32", "mac_control"), false);
  config.agents.list[0].tools.alsoAllow = ["muse-normal-chrome"];
  assert.equal(enabledForAgent(config, "esp32"), true);
  assert.equal(enabledForAgent(config, "esp32", "mac_control"), false);
  config.agents.list[0].tools.alsoAllow.push("mac_control");
  assert.equal(enabledForAgent(config, "esp32", "mac_control"), true);
  assert.equal(enabledForAgent(config, "main", "mac_control"), false);
  const tools = {};
  const hooks = {};
  register({ config, registerTool: (factory, options) => { tools[options.names[0]] = factory; },
    on: (name, handler) => { hooks[name] = handler; } });
  assert.equal(tools.mac_control({ agentId: "main" }), null);
  assert.equal(tools.mac_control({ agentId: "esp32", sandboxed: true }), null);
  if (process.platform === "darwin") {
    assert.equal(tools.mac_control({ agentId: "esp32", sessionKey: "test" }).name, "mac_control");
  }
  assert.ok(hooks.before_agent_start({}, { agentId: "esp32" }).prependContext.includes("mac_control"));
  const event = { toolName: "exec", params: { command: "/opt/homebrew/bin/blueutil --power 0" } };
  assert.equal(hooks.before_tool_call(event, { agentId: "esp32" }).block, true);
  assert.equal(hooks.before_tool_call(event, { agentId: "main" }), undefined);
  assert.equal(hooks.before_tool_call({ toolName: "exec", params: { command: "echo hello" } },
    { agentId: "esp32" }), undefined);
  assert.equal(hooks.before_tool_call({ toolName: "exec", params: { command: "brew install blueutil" } },
    { agentId: "esp32" }), undefined);
});

test("audio changes are range-checked and read back", async t => {
  const { controller, calls } = fixture(t);
  assert.deepEqual(await controller.execute({ command: "audio_status" }), { volume: 50, muted: false });
  assert.equal((await controller.execute({ command: "volume_set", volume: 30 })).volume, 30);
  assert.equal((await controller.execute({ command: "mute_set", muted: true })).muted, true);
  const count = calls.length;
  for (const args of [{ command: "volume_set", volume: -1 }, { command: "volume_set", volume: 101 },
    { command: "volume_set", volume: 12.5 }, { command: "mute_set", muted: "yes" },
    { command: "audio_status", script: "do something" }]) {
    await assert.rejects(controller.execute(args), /volume|muted|Invalid/);
  }
  assert.equal(calls.length, count);
});

test("app opening uses a resolved application path and verifies its running state", async t => {
  const { controller, calls } = fixture(t);
  assert.equal((await controller.execute({ command: "app_status", app: "Calculator" })).running, false);
  assert.equal((await controller.execute({ command: "app_open", app: "Calculator" })).running, true);
  assert.deepEqual(calls.find(call => call.executable === "/usr/bin/open").args,
    ["-a", "/System/Applications/Calculator.app"]);
  for (const app of ["", "/tmp/script.app", "Calculator\nanything"]) {
    await assert.rejects(controller.execute({ command: "app_open", app }), /application name/);
  }
});

test("unsupported or unverifiable native states are explicit failures", async t => {
  const { controller } = fixture(t, { platform: "linux" });
  await assert.rejects(controller.execute({ command: "audio_status" }), /require macOS/);
  const missingFixture = fixture(t, { bluetoothBinary: () => undefined });
  const missing = missingFixture.controller;
  assert.equal((await missing.execute({ command: "capabilities" })).bluetooth.powerControl, false);
  assert.equal((await missing.execute({ command: "bluetooth_status" })).enabled, true);
  missingFixture.state.bluetooth = false;
  await assert.rejects(missing.execute({ command: "bluetooth_set", enabled: true }), /needs blueutil/);
  const invalid = fixture(t, { run: async () => "not JSON" }).controller;
  await assert.rejects(invalid.execute({ command: "audio_status" }), /JSON/);
  const unchanged = fixture(t, { run: async () =>
    '{"outputVolume":50,"outputMuted":false}' }).controller;
  await assert.rejects(unchanged.execute({ command: "volume_set", volume: 20 }), /could not be verified/);
  const notRunning = fixture(t, { run: async () => JSON.stringify({
    application: "Calculator", bundleId: "com.apple.calculator",
    path: "/System/Applications/Calculator.app", running: false,
  }) }).controller;
  await assert.rejects(notRunning.execute({ command: "app_open", app: "Calculator" }), /running state/);
});

test("power status detects real Wi-Fi interfaces without exposing network identities", async t => {
  const { controller, calls } = fixture(t);
  assert.deepEqual(await controller.execute({ command: "bluetooth_status" }),
    { target: "bluetooth", enabled: true });
  assert.deepEqual(await controller.execute({ command: "wifi_status" }),
    { target: "wifi", enabled: true, networkInterface: "en0" });
  await assert.rejects(controller.execute({ command: "wifi_status", networkInterface: "en99" }),
    /not an available Wi-Fi interface/);
  assert.equal(calls.some(call => call.args.includes("-getairportnetwork")), false);
});

test("power off requires a later request, a matching target, and a single-use confirmation", async t => {
  const { controller, state, stateDir, calls } = fixture(t);
  const pending = await controller.execute({ command: "bluetooth_set", enabled: false }, "request-1");
  assert.equal(pending.state, "confirmation_required");
  assert.equal(state.bluetooth, true);
  assert.equal(fs.statSync(join(stateDir, "pending.json")).mode & 0o777, 0o600);
  const confirm = { command: "confirm", target: "bluetooth", confirmationId: pending.confirmationId };
  await assert.rejects(controller.execute(confirm, "request-1"), /separate gadget request/);
  await assert.rejects(controller.execute({ ...confirm, target: "wifi" }, "request-2"), /does not match/);
  assert.equal(state.bluetooth, true);
  const result = await controller.execute(confirm, "request-2");
  assert.equal(result.enabled, false);
  assert.equal(result.verified, true);
  await assert.rejects(controller.execute(confirm, "request-3"), /No matching pending/);
  assert.equal(calls.filter(call => call.args[0] === "--power" && call.args.length === 2).length, 1);
  assert.equal((await controller.execute({ command: "pending" })).state, "completed");
});

test("pending disconnections survive factory/session restarts, expire, and can be cancelled", async t => {
  let time = 0;
  const original = fixture(t, { now: () => time });
  const pending = await original.controller.execute({ command: "wifi_set", enabled: false }, "first");
  assert.ok(pending.warning.includes("ESP32"));
  const restored = createMacController({ run: original.run, stateDir: original.stateDir,
    bluetoothBinary: () => "/test/blueutil", platform: "darwin", now: () => time });
  assert.equal((await restored.execute({ command: "pending" })).id, pending.confirmationId);
  assert.equal((await restored.execute({ command: "pending" })).confirmationId, pending.confirmationId);
  await assert.rejects(restored.execute({ command: "bluetooth_set", enabled: false }, "other"),
    /existing connectivity request/);
  await restored.execute({ command: "cancel", confirmationId: pending.confirmationId });
  assert.equal(original.state.wifi, true);
  const next = await restored.execute({ command: "wifi_set", enabled: false }, "new");
  time = 180001;
  await assert.rejects(restored.execute({ command: "confirm", target: "wifi",
    confirmationId: next.confirmationId }, "later"), /expired/);
  assert.equal((await restored.execute({ command: "pending" })).state, "expired");
  assert.equal(original.state.wifi, true);
});

test("failed confirmed changes are not success or automatically repeated", async t => {
  const fixtureState = fixture(t);
  const run = async (executable, args, signal) => {
    if (args[0] === "--power" && args.length === 2) throw new Error("Permission denied.");
    return fixtureState.run(executable, args, signal);
  };
  const controller = createMacController({ run, stateDir: fixtureState.stateDir,
    bluetoothBinary: () => "/test/blueutil", platform: "darwin" });
  const pending = await controller.execute({ command: "bluetooth_set", enabled: false }, "first");
  const confirm = { command: "confirm", target: "bluetooth", confirmationId: pending.confirmationId };
  await assert.rejects(controller.execute(confirm, "second"), /did not confirm success.*Permission denied/s);
  assert.equal((await controller.execute({ command: "pending" })).state, "failed");
  await assert.rejects(controller.execute(confirm, "third"), /No matching pending/);
});

test("native execution passes fixed argv without a shell and propagates cancellation", async t => {
  let call;
  t.mock.method(childProcess, "execFile", (executable, args, options, callback) => {
    call = { executable, args, options };
    callback(null, "ok\n", "");
  });
  const value = "O'Brien $(not-a-command)";
  assert.equal(await runNative("/usr/bin/open", ["-a", value]), "ok");
  assert.deepEqual(call.args, ["-a", value]);
  assert.equal(call.options.shell, undefined);
  t.mock.restoreAll();
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(runNative("/usr/bin/osascript", ["-e", "get volume settings"],
    controller.signal), /aborted/i);
});

test("structured tool preserves controller results and the originating session", async () => {
  const calls = [];
  const controller = { execute: async (...args) => { calls.push(args); return { volume: 50 }; } };
  const signal = new AbortController().signal;
  const result = await createMacTool(controller, "agent:esp32:muse-job:test")
    .execute("id", { command: "audio_status" }, signal);
  assert.deepEqual(calls, [[{ command: "audio_status" }, "agent:esp32:muse-job:test", signal]]);
  assert.deepEqual(result.details, { volume: 50 });
});
