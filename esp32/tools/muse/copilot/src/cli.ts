// SPDX-License-Identifier: Apache-2.0
import { parseArgs } from "node:util";
import { realpath } from "node:fs/promises";
import { createInterface, type Interface } from "node:readline";
import { CopilotClient } from "@github/copilot-sdk";
import { bridgeTransport } from "./bridge.js";
import { Controller } from "./controller.js";
import { stopClient } from "./lifecycle.js";
import type { CopilotSession } from "@github/copilot-sdk";

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
}, log, 1500, {
    disconnected: async () => {
        const active = busy;
        const previous = session;
        session = undefined;
        taskId = "";
        if (previous && active) await previous.abort();
        if (previous) await previous.disconnect();
        busy = false;
    },
    reconnected: async () => {
        if (shuttingDown) throw new Error("Controller is closing.");
        await createSdkSession();
    },
    stop: async (id) => {
        if (!session || id !== taskId || stopped) return;
        stopped = true;
        await controller.cancel();
        await session.abort();
        await finishTask("cancelled", "Stopped by the user; SDK abort acknowledged.");
    },
});
const client = new CopilotClient();
let input: Interface | undefined;
let busy = false;
let shuttingDown = false;
let session: CopilotSession | undefined;
let taskId = "", taskSummary = "";
let stopped = false, finishing = false;
let updates: Promise<void> = Promise.resolve();
let progressAt = 0;

function progress(summary: string): void {
    const id = taskId;
    if (!id || finishing || Date.now() - progressAt < 1000) return;
    progressAt = Date.now();
    updates = updates.then(() => controller.updateTask(id, "working", summary))
        .catch((error: unknown) => log(`Task progress unavailable: ${String(error)}`));
}

async function finishTask(status: string, summary: string): Promise<void> {
    const id = taskId;
    if (!id || finishing) return;
    finishing = true;
    try {
        await updates;
        await controller.updateTask(id, status, summary);
    } catch (error) { log(`Task result could not be published: ${String(error)}`); }
    finally { if (taskId === id) { taskId = ""; busy = false; } }
}

let sdkReady = false;
let sessionCreation: Promise<void> | undefined;
function createSdkSession(): Promise<void> {
    if (!sdkReady) return Promise.reject(new Error("SDK startup is not finished; retrying recovery."));
    sessionCreation ??= buildSdkSession().finally(() => { sessionCreation = undefined; });
    return sessionCreation;
}

async function buildSdkSession(): Promise<void> {
    const owned = await client.createSession({
        model: values.model,
        workingDirectory: workspace,
        askUserVariant: "legacy",
        onPermissionRequest: (request, invocation) => {
            if (invocation.sessionId !== session?.sessionId || shuttingDown) {
                return { kind: "reject", feedback: "This SDK session is no longer active. Ask in the new session." };
            }
            return controller.permission(request, invocation.sessionId);
        },
        onUserInputRequest: (request, invocation) => {
            if (invocation.sessionId !== session?.sessionId || shuttingDown) throw new Error("Question belongs to an inactive SDK session.");
            return controller.question(request, invocation.sessionId);
        },
    });
    if (shuttingDown) {
        await owned.disconnect();
        throw new Error("Controller closed during SDK-session creation.");
    }
    owned.on("assistant.message", (event) => {
        if (session !== owned) return;
        taskSummary = event.data.content;
        console.log(`\nCopilot: ${terminalText(event.data.content)}`);
    });
    owned.on("tool.execution_start", (event) => {
        if (session === owned) progress(`Running ${event.data.toolName}`);
    });
    owned.on("session.idle", () => {
        if (session !== owned) return;
        void finishTask(stopped ? "cancelled" : "completed",
            stopped ? "SDK work stopped." : taskSummary || "Copilot finished; review the terminal for details.");
    });
    owned.on("session.error", (event) => {
        if (session !== owned) return;
        void finishTask("failed", event.data.message);
        log(`Copilot session error: ${event.data.message}`);
        void controller.cancel().catch((error: unknown) => log(`Cancellation failed: ${String(error)}`));
    });
    session = owned;
    console.log(`SDK session: ${owned.sessionId}`);
}

async function shutdown(): Promise<void> {
    if (shuttingDown) return;
    shuttingDown = true;
    input?.close();
    process.stdin.destroy();
    await controller.close();
    if (!await stopClient(client, log)) process.exitCode = 1;
    else log("Gadget Copilot closed.");
}

try {
    await controller.start();
    await client.start();
    const auth = await client.getAuthStatus();
    if (!auth.isAuthenticated) throw new Error("Copilot CLI is not authenticated. Complete Copilot CLI login, then restart (see setup documentation).");
    sdkReady = true;
    await createSdkSession();
    console.log(`Gadget Copilot ready in ${terminalText(workspace)}`);
    console.log("Enter a task. /stop cancels work; /quit closes the session. Approvals are once-only, never blanket permissions.");

    async function line(raw: string): Promise<void> {
        const value = raw.trim();
        if (!value || shuttingDown || !session) return;
        if (value === "/quit") { await shutdown(); return; }
        if (value === "/stop") {
            stopped = true;
            await controller.cancel();
            await session.abort();
            await finishTask("cancelled", "Stopped from the desktop; SDK abort acknowledged.");
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
        busy = true;
        stopped = finishing = false;
        taskSummary = "";
        try {
            taskId = await controller.beginTask(session.sessionId, value);
            await session.send({ prompt: value });
        } catch (error) {
            if (taskId) await finishTask("failed", String(error));
            else busy = false;
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
