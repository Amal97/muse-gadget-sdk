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

export class Controller {
    readonly id = randomBytes(16).toString("hex");
    readonly pending = new Map<string, PendingRequest>();
    private generation = 0;
    private alive = false;
    private accepting = true;
    private heartbeat?: NodeJS.Timeout;
    private heartbeating = false;

    constructor(
        private readonly transport: Transport,
        readonly workspace: string,
        private readonly notify: (request: PendingRequest) => void,
        private readonly log: (message: string) => void,
        private readonly pollMs = 1500,
    ) {}

    async start(): Promise<void> {
        await this.transport({ action: "open", controller: this.id, workspace: this.workspace });
        this.alive = true;
        this.heartbeat = setInterval(() => {
            if (this.heartbeating || !this.alive) return;
            this.heartbeating = true;
            void this.transport({ action: "heartbeat", controller: this.id })
                .catch((error: unknown) => {
                    this.alive = false;
                    this.generation++;
                    this.log(`Copilot watch disconnected; pending requests will not be approved: ${String(error)}`);
                })
                .finally(() => { this.heartbeating = false; });
        }, 5000);
        this.heartbeat.unref();
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
        if (!this.alive) throw new Error("Copilot watch is disconnected. Restart this controller.");
        this.accepting = true;
    }

    async close(): Promise<void> {
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
        let received = false;
        try {
            const created = await this.transport({
                action: "create", controller: this.id, id: request.id, session: request.session,
                kind: request.kind, body: request.respondable ? request.details :
                    `Copilot ${request.kind}\nReview on the computer: this request cannot be fully displayed on the gadget.\nSession: ${request.session}`,
                choices: request.choices,
                allow_freeform: request.freeform, respondable: request.respondable,
            });
            if (created.id !== request.id || created.state !== "pending") throw new Error("Copilot request was not registered.");
            this.notify(request);
            const deadline = Date.now() + 600_000;
            while (this.alive && this.accepting && this.generation === request.generation && Date.now() < deadline) {
                const value = await this.transport({ action: "take", controller: this.id, id: request.id });
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
                await this.transport({ action: "cancel", controller: this.id, id: request.id });
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
