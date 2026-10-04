#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Local, durable incoming-iMessage previews; never calls an AI provider."""
from __future__ import annotations

from contextlib import closing, contextmanager
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
import uuid
from collections.abc import Iterator

QUEUE_LIMIT = 200
WATCH_LIMIT = 65536
MESSAGES_DB = Path.home() / "Library/Messages/chat.db"


def preview(text: str, limit: int) -> str:
    text = "".join(c if c >= " " or c == "\n" else " " for c in text).strip()
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    return data[:limit - 3].decode("utf-8", errors="ignore").rstrip() + "..."


class MessageInbox:
    def __init__(self, state: Path, *, messages_db: Path = MESSAGES_DB,
                 executable: str = "/opt/homebrew/bin/imsg") -> None:
        self.path = state / "messages.sqlite"
        self.messages_db = messages_db
        self.executable = executable
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.process: subprocess.Popen[str] | None = None
        self.thread: threading.Thread | None = None
        self.error: str | None = "iMessage watcher is starting."
        baseline = self.source_query("SELECT COALESCE(MAX(ROWID), 0) FROM message")[0]
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS notifications "
                       "(source_id INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, "
                       "sender TEXT NOT NULL, preview TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('cursor', ?)", (str(baseline),))
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('last_ack', '')")
            if int(db.execute("SELECT value FROM metadata WHERE key='cursor'").fetchone()[0]) > baseline:
                raise ValueError("Messages database was reset; restore or explicitly reset the local inbox.")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def source_query(self, sql: str, parameters: tuple = ()) -> tuple:
        with closing(sqlite3.connect(self.messages_db.resolve().as_uri() + "?mode=ro", uri=True,
                                     timeout=5)) as db:
            row = db.execute(sql, parameters).fetchone()
        if row is None:
            raise ValueError("Message metadata is unavailable.")
        return row

    def cursor(self) -> int:
        with self.connect() as db:
            return int(db.execute("SELECT value FROM metadata WHERE key='cursor'").fetchone()[0])

    def ingest(self, record: object) -> None:
        if not isinstance(record, dict):
            raise ValueError("Invalid iMessage event shape.")
        rowid = record.get("id")
        if type(rowid) is not int or rowid <= 0:
            raise ValueError("Invalid iMessage event identifier.")
        with self.lock, self.connect() as db:
            cursor = int(db.execute("SELECT value FROM metadata WHERE key='cursor'").fetchone()[0])
            if rowid <= cursor:
                return
            service, outgoing, reaction, system = self.source_query(
                "SELECT service, is_from_me, associated_message_type, is_system_message "
                "FROM message WHERE ROWID=?", (rowid,))
            if service == "iMessage" and not outgoing and not reaction and not system:
                sender, text = record.get("sender"), record.get("text")
                if not isinstance(sender, str) or not sender or not isinstance(text, str):
                    raise ValueError("Invalid incoming iMessage fields.")
                if db.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] >= QUEUE_LIMIT:
                    raise RuntimeError("iMessage queue is full; wake the device and dismiss alerts.")
                db.execute("INSERT INTO notifications VALUES (?, ?, ?, ?)",
                           (rowid, uuid.uuid4().hex, preview(sender, 96),
                            preview(text, 256) or "[Attachment or non-text message]"))
            db.execute("UPDATE metadata SET value=? WHERE key='cursor'", (str(rowid),))

    def poll(self, acknowledgment: object) -> dict:
        if not isinstance(acknowledgment, str) or (
                acknowledgment and (len(acknowledgment) != 32 or
                                    any(c not in "0123456789abcdef" for c in acknowledgment))):
            raise ValueError("Invalid notification acknowledgment.")
        with self.lock, self.connect() as db:
            head = db.execute("SELECT id, sender, preview FROM notifications ORDER BY source_id LIMIT 1").fetchone()
            last_ack = db.execute("SELECT value FROM metadata WHERE key='last_ack'").fetchone()[0]
            if acknowledgment and acknowledgment != last_ack:
                if head is None or head[0] != acknowledgment:
                    raise ValueError("Only the oldest delivered notification can be acknowledged.")
                db.execute("DELETE FROM notifications WHERE id=?", (acknowledgment,))
                db.execute("UPDATE metadata SET value=? WHERE key='last_ack'", (acknowledgment,))
                head = db.execute("SELECT id, sender, preview FROM notifications ORDER BY source_id LIMIT 1").fetchone()
            result = {"notification": dict(zip(("id", "sender", "preview"), head)) if head else None}
            error = self.error
        if head is None and error:
            raise RuntimeError(error)
        return result

    def start(self) -> None:
        self.thread = threading.Thread(target=self.watch, name="imessage-watch", daemon=True)
        self.thread.start()

    def watch(self) -> None:
        while not self.stop.is_set():
            process = None
            try:
                process = subprocess.Popen(
                    [self.executable, "watch", "--json", "--since-rowid", str(self.cursor())],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                    encoding="utf-8")
                with self.lock:
                    self.process = process
                    self.error = None
                assert process.stdout is not None
                while not self.stop.is_set():
                    line = process.stdout.readline(WATCH_LIMIT + 1)
                    if not line:
                        raise RuntimeError("iMessage watcher exited; verify Full Disk Access in the service context.")
                    if len(line.encode("utf-8")) > WATCH_LIMIT or not line.endswith("\n"):
                        raise ValueError("iMessage event exceeds the supported size.")
                    self.ingest(json.loads(line))
            except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
                with self.lock:
                    self.error = str(error) if isinstance(error, RuntimeError) else (
                        "iMessage watcher failed; verify Messages access and the supported event schema.")
                if not self.stop.is_set():
                    logging.error("Incoming iMessages paused: %s", self.error)
            finally:
                if process is not None:
                    self.terminate(process)
                    if process.stdout is not None:
                        process.stdout.close()
                with self.lock:
                    self.process = None
            self.stop.wait(5)

    @staticmethod
    def terminate(process: subprocess.Popen[str]) -> None:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def close(self) -> None:
        self.stop.set()
        with self.lock:
            process = self.process
        if process is not None:
            self.terminate(process)
        if self.thread is not None:
            self.thread.join(timeout=5)
