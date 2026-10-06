import assert from "node:assert/strict";
import fs from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import register from "../index.js";
import { createActivityWriter, trackToolActivity } from "../job-activity.js";

const id = "a".repeat(32);
const context = { agentId: "esp32", sessionKey: `agent:esp32:muse-job:${id}` };

test("activity hooks record real tool requests and returns without payloads or success claims", t => {
  const directory = fs.mkdtempSync(join(tmpdir(), "muse-activity-"));
  t.after(() => fs.rmSync(directory, { recursive: true }));
  const hooks = {};
  const config = { agents: { list: [
    { id: "esp32", tools: { profile: "full", alsoAllow: ["normal_chrome"] } },
  ] } };
  register({ config, pluginConfig: { activityDirectory: directory }, registerTool() {},
    on: (name, callback) => { hooks[name] = callback; } });
  const path = join(directory, `${id}.json`);
  const read = () => JSON.parse(fs.readFileSync(path, "utf8"));
  const event = { toolName: "normal_chrome", params: { command: "fill", value: "PRIVATE_SEARCH" } };
  assert.equal(hooks.before_tool_call(event, context), undefined);
  assert.deepEqual(read(), { id, detail: "Requested: normal_chrome / fill" });
  assert.equal(fs.statSync(path).mode & 0o777, 0o600);
  const returned = { toolName: "normal_chrome",
    message: { content: [{ type: "text", text: "PRIVATE_PAGE_CONTENT" }] } };
  assert.equal(hooks.tool_result_persist(returned, context), undefined);
  assert.equal(read().detail, "Requested: normal_chrome / fill");
  hooks.tool_result_persist({ ...returned, message: { ...returned.message, isError: true } }, context);
  assert.equal(read().detail, "Tool error: normal_chrome");
  hooks.tool_result_persist({ ...returned, isSynthetic: true }, context);
  assert.equal(read().detail, "Tool result unavailable: normal_chrome");
  hooks.before_tool_call({ toolName: "exec", params: { command: "PRIVATE_SHELL" } }, context);
  assert.equal(read().detail, "Requested: exec");
  assert.deepEqual(fs.readdirSync(directory), [`${id}.json`]);
});

test("tool execution reports its exact command and propagates real errors and results", async t => {
  const directory = fs.mkdtempSync(join(tmpdir(), "muse-activity-"));
  t.after(() => fs.rmSync(directory, { recursive: true }));
  const record = createActivityWriter({ directory });
  const read = () => JSON.parse(fs.readFileSync(join(directory, `${id}.json`), "utf8")).detail;
  const result = { details: { volume: 50 } };
  const signal = new AbortController().signal;
  const tool = trackToolActivity({
    name: "mac_control",
    async execute(callId, args, passedSignal) {
      assert.equal(read(), "Requested: mac_control / audio_status");
      assert.equal(callId, "call-1");
      assert.deepEqual(args, { command: "audio_status" });
      assert.equal(passedSignal, signal);
      return result;
    },
  }, context, record);
  assert.equal(await tool.execute("call-1", { command: "audio_status" }, signal), result);
  assert.equal(read(), "Tool returned: mac_control / audio_status");
  const failure = new Error("Native action denied");
  const failing = trackToolActivity({ name: "normal_chrome", execute() { throw failure; } }, context, record);
  await assert.rejects(failing.execute("call-2", { command: "fill" }), error => error === failure);
  assert.equal(read(), "Tool error: normal_chrome / fill");
});
test("activity is bounded, device-job-only, and respects explicit agent opt-in", t => {
  const directory = fs.mkdtempSync(join(tmpdir(), "muse-activity-"));
  t.after(() => fs.rmSync(directory, { recursive: true }));
  const record = createActivityWriter({ directory });
  for (const invalid of [
    { ...context, agentId: "main" }, { ...context, sessionKey: "agent:esp32:main" },
    { ...context, sessionKey: "agent:esp32:muse-job:../../private" }, { agentId: "esp32" },
  ]) record({ toolName: "exec" }, invalid, "Requested");
  assert.deepEqual(fs.readdirSync(directory), []);
  record({ toolName: "\u00e9".repeat(300) }, context, "Requested");
  assert.ok(Buffer.byteLength(JSON.parse(fs.readFileSync(join(directory, `${id}.json`))).detail) <= 256);
  const hooks = {};
  register({ config: {}, pluginConfig: { activityDirectory: directory }, registerTool() {},
    on: (name, callback) => { hooks[name] = callback; } });
  fs.unlinkSync(join(directory, `${id}.json`));
  hooks.before_tool_call({ toolName: "normal_chrome", params: { command: "fill" } }, context);
  hooks.tool_result_persist({ toolName: "normal_chrome", message: {} }, context);
  assert.deepEqual(fs.readdirSync(directory), []);
  assert.throws(() => createActivityWriter({ directory: "relative" }), /absolute/);
});

test("activity I/O failure is logged and never changes a tool result", t => {
  const directory = fs.mkdtempSync(join(tmpdir(), "muse-activity-"));
  t.after(() => fs.rmSync(directory, { recursive: true }));
  const path = join(directory, "not-a-directory");
  fs.writeFileSync(path, "");
  const errors = [];
  const record = createActivityWriter({ directory: path, logger: { error: message => errors.push(message) } });
  record({ toolName: "exec" }, context, "Requested");
  assert.equal(errors.length, 1);
  assert.match(errors[0], /could not be recorded/);
});
