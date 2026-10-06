import fs from "node:fs";
import { randomUUID } from "node:crypto";
import { homedir } from "node:os";
import { isAbsolute, join } from "node:path";

export function createActivityWriter({
  directory = join(homedir(), ".openclaw/muse-esp32/job-activity"),
  logger = console,
} = {}) {
  if (typeof directory !== "string" || !isAbsolute(directory)) {
    throw new Error("activityDirectory must be an absolute local path.");
  }
  return (event, context, phase) => {
    const match = /^agent:esp32:muse-job:([0-9a-f]{32})$/.exec(context.sessionKey ?? "");
    if (context.agentId !== "esp32" || !match || typeof event.toolName !== "string") return;
    let name = event.toolName;
    const command = event.params?.command;
    if (["normal_chrome", "mac_control"].includes(name) &&
        typeof command === "string" && /^[a-z_]+$/.test(command)) {
      name += " / " + command;
    }
    const text = `${phase}: ${name}`;
    const bytes = Buffer.from(text);
    let detail = text;
    if (bytes.length > 256) {
      detail = new TextDecoder("utf-8", { fatal: false }).decode(bytes.subarray(0, 250)) + "...";
    }
    const record = { id: match[1], detail };
    const temporary = join(directory, `${match[1]}.${randomUUID()}.tmp`);
    let temporaryCreated = false;
    try {
      fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
      fs.writeFileSync(temporary, JSON.stringify(record), { mode: 0o600, flag: "wx" });
      temporaryCreated = true;
      fs.renameSync(temporary, join(directory, `${match[1]}.json`));
    } catch (error) {
      if (!(error instanceof Error) || !("code" in error)) throw error;
      logger.error(`Muse job activity could not be recorded (${error.code}).`);
    } finally {
      if (temporaryCreated) {
        try {
          fs.unlinkSync(temporary);
        } catch (error) {
          if (error.code !== "ENOENT") throw error;
        }
      }
    }
  };
}

export function trackToolActivity(tool, context, record) {
  return {
    ...tool,
    async execute(id, args, ...rest) {
      const event = { toolName: tool.name, params: args };
      record(event, context, "Requested");
      try {
        const result = await tool.execute(id, args, ...rest);
        record(event, context, "Tool returned");
        return result;
      } catch (error) {
        record(event, context, "Tool error");
        throw error;
      }
    },
  };
}
