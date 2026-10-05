// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import { test } from "node:test";
import { Controller, boundedText } from "../dist/controller.js";

function fixture(result = { kind: "approve-once" }, customize, events) {
    const records = [], logs = [], notices = [];
    let controller;
    const transport = async (body) => {
        records.push(body);
        const override = await customize?.(body, controller);
        if (override) return override;
        if (body.action === "create") return { id: body.id, state: "pending" };
        if (body.action === "take") return { id: body.id, state: "answered", result };
        return {};
    };
    controller = new Controller(transport, "/safe/project", (value) => notices.push(value), (value) => logs.push(value), 1, events);
    return { controller, records, logs, notices };
}

test("recovery uses a fresh lease and closed controllers cannot reopen", async () => {
    let offline = false, disconnected = 0, reconnected = 0;
    const f = fixture(undefined, (body) => {
        if (offline && body.action === "heartbeat") throw new Error("Bridge restarted");
    }, { disconnected: async () => { disconnected++; }, stop: async () => {},
        reconnected: async () => { reconnected++; } });
    await f.controller.start();
    const old = f.controller.id;
    offline = true;
    await f.controller.checkConnection();
    assert.equal(disconnected, 1);
    assert.throws(() => f.controller.resume(), /disconnected/);
    offline = false;
    await f.controller.checkConnection();
    assert.notEqual(f.controller.id, old);
    assert.equal(reconnected, 1);
    f.controller.resume();
    await f.controller.close();
    const count = f.records.length;
    await f.controller.checkConnection();
    assert.equal(f.records.length, count);
    await assert.rejects(f.controller.start(), /closed/);
});

test("an old authorization cannot cross a reconnect, including cleanup ownership", async () => {
    let failHeartbeat = false, once = true;
    const f = fixture(undefined, async (body, controller) => {
        if (body.action === "heartbeat" && failHeartbeat) throw new Error("Lost lease");
        if (body.action === "take" && once) {
            once = false;
            failHeartbeat = true;
            await controller.checkConnection();
            failHeartbeat = false;
            await controller.checkConnection();
        }
    });
    await f.controller.start();
    const old = f.controller.id;
    try {
        assert.equal((await f.controller.permission({ kind: "shell" }, "old-session")).kind, "reject");
        assert.notEqual(f.controller.id, old);
        assert.ok(f.records.filter((body) => ["take", "cancel"].includes(body.action))
            .every((body) => body.controller === old));
        assert.equal((await f.controller.permission({ kind: "shell" }, "fresh-session")).kind, "approve-once");
    } finally { await f.controller.close(); }
});

test("task stop commands preserve exact IDs; malformed commands disconnect", async () => {
    const stopped = [];
    let commands = ["a".repeat(32), "b".repeat(32)], disconnected = 0;
    const f = fixture(undefined, (body) => body.action === "heartbeat" ? { stop_tasks: commands } : undefined,
        { disconnected: async () => { disconnected++; }, stop: async (id) => { stopped.push(id); } });
    await f.controller.start();
    try {
        await f.controller.checkConnection();
        assert.deepEqual(stopped, commands);
        commands = ["wrong"];
        await f.controller.checkConnection();
        assert.equal(disconnected, 1);
        assert.equal(stopped.length, 2);
    } finally { await f.controller.close(); }
});

test("task registration and progress require exact acknowledgment and UTF-8 bounds", async () => {
    let wrong = false;
    const f = fixture(undefined, (body) => {
        if (body.action === "task_begin") return { id: body.id, status: "working" };
        if (body.action === "task_update") return { id: wrong ? "bad" : body.id, status: "stopping" };
    });
    await f.controller.start();
    try {
        const id = await f.controller.beginTask("session", "\u{1f680}".repeat(500));
        await f.controller.updateTask(id, "working", "\u{1f680}".repeat(1000));
        for (const body of f.records) {
            if (body.title) assert.ok(Buffer.byteLength(body.title) <= 240 && !body.title.includes("\ufffd"));
            if (body.summary) assert.ok(Buffer.byteLength(body.summary) <= 2047 && !body.summary.includes("\ufffd"));
        }
        wrong = true;
        await assert.rejects(f.controller.updateTask(id, "completed", "Done"), /acknowledged/);
        assert.equal(boundedText("test\u0000\u202e\nok", 20), "test  \nok");
    } finally { await f.controller.close(); }
});

test("closing during SDK recovery never restores an accepting controller", async () => {
    let fail = true;
    const f = fixture(undefined, (body) => {
        if (body.action === "heartbeat" && fail) throw new Error("Offline");
    }, { disconnected: async () => {}, stop: async () => {},
        reconnected: async () => { await f.controller.close(); } });
    await f.controller.start();
    await f.controller.checkConnection();
    fail = false;
    await f.controller.checkConnection();
    assert.throws(() => f.controller.resume(), /disconnected/);
    assert.equal(f.records.at(-1).action, "close");
});

test("permission callback returns only SDK approve-once, with complete scope", async () => {
    const f = fixture();
    await f.controller.start();
    try {
        const result = await f.controller.permission({ kind: "shell", fullCommandText: "printf harmless" }, "session-one");
        assert.deepEqual(result, { kind: "approve-once" });
        const created = f.records.find((record) => record.action === "create");
        assert.match(created.body, /Workspace: \/safe\/project\nSession: session-one/);
        assert.ok(created.body.indexOf("printf harmless") < created.body.indexOf("Workspace:"));
        assert.match(created.body, /printf harmless/);
        assert.equal(created.respondable, true);
        assert.equal(f.notices.length, 1);
        assert.equal(f.controller.pending.size, 0);
    } finally { await f.controller.close(); }
});

