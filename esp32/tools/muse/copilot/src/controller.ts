// SPDX-License-Identifier: Apache-2.0
import { randomBytes } from "node:crypto";
import { basename } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import type { PermissionRequest, PermissionRequestResult, SessionConfig } from "@github/copilot-sdk";
import { object, type ObjectValue, type Transport } from "./bridge.js";

type InputHandler = NonNullable<SessionConfig["onUserInputRequest"]>;
type UserInputRequest = Parameters<InputHandler>[0];
type UserInputResponse = Awaited<ReturnType<InputHandler>>;

export interface PendingRequest {
    id: string;
    kind: "permission" | "question";
    session: string;
    details: string;
    choices: string[];
    freeform: boolean;
    respondable: boolean;
    generation: number;
}

function displayable(value: string): boolean {
    return /^[\x20-\x7e\n]+$/.test(value) && Buffer.byteLength(value, "utf8") <= 2047;
}

export function boundedText(value: string, bytes: number): string {
    let result = value.replace(/[\p{Cc}\p{Cf}]/gu, (c) => c === "\n" ? c : " ").trim();
    if (Buffer.byteLength(result, "utf8") <= bytes) return result || "No additional details.";
    result = Buffer.from(result).subarray(0, bytes - 3).toString("utf8").replace(/\ufffd$/, "");
    return result + "...";
}

export class Controller {
    id = randomBytes(16).toString("hex");
    readonly pending = new Map<string, PendingRequest>();
    private generation = 0;
    private alive = false;
    private accepting = true;
    private heartbeat?: NodeJS.Timeout;
    private heartbeating = false;
    private closing = false;

    constructor(
        private readonly transport: Transport,
        readonly workspace: string,
        private readonly notify: (request: PendingRequest) => void,
        private readonly log: (message: string) => void,
        private readonly pollMs = 1500,
        private readonly events?: { disconnected: () => Promise<void>; stop: (id: string) => Promise<void>;
            reconnected?: () => Promise<void> },
    ) {}

    async start(): Promise<void> {
        if (this.closing) throw new Error("Copilot controller is closed.");
        await this.transport({ action: "open", controller: this.id, workspace: this.workspace });
        if (this.closing) {
            await this.transport({ action: "close", controller: this.id });
            throw new Error("Copilot controller closed during startup.");
        }
        this.alive = true;
        this.heartbeat = setInterval(() => {
            void this.checkConnection();
        }, 5000);
        this.heartbeat.unref();
    }

    async checkConnection(): Promise<void> {
        if (this.heartbeating || this.closing) return;
        this.heartbeating = true;
        const wasAlive = this.alive;
        try {
            if (!wasAlive) {
                const key = randomBytes(16).toString("hex");
                await this.transport({ action: "open", controller: key, workspace: this.workspace });
                if (this.closing) {
                    await this.transport({ action: "close", controller: key });
                    return;
                }
                this.id = key;
                try { await this.events?.reconnected?.(); }
                catch (error) {
                    await this.transport({ action: "close", controller: key });
                    throw error;
                }
                if (this.closing) {
                    await this.transport({ action: "close", controller: key });
                    return;
                }
                this.alive = true;
                this.accepting = true;
                this.log("Copilot watch reconnected with a fresh lease. Interrupted work and old approvals are not resumed.");
            } else {
                const response = await this.transport({ action: "heartbeat", controller: this.id });
                if (response.stop_tasks !== undefined) {
                    if (!Array.isArray(response.stop_tasks) || response.stop_tasks.some(
                        (id: unknown) => typeof id !== "string" || !/^[0-9a-f]{32}$/.test(id))) {
                        throw new Error("Invalid task stop commands from bridge.");
                    }
                    for (const id of response.stop_tasks) await this.events?.stop(id);
                }
            }
        } catch (error) {
            if (wasAlive && this.alive) {
                this.alive = false;
                this.accepting = false;
                this.generation++;
                this.log(`Copilot watch disconnected; pending requests will not be approved: ${String(error)}`);
                try { await this.events?.disconnected(); }
                catch (abortError) { this.log(`SDK interruption failed; stop work on this computer: ${String(abortError)}`); }
            } else this.log(`Copilot reconnect retry failed: ${String(error)}`);
        } finally { this.heartbeating = false; }
    }

    async beginTask(session: string, title: string): Promise<string> {
        this.resume();
        const id = randomBytes(16).toString("hex");
        const result = await this.transport({ action: "task_begin", controller: this.id, id, session,
            title: boundedText(title, 240) });
        if (result.id !== id || result.status !== "working") throw new Error("Copilot task was not registered.");
        return id;
    }

    async updateTask(id: string, status: string, summary: string): Promise<void> {
        const result = await this.transport({ action: "task_update", controller: this.id, id, status,
            summary: boundedText(summary, 2047) });
        if (result.id !== id || (result.status !== status && result.status !== "stopping")) {
            throw new Error("Copilot task update was not acknowledged.");
        }
    }

    async cancel(): Promise<void> {
        this.accepting = false;
        this.generation++;
        const results = await Promise.allSettled([...this.pending.values()].map(
            (request) => this.transport({ action: "cancel", controller: this.id, id: request.id })));
        for (const result of results) {
            if (result.status === "rejected") this.log(`Could not acknowledge Copilot cancellation: ${String(result.reason)}`);
        }
    }

    resume(): void {
        if (!this.alive) throw new Error("Copilot watch is disconnected. Wait for automatic recovery or restart this controller.");
        this.accepting = true;
    }

