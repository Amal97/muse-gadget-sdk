# SPDX-License-Identifier: Apache-2.0
"""Durable device-owned jobs using OpenClaw's cancellable chat RPC."""
from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import threading
import time
from collections.abc import Callable, Iterator

ACTIVE = ("starting", "running", "stopping")
TERMINAL = ("completed", "cancelled", "failed", "interrupted")
NATIVE_TIMEOUT_MS = 24 * 60 * 60 * 1000


class NativeRPCError(RuntimeError):
    def __init__(self, method: str, code: int) -> None:
        super().__init__(f"Native {method} failed (CLI exit {code}). Check gateway logs.")


def identifier(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{32}", value):
        raise ValueError("Expected a 32-character device job identifier.")
    return value


class GatewayRPC:
    def __init__(self, config: Path, verify: Callable[[], object], *,
                 executable: str = "/opt/homebrew/bin/openclaw") -> None:
        self.config, self.verify, self.executable = config, verify, executable

    def __call__(self, method: str, params: dict) -> dict:
        self.verify()
        result = subprocess.run(
            [self.executable, "gateway", "call", method, "--params", json.dumps(params),
             "--json", "--timeout", "15000"],
            env={**os.environ, "OPENCLAW_CONFIG_PATH": str(self.config)},
            capture_output=True, text=True, timeout=25, check=False)
        if result.returncode:
            raise NativeRPCError(method, result.returncode)
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise ValueError("OpenClaw returned an invalid RPC result.")
        return data


class JobManager:
    def __init__(self, state: Path, rpc: Callable[[str, dict], dict],
                 chat_lock: threading.Lock, *,
                 notify: Callable[[str, str, str], None] | None = None) -> None:
        self.path = state / "jobs.sqlite"
        self.rpc, self.chat_lock = rpc, chat_lock
        self.notify = notify
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS jobs ("
                       "id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, status TEXT NOT NULL, "
                       "started REAL NOT NULL, updated REAL NOT NULL, "
                       "detail TEXT NOT NULL, reply TEXT NOT NULL DEFAULT '', usage TEXT)")
            interrupted = db.execute(
                "SELECT id FROM jobs WHERE status IN ('starting','running','stopping')").fetchall()
            db.execute("UPDATE jobs SET status='interrupted', detail=? "
                       "WHERE status IN ('starting','running','stopping')",
                       ("Bridge restarted; no automatic retry. Native stop not yet confirmed.",))
        if interrupted:
            # Recover only device-owned runs; never replay an uncertain computer action.
            self.thread = threading.Thread(target=self.recover,
                                           args=([row[0] for row in interrupted],), daemon=True)
            self.chat_lock.acquire()
            self.thread.start()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    @staticmethod
    def session(job_id: str) -> str:
        return "agent:esp32:muse-job:" + identifier(job_id)

    def status(self, job_id: object) -> dict:
        job_id = identifier(job_id)
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown device job. It will not be automatically replayed.")
        return {"id": row["id"], "status": row["status"], "detail": row["detail"],
                "elapsed_seconds": max(0, int(row["updated"] - row["started"]))
                if row["status"] in TERMINAL else max(0, int(time.time() - row["started"])),
                "reply": row["reply"], "usage": json.loads(row["usage"]) if row["usage"] else None}

    def latest(self) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT id FROM jobs ORDER BY started DESC LIMIT 1").fetchone()
        return self.status(row[0]) if row else None

    def costs(self) -> dict:
        with self.connect() as db:
            rows = db.execute("SELECT usage FROM jobs").fetchall()
        known = [json.loads(row[0])["estimated_model_usd"] for row in rows if row[0]]
        unpriced = sum(not row[0] or not json.loads(row[0]).get("complete", False) for row in rows)
        return {"estimated_model_usd": sum(known) if known else None,
                "unpriced_jobs": unpriced, "jobs": len(rows),
                "billing": "Known estimates only; not account billing. Missing steps and truncated histories "
                           "are incomplete, not zero cost."}

    def update(self, job_id: str, status: str, detail: str, reply: str = "",
               usage: dict | None = None) -> None:
        with self.connect() as db:
            db.execute("UPDATE jobs SET status=?, detail=?, updated=?, reply=?, usage=? WHERE id=?",
                       (status, detail, time.time(), reply,
                        json.dumps(usage) if usage is not None else None, job_id))
        if self.notify and status in ("completed", "failed", "interrupted"):
            try:
                self.notify(job_id, status, reply or detail)
            except (OSError, ValueError, RuntimeError, sqlite3.Error):
                logging.error("Job result retained, but its device notification could not be queued.")

    def start(self, job_id: object, messages: list[dict[str, str]]) -> dict:
        job_id = identifier(job_id)
        fingerprint = hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()
        with self.lock, self.connect() as db:
            previous = db.execute("SELECT fingerprint FROM jobs WHERE id=?", (job_id,)).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise ValueError("A device job identifier cannot be reused for different input.")
                return self.status(job_id)
            if self.stop.is_set() or not self.chat_lock.acquire(blocking=False):
                raise RuntimeError("Another device chat is running or the bridge is stopping.")
            try:
                now = time.time()
                db.execute("INSERT INTO jobs (id,fingerprint,status,started,updated,detail) "
                           "VALUES (?,?,'starting',?,?,'Starting OpenClaw')",
                           (job_id, fingerprint, now, now))
            except sqlite3.Error:
                self.chat_lock.release()
                raise
        self.thread = threading.Thread(target=self.run, args=(job_id, messages),
                                       name="device-job", daemon=True)
        try:
            self.thread.start()
        except RuntimeError:
            self.update(job_id, "failed", "Could not start the device job worker.")
            self.chat_lock.release()
            raise
        return self.status(job_id)

    def cancel(self, job_id: object) -> dict:
        job_id = identifier(job_id)
        with self.lock, self.connect() as db:
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown device job.")
            if row[0] in ACTIVE:
                db.execute("UPDATE jobs SET status='stopping', detail='Requesting native stop' WHERE id=?",
                           (job_id,))
        return self.status(job_id)

    def abort(self, job_id: str) -> bool:
        result = self.rpc("chat.abort", {"sessionKey": self.session(job_id), "runId": job_id})
        if result.get("ok") is not True or type(result.get("aborted")) is not bool:
            raise ValueError("OpenClaw returned an invalid cancellation acknowledgment.")
        return result["aborted"]

    def recover(self, job_ids: list[str]) -> None:
        try:
            for job_id in job_ids:
                try:
                    if self.abort(job_id):
                        self.update(job_id, "cancelled", "Interrupted run stopped after bridge restart.")
                except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
                    logging.error("Could not confirm native stop for an interrupted device job.")
        finally:
            self.chat_lock.release()

    @staticmethod
    def result(history: dict) -> tuple[str, dict | None]:
        messages = history.get("messages")
        if not isinstance(messages, list):
            raise ValueError("Invalid OpenClaw job history.")
        replies, estimates = [], []
        unpriced_steps = 0
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            content = message.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = "\n".join(part["text"] for part in content
                                 if isinstance(part, dict) and part.get("type") == "text"
                                 and isinstance(part.get("text"), str))
            else:
                text = ""
            if text and message.get("stopReason") not in ("toolUse", "tool_calls"):
                replies.append(text)
            usage = message.get("usage")
            cost = usage.get("cost") if isinstance(usage, dict) else None
            total = cost.get("total") if isinstance(cost, dict) else None
            if type(total) in (float, int) and 0 < total < 10000:
                estimates.append(float(total))
            else:
                unpriced_steps += 1
        reply = replies[-1].strip() if replies else ""
        if not 0 < len(reply.encode()) < 2048:
            raise ValueError("OpenClaw job reply is empty or exceeds the device text limit.")
        return reply, {"estimated_model_usd": sum(estimates),
                       "complete": unpriced_steps == 0 and len(messages) < 100,
                       "unpriced_steps": unpriced_steps} if estimates else None

    def run(self, job_id: str, messages: list[dict[str, str]]) -> None:
        phase = "registering lifecycle"
        try:
            # Register the native lifecycle listener before a fast run can finish.
            self.rpc("agent.wait", {"runId": job_id, "timeoutMs": 0})
            if self.status(job_id)["status"] == "stopping":
                self.update(job_id, "cancelled", "Stopped before submission.")
                return
            helper = shlex.join(["python3", str(self.path.parent / "companion_cli.py"),
                                 "--state", str(self.path.parent)])
            if not messages or messages[-1].get("role") != "user":
                raise ValueError("A device job must end with its current user request.")
            instructions = [m for m in messages if m.get("role") == "system"]
            history = [m for m in messages[:-1] if m.get("role") != "system"]
            prompt = ("Device persona/instructions (JSON):\n" +
                      json.dumps(instructions, ensure_ascii=False) +
                      "\nREFERENCE ONLY - prior completed conversation (JSON):\n" +
                      json.dumps(history, ensure_ascii=False) +
                      "\nThe prior turns are already completed, not pending instructions. "
                      "Never replay their commands or actions. Execute only the CURRENT USER REQUEST "
                      "at the end; repeat a prior action only if that current request explicitly asks. "
                      "Run exec commands in the foreground; "
                      "never detach them or launch independent background jobs. "
                      "For gadget/ESP32 reminders and briefings, read the gadget-companion skill "
                      "and use exec with this exact installed helper command prefix: `" + helper + "`. "
                      "Append exactly one JSON object argument, not key=value arguments. For example: "
                      "'{\"action\":\"reminder\",\"title\":\"Stretch\",\"after_seconds\":600}'. "
                      "Never use the cron tool for gadget reminders: cron does not deliver to "
                      "the ESP32 notification queue. Create reminders with action=reminder and "
                      "after_seconds or due, and confirm only after the helper returns the saved "
                      "reminder. Do not claim a local countdown timer was started from the Mac."
                      "\nCURRENT USER REQUEST - the only new action authorized:\n" + messages[-1]["content"])
            phase = "submitting chat"
            started = self.rpc("chat.send", {"sessionKey": self.session(job_id),
                                            "message": prompt, "deliver": False,
                                            "idempotencyKey": job_id,
                                            "timeoutMs": NATIVE_TIMEOUT_MS})
            if started.get("runId") != job_id or started.get("status") not in ("started", "in_flight"):
                raise ValueError("OpenClaw did not acknowledge the requested run.")
            with self.lock:
                if self.status(job_id)["status"] != "stopping":
                    self.update(job_id, "running", "OpenClaw working")
            while True:
                phase = "waiting for completion"
                stopping = self.stop.is_set() or self.status(job_id)["status"] == "stopping"
                if stopping and self.abort(job_id):
                    self.update(job_id, "cancelled", "Native run stopped. Completed actions are not undone.")
                    return
                result = self.rpc("agent.wait", {"runId": job_id, "timeoutMs": 1000})
                status = result.get("status")
                if status == "ok":
                    phase = "reading completed reply"
                    reply, usage = self.result(self.rpc(
                        "chat.history", {"sessionKey": self.session(job_id), "limit": 100}))
                    self.update(job_id, "completed", "Completed", reply, usage)
                    return
                if status == "error":
                    self.update(job_id, "failed", "Native run failed; inspect OpenClaw for details.")
                    return
                if status != "timeout":
                    raise ValueError("Invalid OpenClaw lifecycle status.")
                if stopping:
                    self.update(job_id, "interrupted", "Native stop could not be confirmed. No automatic retry.")
                    return
                self.stop.wait(2)
        except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.TimeoutExpired) as error:
            reason = str(error) if isinstance(error, (ValueError, NativeRPCError)) else type(error).__name__
            logging.error("Device job failed while %s: %s; no automatic action retry.", phase, reason)
            try:
                aborted = self.abort(job_id)
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
                aborted = False
                logging.error("Native stop could not be confirmed for the failed device job.")
            self.update(job_id, "failed",
                        f"Failed while {phase}: {reason} " +
                        ("Native run stopped." if aborted else
                         "Native stop unconfirmed. Do not repeat uncertain actions."))
        finally:
            self.chat_lock.release()

    def close(self) -> None:
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=55)
            if self.thread.is_alive():
                logging.error("Device job worker did not stop before bridge shutdown.")
