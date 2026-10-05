// SPDX-License-Identifier: Apache-2.0
import type { CopilotClient } from "@github/copilot-sdk";

export async function stopClient(
    client: Pick<CopilotClient, "stop" | "forceStop">,
    log: (message: string) => void,
    timeoutMs = 20_000,
): Promise<boolean> {
    let timer: NodeJS.Timeout | undefined;
    let clean = false;
    try {
        const errors = await Promise.race([
            client.stop(),
            new Promise<never>((_, reject) => {
                timer = setTimeout(() => reject(new Error("SDK shutdown timed out.")), timeoutMs);
            }),
        ]);
        for (const error of errors) log(`SDK shutdown error: ${error.message}`);
        clean = errors.length === 0;
    } catch (error) {
        log(`Graceful SDK shutdown failed: ${String(error)}`);
    } finally {
        if (timer) clearTimeout(timer);
    }
    if (!clean) {
        await client.forceStop();
        log("SDK transport forcibly stopped. Review previously approved work on the computer; shutdown does not undo it.");
    }
    return clean;
}