for (const result of [{ kind: "approve-all" }, { kind: "approve-once", extra: true }, {}, { kind: "reject" }]) {
    test(`invalid decision ${JSON.stringify(result)} fails closed`, async () => {
        const f = fixture(result);
        await f.controller.start();
        try {
            assert.equal((await f.controller.permission({ kind: "shell" }, "session")).kind, "reject");
            assert.match(f.logs.join("\n"), /denied/);
        } finally { await f.controller.close(); }
    });
}

test("a bridge failure denies and cancels the request rather than leaving a ghost approval", async () => {
    const f = fixture(undefined, (body) => { if (body.action === "take") throw new Error("Disconnected"); });
    await f.controller.start();
    try {
        assert.equal((await f.controller.permission({ kind: "shell" }, "session")).kind, "reject");
        assert.ok(f.records.some((body) => body.action === "cancel"));
        assert.match(f.logs.join("\n"), /Disconnected/);
    } finally { await f.controller.close(); }
});

test("losing the controller lease after take but before delivery still rejects", async () => {
    const f = fixture(undefined, (body) => {
        if (body.action === "cancel") throw new Error("Controller expired after bridge restart");
    });
    await f.controller.start();
    try {
        assert.equal((await f.controller.permission({ kind: "shell" }, "session")).kind, "reject");
    } finally { await f.controller.close(); }
});

for (const state of ["expired", "cancelled", "interrupted", "consumed"]) {
    test(`${state} request never approves`, async () => {
        const f = fixture(undefined, (body) => body.action === "take" ? { id: body.id, state } : undefined);
        await f.controller.start();
        try {
            assert.equal((await f.controller.permission({ kind: "shell" }, "session")).kind, "reject");
        } finally { await f.controller.close(); }
    });
}

test("wrong response ID fails closed", async () => {
    const f = fixture(undefined, (body) => body.action === "take" ?
        { id: "wrong", state: "answered", result: { kind: "approve-once" } } : undefined);
    await f.controller.start();
    try {
        assert.equal((await f.controller.permission({ kind: "shell" }, "session")).kind, "reject");
    } finally { await f.controller.close(); }
});

test("stopping after take but before callback delivery still rejects approval", async () => {
    let stop = true;
    const f = fixture(undefined, async (body, controller) => {
        if (body.action === "cancel" && stop) {
            stop = false;
            await controller.cancel();
        }
    });
    await f.controller.start();
    try {
        assert.equal((await f.controller.permission({ kind: "shell" }, "session")).kind, "reject");
        assert.match(f.logs.join("\n"), /stopped before authorization/);
    } finally { await f.controller.close(); }
});

test("question callback preserves exact choice and wasFreeform", async () => {
    const f = fixture({ answer: "Detailed", wasFreeform: false });
    await f.controller.start();
    try {
        assert.deepEqual(await f.controller.question({ question: "Which format?", choices: ["Short", "Detailed"], allowFreeform: false }, "session"),
            { answer: "Detailed", wasFreeform: false });
        assert.match(f.records.find((body) => body.action === "create").body, /1. Short\n2. Detailed/);
        const body = f.records.find((body) => body.action === "create").body;
        assert.ok(body.indexOf("Which format?") < body.indexOf("Workspace:"));
    } finally { await f.controller.close(); }
});

test("freeform question callback returns human answer without an agent decision", async () => {
    const f = fixture({ answer: "Keep the current theme", wasFreeform: true });
    await f.controller.start();
    try {
        assert.deepEqual(await f.controller.question({ question: "Your preference?" }, "session"),
            { answer: "Keep the current theme", wasFreeform: true });
    } finally { await f.controller.close(); }
});

test("fabricated choice cannot answer the SDK's question", async () => {
    const f = fixture({ answer: "Invented", wasFreeform: false });
    await f.controller.start();
    try {
        await assert.rejects(f.controller.question({ question: "Which?", choices: ["Short"], allowFreeform: false }, "session"), /Invalid answer/);
    } finally { await f.controller.close(); }
});

test("large or Unicode permissions are opaque, never truncated reviewable work", async () => {
    for (const fullCommandText of ["x".repeat(3000), "echo \u202e"]) {
        const f = fixture({ kind: "reject", feedback: "Denied" });
        await f.controller.start();
        try {
            await f.controller.permission({ kind: "shell", fullCommandText }, "session");
            const created = f.records.find((body) => body.action === "create");
            assert.equal(created.respondable, false);
            assert.match(created.body, /Review on the computer/);
            assert.ok(!created.body.includes(fullCommandText));
            assert.ok(f.notices[0].details.includes(fullCommandText));
        } finally { await f.controller.close(); }
    }
});

test("desktop choices survive an opaque gadget preview", async () => {
    const choices = Array.from({ length: 20 }, (_, i) => `Option ${i}`);
    const f = fixture({ answer: choices[19], wasFreeform: false });
    await f.controller.start();
    try {
        await f.controller.question({ question: "Which?", choices, allowFreeform: false }, "session");
        const created = f.records.find((body) => body.action === "create");
        assert.equal(created.respondable, false);
        assert.deepEqual(created.choices, choices);
    } finally { await f.controller.close(); }
});
