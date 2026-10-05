# SPDX-License-Identifier: Apache-2.0
"""Dedicated SDK task state; never interprets or replays an authorization."""
from __future__ import annotations

import os
import sqlite3
import time

from openclaw_jobs import identifier
from openclaw_messages import preview

ACTIVE = ("working", "stopping")
TERMINAL = ("completed", "failed", "cancelled", "interrupted")


def initialize(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS copilot_tasks ("
               "id TEXT PRIMARY KEY,controller TEXT NOT NULL,session TEXT NOT NULL,"
               "workspace TEXT NOT NULL,title TEXT NOT NULL,status TEXT NOT NULL,"
               "summary TEXT NOT NULL,started REAL NOT NULL,updated REAL NOT NULL,"
               "ack INTEGER NOT NULL DEFAULT 0)")


def interrupt(db: sqlite3.Connection, *, all_controllers: bool = False) -> None:
    clause = "" if all_controllers else " AND controller NOT IN (SELECT id FROM controllers WHERE lease>?)"
    values = () if all_controllers else (time.time(),)
    db.execute("UPDATE copilot_tasks SET status='interrupted',summary="
               "'Controller disconnected. Work is not resumed automatically.',updated=? "
               "WHERE status IN ('working','stopping')" + clause, (time.time(), *values))
    db.execute("DELETE FROM copilot_tasks WHERE updated<? AND status NOT IN ('working','stopping')",
               (time.time() - 7 * 86400,))


def producer(db: sqlite3.Connection, key: str, body: dict, validate_text) -> dict:
    action = body["action"]
    task_id = identifier(body.get("id"))
    if action == "task_begin" and set(body) == {"action", "controller", "id", "session", "title"}:
        session = validate_text(body["session"], 128)
        title = validate_text(body["title"], 240, multiline=True)
        workspace = db.execute("SELECT workspace FROM controllers WHERE id=?", (key,)).fetchone()[0]
        if db.execute("SELECT 1 FROM copilot_tasks WHERE id=?", (task_id,)).fetchone() or db.execute(
                "SELECT 1 FROM requests WHERE id=?", (task_id,)).fetchone():
            raise ValueError("Copilot task identifiers cannot be reused.")
        if db.execute("SELECT 1 FROM copilot_tasks WHERE controller=? AND status IN ('working','stopping')",
                      (key,)).fetchone():
            raise ValueError("Stop or finish the current Copilot task first.")
        now = time.time()
        db.execute("INSERT INTO copilot_tasks VALUES (?,?,?,?,?,'working','Starting Copilot work',?,?,0)",
                   (task_id, key, session, workspace, title, now, now))
    elif action == "task_update" and set(body) == {"action", "controller", "id", "status", "summary"}:
        row = db.execute("SELECT * FROM copilot_tasks WHERE id=? AND controller=?", (task_id, key)).fetchone()
        if row is None or row["status"] not in ACTIVE:
            raise ValueError("Copilot task is no longer active; no state was changed.")
        status = body["status"]
        if status not in ("working", *TERMINAL):
            raise ValueError("Invalid Copilot task state.")
        summary = validate_text(body["summary"], 2047, multiline=True)
        if row["status"] == "stopping" and status == "working":
            return {"id": task_id, "status": "stopping"}
        db.execute("UPDATE copilot_tasks SET status=?,summary=?,updated=? WHERE id=?",
                   (status, summary, time.time(), task_id))
    else:
        raise ValueError("Invalid Copilot task action or fields.")
    return {"id": task_id, "status": db.execute("SELECT status FROM copilot_tasks WHERE id=?", (task_id,)).fetchone()[0]}


def stop(db: sqlite3.Connection, task_id: object) -> dict:
    task_id = identifier(task_id)
    row = db.execute("SELECT * FROM copilot_tasks WHERE id=?", (task_id,)).fetchone()
    if row is None or row["status"] not in ACTIVE:
        raise ValueError("That Copilot task is no longer running.")
    db.execute("UPDATE copilot_tasks SET status='stopping',summary='Stop requested; awaiting SDK confirmation',"
               "updated=? WHERE id=?", (time.time(), task_id))
    db.execute("UPDATE requests SET state='cancelled',result=NULL WHERE controller=? AND session=? "
               "AND state IN ('pending','answered')", (row["controller"], row["session"]))
    return {"copilot": snapshot(db)}


def snapshot(db: sqlite3.Connection) -> dict:
    controllers = db.execute("SELECT COUNT(*) FROM controllers").fetchone()[0]
    rows = db.execute("SELECT * FROM copilot_tasks ORDER BY started DESC LIMIT 6").fetchall()
    tasks = []
    for index, row in enumerate(rows):
        status = row["status"]
        if status == "working" and db.execute("SELECT 1 FROM requests WHERE controller=? AND session=? "
                "AND state='pending'", (row["controller"], row["session"])).fetchone():
            status = "waiting"
        tasks.append({"id": row["id"], "session": row["session"], "project": os.path.basename(row["workspace"]),
                      "title": row["title"], "status": status, "updated_at": row["updated"],
                      "summary": row["summary"] if index == 0 else preview(row["summary"], 256)})
    return {"state": "connected" if controllers else "offline", "controllers": controllers,
            "checked_at": time.time(), "tasks": tasks}


def notification(db: sqlite3.Connection) -> dict:
    row = db.execute("SELECT * FROM copilot_tasks WHERE ack=0 AND status IN ('completed','failed','interrupted') "
                     "AND updated>? ORDER BY updated LIMIT 1", (time.time() - 600,)).fetchone()
    if row is None:
        return {"notification": None}
    return {"notification": {
        "id": row["id"], "kind": "copilot_done" if row["status"] == "completed" else "copilot_fail",
        "sender": "Copilot finished" if row["status"] == "completed" else "Copilot needs attention",
        "preview": preview(row["title"] + "\n" + row["summary"], 256),
        "body": preview(os.path.basename(row["workspace"]) + "\n" + row["title"] + "\n" + row["summary"], 2047),
        "expires_at": int(row["updated"] + 600), "respondable": False,
        "choices": [], "allow_freeform": False}}
