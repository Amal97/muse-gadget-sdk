# SPDX-License-Identifier: Apache-2.0
"""Request-scoped Copilot decisions. No model interprets an authorization."""
from __future__ import annotations

from contextlib import closing, contextmanager
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from collections.abc import Iterator

from openclaw_jobs import identifier
from openclaw_messages import preview

LEASE_SECONDS = 20
REQUEST_SECONDS = 600


def spoken(value: str) -> str:
    return value.strip().casefold().rstrip(".!")


def text(value: object, limit: int, *, multiline: bool = False) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > limit:
        raise ValueError("Copilot text is empty or exceeds its limit.")
    if any(ord(c) < 32 and not (multiline and c == "\n") or ord(c) == 127 for c in value):
        raise ValueError("Copilot text contains control characters.")
    return value


class CopilotRequests:
    def __init__(self, state: Path) -> None:
        self.path = state / "copilot.sqlite"
        self.lock = threading.RLock()
        credential = state / "copilot-controller.json"
        try:
            fd = os.open(credential, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w") as output:
                json.dump({"token": secrets.token_hex(32)}, output)
        os.chmod(credential, 0o600)
        token = json.loads(credential.read_text()).get("token")
        if not isinstance(token, str) or len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
            raise ValueError("Invalid private Copilot controller credential.")
        self.token = token
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS controllers "
                       "(id TEXT PRIMARY KEY,workspace TEXT NOT NULL,lease REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS requests "
                       "(seq INTEGER PRIMARY KEY,id TEXT UNIQUE NOT NULL,controller TEXT NOT NULL,"
                       "session TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL,choices TEXT NOT NULL,"
                       "freeform INTEGER NOT NULL,respondable INTEGER NOT NULL,expires REAL NOT NULL,"
                       "state TEXT NOT NULL,result TEXT)")
            self._interrupt(db)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def _interrupt(self, db: sqlite3.Connection) -> None:
        db.execute("UPDATE requests SET state='interrupted',result=NULL "
                   "WHERE state IN ('pending','answered')")
        db.execute("DELETE FROM controllers")

    def close(self) -> None:
        with self.lock, self.connect() as db:
            self._interrupt(db)

    def _expire(self, db: sqlite3.Connection) -> None:
        now = time.time()
        db.execute("UPDATE requests SET state='expired',result=NULL "
                   "WHERE state IN ('pending','answered') AND (expires<=? OR controller NOT IN "
                   "(SELECT id FROM controllers WHERE lease>?))", (now, now))
        db.execute("DELETE FROM controllers WHERE lease<=?", (now,))
        db.execute("DELETE FROM requests WHERE expires<? AND state NOT IN ('pending','answered')",
                   (now - 7 * 86400,))

    def _controller(self, db: sqlite3.Connection, value: object) -> str:
        key = identifier(value)
        if not db.execute("SELECT 1 FROM controllers WHERE id=?", (key,)).fetchone():
            raise ValueError("Copilot controller expired or disconnected; start a new session.")
        return key

    def controller(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise ValueError("Expected a Copilot controller request.")
        action = body.get("action")
        with self.lock, self.connect() as db:
            self._expire(db)
            if action == "open" and set(body) == {"action", "controller", "workspace"}:
                key = identifier(body["controller"])
                workspace = text(body["workspace"], 1024)
                existing = db.execute("SELECT * FROM controllers WHERE id=?", (key,)).fetchone()
                if existing and existing["workspace"] != workspace:
                    raise ValueError("Copilot controller workspace cannot change.")
                if not existing and db.execute("SELECT COUNT(*) FROM controllers").fetchone()[0] >= 8:
                    raise ValueError("Too many Copilot controllers.")
                db.execute("INSERT INTO controllers VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET lease=excluded.lease",
                           (key, workspace, time.time() + LEASE_SECONDS))
                return {"controller": key}
            key = self._controller(db, body.get("controller"))
            if action == "heartbeat" and set(body) == {"action", "controller"}:
                db.execute("UPDATE controllers SET lease=? WHERE id=?", (time.time() + LEASE_SECONDS, key))
                return {"controller": key}
            if action == "close" and set(body) == {"action", "controller"}:
                db.execute("UPDATE requests SET state='cancelled',result=NULL "
                           "WHERE controller=? AND state IN ('pending','answered')", (key,))
                db.execute("DELETE FROM controllers WHERE id=?", (key,))
                return {"closed": True}
            if action == "create" and set(body) == {
                    "action", "controller", "id", "session", "kind", "body",
                    "choices", "allow_freeform", "respondable"}:
                request_id = identifier(body["id"])
                session = text(body["session"], 128)
                kind = body["kind"]
                if kind not in ("permission", "question"):
                    raise ValueError("Unknown Copilot request kind.")
                content = text(body["body"], 2047, multiline=True)
                choices = body["choices"]
                freeform, respondable = body["allow_freeform"], body["respondable"]
                if type(freeform) is not bool or type(respondable) is not bool:
                    raise ValueError("Copilot flags must be booleans.")
                if not isinstance(choices, list) or len(choices) > 64:
                    raise ValueError("Invalid Copilot choices.")
                choices = [text(choice, 2047) for choice in choices]
                if len(json.dumps(choices).encode()) > 16384:
                    raise ValueError("Copilot choices exceed the transport budget.")
                if len(set(spoken(c) for c in choices)) != len(choices):
                    raise ValueError("Copilot choices must be unambiguous.")
                if kind == "permission" and (choices or freeform):
                    raise ValueError("Permissions support only approve-once or deny.")
                if kind == "question" and not freeform and not choices and respondable:
                    raise ValueError("Question has no answer options.")
                if respondable and (not content.isascii() or any(not c.isascii() for c in choices)):
                    raise ValueError("Gadget-reviewable requests must be fully displayable ASCII.")
                if respondable and (len(choices) > 12 or any(len(c.encode()) > 240 for c in choices)):
                    raise ValueError("Gadget choice limits exceeded.")
                fields = (key, session, kind, content, json.dumps(choices), int(freeform), int(respondable))
                existing = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
                if existing:
                    old = tuple(existing[name] for name in (
                        "controller", "session", "kind", "body", "choices", "freeform", "respondable"))
                    if fields != old:
                        raise ValueError("Copilot request ID is already bound to different work.")
                    return {"id": request_id, "state": existing["state"]}
                if db.execute("SELECT COUNT(*) FROM requests WHERE state='pending'").fetchone()[0] >= 32:
                    raise ValueError("Too many pending Copilot requests.")
                db.execute("INSERT INTO requests (id,controller,session,kind,body,choices,freeform,"
                           "respondable,expires,state) VALUES (?,?,?,?,?,?,?,?,?,'pending')",
                           (request_id, *fields, time.time() + REQUEST_SECONDS))
                return {"id": request_id, "state": "pending"}
            if action in ("take", "cancel") and set(body) == {"action", "controller", "id"}:
                row = db.execute("SELECT * FROM requests WHERE id=? AND controller=?",
                                 (identifier(body["id"]), key)).fetchone()
                if row is None:
                    raise ValueError("Unknown Copilot request for this controller.")
                if action == "cancel":
                    db.execute("UPDATE requests SET state='cancelled',result=NULL "
                               "WHERE id=? AND state IN ('pending','answered')", (row["id"],))
                    return {"cancelled": True}
                result = {"id": row["id"], "state": row["state"]}
                if row["state"] == "answered":
                    result["result"] = json.loads(row["result"])
                    db.execute("UPDATE requests SET state='consumed' WHERE id=?", (row["id"],))
                return result
            if action == "answer" and set(body) == {"action", "controller", "id", "text"}:
                row = db.execute("SELECT * FROM requests WHERE id=? AND controller=?",
                                 (identifier(body["id"]), key)).fetchone()
                if row is None:
                    raise ValueError("Unknown Copilot request for this controller.")
                return self._answer(db, row, body["text"], desktop=True)
        raise ValueError("Invalid Copilot controller action or fields.")

    def _answer(self, db: sqlite3.Connection, row: sqlite3.Row, value: object, *, desktop: bool,
                source: str = "voice") -> dict:
        if row["state"] != "pending":
            raise ValueError("Copilot request is no longer waiting; no decision was sent.")
        answer = text(value, 2047, multiline=True).strip()
        if row["kind"] == "permission":
            normalized = spoken(answer)
            if normalized in ("approve", "approve once", "approve it"):
                if not desktop and not row["respondable"]:
                    raise ValueError("Review this request on the computer; gadget approval is disabled.")
                result = {"kind": "approve-once"}
                message = "Approval submitted for this request only."
            elif normalized in ("deny", "deny it", "reject", "no", "cancel"):
                result = {"kind": "reject", "feedback": "User denied this request."}
                message = "Denial submitted."
            else:
                raise ValueError("Say 'approve' or 'deny'. Ambiguous speech never approves work.")
        else:
            if not desktop and not row["respondable"]:
                raise ValueError("Answer this question on the computer; gadget response is disabled.")
            choices = json.loads(row["choices"])
            matched = next((choice for choice in choices if spoken(answer) == spoken(choice)), None)
            numbers = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                       "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
                       "first": 1, "second": 2, "third": 3}
            option = spoken(answer)
            for prefix in ("option ", "choice ", "number "):
                if option.startswith(prefix):
                    option = option[len(prefix):]
                    break
            number = int(option) if option.isascii() and option.isdigit() and len(option) <= 2 else numbers.get(option)
            if matched is None and number is not None and 1 <= number <= len(choices):
                matched = choices[number - 1]
            if matched is None and number is not None and choices:
                raise ValueError("That option number was not offered. No answer was sent.")
            if matched is None and not row["freeform"]:
                raise ValueError("Say an offered choice or its option number. No answer was sent.")
            result = {"answer": matched or answer, "wasFreeform": matched is None}
            message = "Answer submitted."
        db.execute("UPDATE requests SET state='answered',result=? WHERE id=?",
                   (json.dumps(result), row["id"]))
        logging.info("Copilot %s response queued for its owning session (%s).",
                     row["kind"], "desktop" if desktop else source)
        return {"copilot": {"id": row["id"], "state": "answered", "message": message}}

    def device(self, body: dict) -> dict:
        if not isinstance(body, dict) or body.get("action") != "copilot_voice" or set(body) != {"action", "id", "text"}:
            raise ValueError("Expected a request-scoped Copilot voice response.")
        with self.lock, self.connect() as db:
            self._expire(db)
            row = db.execute("SELECT * FROM requests WHERE id=?", (identifier(body["id"]),)).fetchone()
            if row is None:
                raise ValueError("Unknown Copilot request; no decision was sent.")
            return self._answer(db, row, body["text"], desktop=False)

    def owns_ack(self, ack: str) -> bool:
        with self.lock, self.connect() as db:
            return bool(ack and db.execute("SELECT 1 FROM requests WHERE id=?", (ack,)).fetchone())

    def poll(self, ack: str = "") -> dict:
        with self.lock, self.connect() as db:
            self._expire(db)
            if ack:
                row = db.execute("SELECT * FROM requests WHERE id=?", (identifier(ack),)).fetchone()
                if row is None:
                    raise ValueError("Unknown Copilot acknowledgment.")
                if row["state"] == "pending":
                    if row["kind"] == "permission":
                        self._answer(db, row, "deny", desktop=False, source="dismiss")
                    else:
                        db.execute("UPDATE requests SET state='cancelled' WHERE id=?", (ack,))
                        logging.info("Copilot question declined by the gadget.")
            row = db.execute("SELECT requests.*,controllers.workspace FROM requests JOIN controllers "
                             "ON controllers.id=requests.controller WHERE state='pending' ORDER BY seq LIMIT 1").fetchone()
            if row is None:
                return {"notification": None}
            return {"notification": {
                "id": row["id"], "kind": "copilot_allow" if row["kind"] == "permission" else "copilot_ask",
                "sender": "Copilot approval" if row["kind"] == "permission" else "Copilot question",
                "preview": preview(row["body"], 256), "body": row["body"],
                "expires_at": int(row["expires"]), "respondable": bool(row["respondable"]),
            }}
