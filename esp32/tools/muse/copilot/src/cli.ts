// SPDX-License-Identifier: Apache-2.0
import { parseArgs } from "node:util";
import { realpath } from "node:fs/promises";
import { createInterface, type Interface } from "node:readline";
import { CopilotClient } from "@github/copilot-sdk";
import { bridgeTransport } from "./bridge.js";
import { Controller } from "./controller.js";

const { values } = parseArgs({ options: {
    cwd: { type: "string", default: process.cwd() },
    python: { type: "string", default: "python3" },
    state: { type: "string" },
    model: { type: "string", default: "auto" },
} });
const workspace = await realpath(values.cwd);
const terminalText = (value: string) => value.replace(/[\p{Cc}\p{Cf}]/gu, (character) => character === "\n" ? character :
    character.split("").map((unit) => `\\u${unit.charCodeAt(0).toString(16).padStart(4, "0")}`).join(""));
const log = (message: string) => console.error(terminalText(message));
const controller = new Controller(bridgeTransport(values.python, values.state), workspace, (request) => {
    console.log(`\nCOPILOT WAITING - ${request.kind} - ${request.id}\n${terminalText(request.details)}`);
    console.log(request.kind === "permission" ?
        `/approve ${request.id}  OR  /deny ${request.id}` : `/answer ${request.id} YOUR ANSWER  OR  /deny ${request.id}`);
    console.log(request.respondable ? "Or review the gadget and hold Talk to respond." : "This request must be reviewed on this computer.");
}, log);
const client = new CopilotClient();
let input: Interface | undefined;
let busy = false;
let shuttingDown = false;

async function shutdown(): Promise<void> {
    if (shuttingDown) return;
    shuttingDown = true;
    input?.close();
    await controller.close();
    const errors = await client.stop();
    if (errors.length) {
        log(`Copilot shutdown reported ${errors.length} error(s).`);
        process.exitCode = 1;
    }
}

try {
    await controller.start();
    await client.start();
    const auth = await client.getAuthStatus();
    if (!auth.isAuthenticated) throw new Error("Copilot CLI is not authenticated. Complete Copilot CLI login, then restart (see setup documentation).");
    const session = await client.createSession({
        model: values.model,
        workingDirectory: workspace,
        askUserVariant: "legacy",
        onPermissionRequest: (request, invocation) => controller.permission(request, invocation.sessionId),
        onUserInputRequest: (request, invocation) => controller.question(request, invocation.sessionId),
    });
    session.on("assistant.message", (event) => console.log(`\nCopilot: ${terminalText(event.data.content)}`));
    session.on("session.idle", () => { busy = false; });
    session.on("session.error", (event) => {
        busy = false;
        log(`Copilot session error: ${event.data.message}`);
        void controller.cancel().catch((error: unknown) => log(`Cancellation failed: ${String(error)}`));
    });
    console.log(`Gadget Copilot ready in ${terminalText(workspace)}\nSession: ${session.sessionId}`);
    console.log("Enter a task. /stop cancels work; /quit closes the session. Approvals are once-only, never blanket permissions.");

    async function line(raw: string): Promise<void> {
        const value = raw.trim();
        if (!value || shuttingDown) return;
        if (value === "/quit") { await shutdown(); return; }
        if (value === "/stop") {
            await controller.cancel();
            await session.abort();
            log("Copilot work stopped. Pending gadget responses cannot authorize it.");
            return;
        }
        const match = /^\/(approve|deny|answer) ([0-9a-f]{32})(?: (.+))?$/.exec(value);
        if (match) {
            const [, action, id, answer] = match;
            if (!id) throw new Error("Missing request ID.");
            if (action === "deny" && controller.pending.get(id)?.kind === "question") {
                await bridgeTransport(values.python, values.state)({ action: "cancel", controller: controller.id, id });
            } else {
                await controller.answer(id, action === "answer" ? answer ?? "" : action ?? "");
            }
            log("Response submitted; awaiting the owning Copilot callback.");
            return;
        }
        if (value.startsWith("/")) throw new Error("Use /stop, /quit, or the exact response command shown with the request ID.");
        if (busy) throw new Error("Copilot is busy. Respond to its pending request or use /stop first.");
        controller.resume();
        busy = true;
        try {
            await session.send({ prompt: value });
        } catch (error) {
            busy = false;
            await controller.cancel();
            throw error;
        }
    }
    input = createInterface({ input: process.stdin, output: process.stdout, terminal: process.stdin.isTTY });
    input.on("line", (value) => { void line(value).catch((error: unknown) => log(`Action failed: ${String(error)}`)); });
    input.on("close", () => { void shutdown().catch((error: unknown) => { log(`Shutdown failed: ${String(error)}`); process.exitCode = 1; }); });
    process.once("SIGTERM", () => { void shutdown().catch((error: unknown) => { log(`Shutdown failed: ${String(error)}`); process.exitCode = 1; }); });
    process.once("SIGINT", () => { void shutdown().catch((error: unknown) => { log(`Shutdown failed: ${String(error)}`); process.exitCode = 1; }); });
} catch (error) {
    log(`Gadget Copilot failed: ${String(error)}`);
    process.exitCode = 1;
    await shutdown();
}
