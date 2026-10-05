import assert from "node:assert/strict";
import childProcess from "node:child_process";
import { EventEmitter } from "node:events";
import { test } from "node:test";
import register, { createTool, enabledForAgent, invokeHelper } from "../index.js";

const config = {
  agents: { list: [
    { id: "esp32", tools: { profile: "full", alsoAllow: ["normal_chrome"], deny: ["browser", "process"] } },
    { id: "main", tools: { profile: "full", alsoAllow: ["normal_chrome"] } },
  ] },
};

test("tool is optional, ESP32-only, and respects the existing control opt-in", () => {
  assert.equal(enabledForAgent(config, "esp32"), true);
  assert.equal(enabledForAgent(config, "main"), false);
  assert.equal(enabledForAgent({ agents: { list: [] } }, "esp32"), false);
  for (const tools of [
    { profile: "full" },
    { profile: "full", alsoAllow: ["normal_chrome"], deny: ["*"] },
    { profile: "full", alsoAllow: ["normal_chrome"], deny: ["normal_chrome"] },
    { profile: "minimal", alsoAllow: ["normal_chrome"] },
  ]) {
    assert.equal(enabledForAgent({ agents: { list: [{ id: "esp32", tools }] } }, "esp32"), false);
  }
  let factory, options;
  register({ config, registerTool: (tool, opts) => {
    if (opts.names.includes("normal_chrome")) { factory = tool; options = opts; }
  }, on() {} });
  assert.equal(options.optional, true);
  assert.equal(factory({ agentId: "esp32" }).name, "normal_chrome");
  assert.equal(factory({ agentId: "main" }), null);
  assert.equal(factory({ agentId: "esp32", sandboxed: true }), null);
});

test("structured typing preserves quotes and shell-like text exactly", async () => {
  const calls = [];
  const tool = createTool(async (...args) => {
    calls.push(args);
    return { structuredContent: { snapshot: { id: "1_0", children: [{ id: "1_3" }] } } };
  });
  const value = "O'Brien \"test\" $HOME $(not-a-command)";
  const signal = new AbortController().signal;
  await tool.execute("snapshot", { command: "take_snapshot", pageId: 2 }, signal);
  await tool.execute("id", { command: "fill", pageId: 2, uid: "1_3", value }, signal);
  assert.deepEqual(calls.at(-1), ["fill", { pageId: 2, uid: "1_3", value }, signal]);
  await assert.rejects(tool.execute("id", { command: "fill", browserUrl: "http://other" }), /Invalid/);
  await assert.rejects(tool.execute("id", { command: "stop" }), /Invalid/);
  assert.equal(calls.length, 2);
});

test("invented IDs return the requested snapshot without running the action", async () => {
  const calls = [];
  const tool = createTool(async (...args) => {
    calls.push(args);
    return { structuredContent: { snapshot: { id: "1_0", children: [{ id: "1_3" }] } } };
  });
  await assert.rejects(tool.execute("fill", {
    command: "fill", pageId: 2, uid: "ACTUAL_SNAPSHOT_UID", value: "test",
  }), /No fill action ran.*actual snapshot/s);
  assert.deepEqual(calls, [["take_snapshot", { pageId: 2 }, undefined]]);
  await assert.rejects(tool.execute("enter", { command: "press_key", pageId: 2, key: "Enter" }),
    /previous fill failed/);
  assert.equal(calls.length, 1);
  await tool.execute("fill", { command: "fill", pageId: 2, uid: "1_3", value: "test" });
  await tool.execute("enter", { command: "press_key", pageId: 2, key: "Enter" });
  assert.equal(calls.at(-1)[0], "press_key");
});

test("a failed fill blocks subsequent submission and navigation invalidates IDs", async () => {
  let fail = false;
  const calls = [];
  const tool = createTool(async (command, parameters) => {
    calls.push(command);
    if (command === "fill" && fail) throw new Error("Chrome disconnected.");
    return { structuredContent: { snapshot: { id: "1_0", children: [{ id: "1_3" }] } } };
  });
  await tool.execute("snapshot", { command: "take_snapshot", pageId: 2 });
  fail = true;
  await assert.rejects(tool.execute("fill", {
    command: "fill", pageId: 2, uid: "1_3", value: "test",
  }), /disconnected/);
  for (const command of ["click", "press_key", "evaluate_script"]) {
    await assert.rejects(tool.execute("submit", { command, pageId: 2 }), /previous fill failed/);
  }
  assert.deepEqual(calls, ["take_snapshot", "fill"]);
  await tool.execute("navigate", { command: "navigate_page", pageId: 2, url: "about:blank" });
  fail = false;
  await assert.rejects(tool.execute("fill", {
    command: "fill", pageId: 2, uid: "1_3", value: "test",
  }), /No fill action ran/);
  assert.equal(calls.at(-1), "take_snapshot");
});

test("helper receives stdin JSON, never a shell command", async t => {
  let invocation, input;
  t.mock.method(childProcess, "execFile", (executable, args, options, callback) => {
    invocation = { executable, args, options };
    const stdin = new EventEmitter();
    stdin.end = value => {
      input = value;
      callback(null, '{"structuredContent":{}}', "");
    };
    return { stdin };
  });
  const parameters = { pageId: 2, uid: "1_3", value: "O'Brien \"test\" $HOME" };
  await invokeHelper("fill", parameters);
  assert.equal(invocation.executable, "python3");
  assert.deepEqual(invocation.args.slice(1), ["fill", "-"]);
  assert.equal(invocation.options.shell, undefined);
  assert.deepEqual(JSON.parse(input), parameters);
  await invokeHelper("list_pages", {});
  assert.deepEqual(invocation.args.slice(1), ["list_pages"]);
  assert.equal(input, undefined);
  assert.throws(() => invokeHelper("list_pages", { pageId: 2 }), /no parameters/);
});

test("helper errors, invalid results, and cancellation propagate without retries", async t => {
  let count = 0;
  const outputs = [
    [new Error("Failed"), "", "Normal Chrome: Denied"],
    [null, "not JSON", ""],
    [null, "[]", ""],
    [null, "{}", ""],
  ];
  t.mock.method(childProcess, "execFile", (_executable, _args, _options, callback) => {
    const stdin = new EventEmitter();
    stdin.end = () => { callback(...outputs[count++]); };
    return { stdin };
  });
  for (const error of [/Denied/, /invalid JSON/, /invalid tool result/, /invalid tool result/]) {
    await assert.rejects(invokeHelper("fill", { pageId: 2, uid: "1_3", value: "test" }), error);
  }
  assert.equal(count, 4);
  const controller = new AbortController();
  controller.abort();
  t.mock.restoreAll();
  await assert.rejects(invokeHelper("list_pages", {}, controller.signal), /aborted/i);
});

test("browser exec redirect does not affect other agents or unrelated shell tasks", () => {
  const hooks = {};
  register({ config, registerTool() {}, on: (name, handler) => { hooks[name] = handler; } });
  const event = { toolName: "exec", params: { command: "python3 normal_chrome.py fill broken" } };
  assert.equal(hooks.before_tool_call(event, { agentId: "esp32" }).block, true);
  assert.equal(hooks.before_tool_call(event, { agentId: "main" }), undefined);
  assert.equal(hooks.before_tool_call(
    { toolName: "exec", params: { command: "echo hello" } }, { agentId: "esp32" }), undefined);
  assert.ok(hooks.before_agent_start({}, { agentId: "esp32" }).prependContext.includes("normal_chrome"));
  assert.equal(hooks.before_agent_start({}, { agentId: "main" }), undefined);
});
