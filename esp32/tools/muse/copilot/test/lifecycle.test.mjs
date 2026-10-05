// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import { test } from "node:test";
import { stopClient } from "../dist/lifecycle.js";

test("normal SDK shutdown does not force-stop", async () => {
    let forced = 0;
    const logs = [];
    assert.equal(await stopClient({ stop: async () => [], forceStop: async () => { forced++; } },
        (value) => logs.push(value), 50), true);
    assert.equal(forced, 0);
    assert.deepEqual(logs, []);
});

test("hung SDK shutdown is bounded, forced and reported as unsuccessful", async () => {
    let forced = 0;
    const logs = [];
    assert.equal(await stopClient({ stop: () => new Promise(() => {}), forceStop: async () => { forced++; } },
        (value) => logs.push(value), 5), false);
    assert.equal(forced, 1);
    assert.match(logs.join("\n"), /timed out/);
    assert.match(logs.join("\n"), /does not undo/);
});

test("SDK stop errors are explicit and force-stop failure propagates", async () => {
    const logs = [];
    assert.equal(await stopClient({ stop: async () => [new Error("detach failed")], forceStop: async () => {} },
        (value) => logs.push(value), 50), false);
    assert.match(logs.join("\n"), /detach failed/);
    await assert.rejects(stopClient({ stop: async () => { throw new Error("transport failed"); },
        forceStop: async () => { throw new Error("force-stop failed"); } }, () => {}, 50), /force-stop failed/);
});