    async close(): Promise<void> {
        this.closing = true;
        if (this.heartbeat) clearInterval(this.heartbeat);
        await this.cancel();
        if (this.alive) {
            try {
                await this.transport({ action: "close", controller: this.id });
            } catch (error) {
                this.log(`Copilot controller close was not acknowledged; its lease will expire: ${String(error)}`);
            }
        }
        this.alive = false;
    }

    async answer(id: string, answer: string): Promise<void> {
        if (!this.pending.has(id)) throw new Error("Use the exact ID of a currently waiting request.");
        await this.transport({ action: "answer", controller: this.id, id, text: answer });
    }

    private async wait(request: PendingRequest): Promise<ObjectValue> {
        if (!this.alive || !this.accepting) throw new Error("Copilot controller is stopped or disconnected.");
        this.pending.set(request.id, request);
        const controller = this.id;
        let received = false;
        try {
            const created = await this.transport({
                action: "create", controller, id: request.id, session: request.session,
                kind: request.kind, body: request.respondable ? request.details :
                    `Copilot ${request.kind}\nReview on the computer: this request cannot be fully displayed on the gadget.\nSession: ${request.session}`,
                choices: request.choices,
                allow_freeform: request.freeform, respondable: request.respondable,
            });
            if (created.id !== request.id || created.state !== "pending") throw new Error("Copilot request was not registered.");
            this.notify(request);
            const deadline = Date.now() + 600_000;
            while (this.alive && this.accepting && this.generation === request.generation && Date.now() < deadline) {
                const value = await this.transport({ action: "take", controller, id: request.id });
                if (!this.alive || !this.accepting || this.generation !== request.generation) break;
                if (value.id !== request.id) throw new Error("Copilot response has the wrong request ID.");
                if (value.state === "answered" && object(value.result)) {
                    received = true;
                    return value.result;
                }
                if (value.state !== "pending") throw new Error(`Copilot request ${String(value.state)}; no response applied.`);
                await delay(this.pollMs);
            }
            throw new Error("Copilot request cancelled, expired or disconnected; no response applied.");
        } finally {
            this.pending.delete(request.id);
            try {
                await this.transport({ action: "cancel", controller, id: request.id });
            } catch (error) {
                this.log(`Copilot request cleanup was not acknowledged; its lease/timeout will close it: ${String(error)}`);
                if (received) throw new Error("Controller lease could not be verified before decision delivery.", { cause: error });
            }
        }
    }

    async permission(request: PermissionRequest, session: string): Promise<PermissionRequestResult> {
        const generation = this.generation;
        let command: unknown;
        if (object(request)) {
            command = request.fullCommandText;
            if (typeof command !== "string" && object(request.toolArgs)) command = request.toolArgs.command;
        }
        const details = `Project: ${basename(this.workspace)}\nAPPROVE ONCE OR DENY\n` +
            (typeof command === "string" ? `Command: ${JSON.stringify(command)}\n` : "") +
            `${JSON.stringify(request, null, 2)}\nWorkspace: ${this.workspace}\nSession: ${session}`;
        try {
            const result = await this.wait({
                id: randomBytes(16).toString("hex"), kind: "permission", session, details,
                choices: [], freeform: false, respondable: displayable(details), generation,
            });
            if (!this.alive || !this.accepting || this.generation !== generation) throw new Error("Copilot was stopped before authorization.");
            if (result.kind === "approve-once" && Object.keys(result).length === 1) return { kind: "approve-once" };
            if (result.kind === "reject" && typeof result.feedback === "string") {
                return { kind: "reject", feedback: result.feedback };
            }
            throw new Error("Invalid permission decision; approval rejected.");
        } catch (error) {
            this.log(`Copilot permission denied: ${String(error)}`);
            return { kind: "reject", feedback: "Gadget/desktop authorization unavailable, cancelled or invalid. Ask the user again." };
        }
    }

    async question(request: UserInputRequest, session: string): Promise<UserInputResponse> {
        const generation = this.generation;
        const choices = request.choices ?? [];
        const details = `Project: ${basename(this.workspace)}\n${request.question}` +
            choices.map((choice, index) => `\n${index + 1}. ${choice}`).join("") +
            (request.allowFreeform !== false ? "\nFreeform answers allowed." : "\nChoose an offered option.") +
            `\nWorkspace: ${this.workspace}\nSession: ${session}`;
        const respondable = displayable(details) && choices.length <= 12 &&
            choices.every((choice) => Buffer.byteLength(choice) <= 240 && displayable(choice)) &&
            new Set(choices.map((choice) => choice.trim().toLowerCase().replace(/[.!]+$/, ""))).size === choices.length;
        const result = await this.wait({
            id: randomBytes(16).toString("hex"), kind: "question", session, details, choices,
            freeform: request.allowFreeform !== false, respondable, generation,
        });
        if (!this.alive || !this.accepting || this.generation !== generation) throw new Error("Copilot was stopped before answering.");
        if (typeof result.answer !== "string" || !result.answer.trim() ||
            Buffer.byteLength(result.answer) > 2047 || typeof result.wasFreeform !== "boolean" ||
            (result.wasFreeform ? request.allowFreeform === false : !choices.includes(result.answer))) {
            throw new Error("Invalid answer for this exact Copilot question.");
        }
        return { answer: result.answer, wasFreeform: result.wasFreeform };
    }
}
