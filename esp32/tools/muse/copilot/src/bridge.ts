// SPDX-License-Identifier: Apache-2.0
import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";

export type ObjectValue = Record<string, unknown>;
export type Transport = (body: ObjectValue) => Promise<ObjectValue>;

export function object(value: unknown): value is ObjectValue {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function bridgeTransport(python: string, state?: string): Transport {
    const helper = fileURLToPath(new URL("../../companion_cli.py", import.meta.url));
    let serial: Promise<void> = Promise.resolve();
    return (body) => {
        const run = () => new Promise<ObjectValue>((resolve, reject) => {
            const args = [helper, "--copilot-controller", ...(state ? ["--state", state] : [])];
            const child = execFile(python, args, { timeout: 5000, maxBuffer: 32768 }, (error, stdout, stderr) => {
                if (error) {
                    reject(new Error(stderr.trim() || "Copilot bridge unavailable or timed out.", { cause: error }));
                    return;
                }
                try {
                    const value: unknown = JSON.parse(stdout);
                    if (!object(value)) throw new Error("Invalid Copilot bridge response.");
                    resolve(value);
                } catch (error) {
                    reject(error);
                }
            });
            child.stdin?.end(JSON.stringify(body));
        });
        const result = serial.then(run);
        serial = result.then(() => undefined, () => undefined);
        return result;
    };
}
