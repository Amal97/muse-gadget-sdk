# SPDX-License-Identifier: Apache-2.0
"""Private local reminders, calendar preferences and deterministic briefings."""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timedelta
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from urllib.parse import urlencode
from urllib.request import urlopen
import uuid

from openclaw_messages import preview
from openclaw_jobs import identifier

DEFAULTS = {"briefing_enabled": False, "calendars_enabled": False, "calendar_ids": [],
            "hour": 8, "minute": 0, "weather": None,
            "favourites": ["timer_5", "timer_10", "briefing", "dashboard"]}
FAVOURITES = ("timer_5", "timer_10", "timer_custom", "reminder", "briefing", "dashboard")


class Companion:
    def __init__(self, state: Path, *, calendar: Path | None = None) -> None:
        self.path = state / "companion.sqlite"
        self.calendar = calendar or state / "Muse Calendar Reader.app/Contents/MacOS/calendar-reader"
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.brief_thread: threading.Thread | None = None
        self.reply_thread: threading.Thread | None = None
        self.weather_thread: threading.Thread | None = None
        self.weather_lock = threading.Lock()
        self.calendars: list[dict] = []
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS reminders "
                       "(id TEXT PRIMARY KEY,title TEXT NOT NULL,due REAL NOT NULL,state TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS alerts "
                       "(seq INTEGER PRIMARY KEY,id TEXT UNIQUE NOT NULL,kind TEXT NOT NULL,"
                       "title TEXT NOT NULL,body TEXT NOT NULL,reference TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS drafts "
                       "(id TEXT PRIMARY KEY,chat_guid TEXT NOT NULL,recipient TEXT NOT NULL,"
                       "text TEXT NOT NULL,state TEXT NOT NULL,detail TEXT NOT NULL)")
            db.execute("UPDATE drafts SET state='uncertain',detail=? WHERE state='sending'",
                       ("Bridge restarted during send; check Messages. No automatic retry.",))
            for key, value in (("settings", DEFAULTS), ("calendars", []), ("last_ack", ""),
                               ("briefing_date", ""), ("briefing", {"state": "not_requested"}),
                               ("weather", {"state": "not_configured"})):
                db.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)", (key, json.dumps(value)))
        self.calendars = self.get("calendars")
        previous = self.get("briefing")
        if previous.get("state") == "building":
            self.put("briefing", {"state": "interrupted", "body": "Briefing interrupted by bridge restart."})

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def get(self, key: str):
        with self.connect() as db:
            row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        if row is None:
            raise ValueError("Companion metadata is missing.")
        return json.loads(row[0])

    def put(self, key: str, value: object) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, json.dumps(value)))

    def calendar_query(self, command: str, query: dict | None = None) -> dict:
        # LaunchServices attributes calendar access to the app the user authorized.
        with tempfile.TemporaryDirectory(prefix="muse-calendar-") as directory:
            request = Path(directory) / "request.json"
            request.write_text(json.dumps({"command": command, "query": query}), encoding="utf-8")
            request.chmod(0o600)
            result = subprocess.run(
                ["/usr/bin/open", "-n", str(self.calendar.parents[2]),
                 "--args", "exchange", str(request)],
                capture_output=True, text=True, timeout=15, check=False)
            response = Path(directory) / "response.json"
            if result.returncode:
                raise RuntimeError("Calendar reader launch failed; check its installation and permissions.")
            # The atomic response is authoritative; short-lived apps cannot reliably use open -W.
            deadline = time.monotonic() + 15
            while not response.is_file() and time.monotonic() < deadline:
                time.sleep(0.05)
            if not response.is_file():
                raise RuntimeError("Calendar reader did not respond; check its installation and permissions.")
            value = json.loads(response.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Invalid calendar reader response.")
        if value.get("error"):
            raise RuntimeError("Calendar access unavailable; grant the reader full access or disable calendars.")
        return value

    def refresh_calendars(self) -> list[dict]:
        calendars = self.calendar_query("calendars").get("calendars")
        if not isinstance(calendars, list) or len(calendars) > 100 or any(
                not isinstance(c, dict) or not isinstance(c.get("id"), str) or not c["id"]
                or len(c["id"]) > 256 or not isinstance(c.get("name"), str) for c in calendars):
            raise ValueError("Invalid calendar list.")
        self.calendars = calendars
        self.put("calendars", calendars)
        return calendars

    def configure(self, changes: dict) -> dict:
        if not isinstance(changes, dict) or not changes or set(changes) - set(DEFAULTS):
            raise ValueError("Unknown companion setting.")
        with self.lock:
            settings = {**self.get("settings"), **changes}
            for field in ("briefing_enabled", "calendars_enabled"):
                if type(settings[field]) is not bool:
                    raise ValueError("Expected a boolean setting.")
            for field, maximum in (("hour", 23), ("minute", 59)):
                if type(settings[field]) is not int or not 0 <= settings[field] <= maximum:
                    raise ValueError("Invalid briefing time.")
            favourites = settings["favourites"]
            if not isinstance(favourites, list) or not 0 <= len(favourites) <= len(FAVOURITES) or any(
                    f not in FAVOURITES for f in favourites) or len(set(favourites)) != len(favourites):
                raise ValueError("Invalid favourite quick actions.")
            ids = settings["calendar_ids"]
            if settings["calendars_enabled"] or ids == "all":
                available = {c["id"] for c in self.refresh_calendars()}
                if ids == "all":
                    ids = [c["id"] for c in self.calendars]
                    settings["calendar_ids"] = ids
                if not isinstance(ids, list) or any(c not in available for c in ids):
                    raise ValueError("An enabled calendar is unavailable.")
            if not isinstance(ids, list) or any(not isinstance(c, str) for c in ids):
                raise ValueError("Invalid enabled calendar identifiers.")
            weather = settings["weather"]
            if weather is not None:
                if not isinstance(weather, dict) or set(weather) != {"label", "latitude", "longitude"}:
                    raise ValueError("Invalid weather location.")
                if not isinstance(weather["label"], str) or not 0 < len(weather["label"].encode()) <= 96:
                    raise ValueError("Invalid weather location label.")
                for name, maximum in (("latitude", 90), ("longitude", 180)):
                    value = weather[name]
                    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > maximum:
                        raise ValueError("Invalid weather coordinates.")
            self.put("settings", settings)
            return settings

    def reminders(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM reminders WHERE state!='done' ORDER BY due LIMIT 100")]

    def add_reminder(self, title: object, due: object) -> dict:
        if not isinstance(title, str) or not 0 < len(title.strip().encode()) <= 160:
            raise ValueError("Reminder title must contain 1-160 UTF-8 bytes.")
        if type(due) not in (float, int) or not math.isfinite(due) or not time.time() < due < time.time() + 366 * 86400:
            raise ValueError("Reminder time must be within the next year.")
        reminder = {"id": uuid.uuid4().hex, "title": title.strip(), "due": due, "state": "pending"}
        with self.lock, self.connect() as db:
            if db.execute("SELECT COUNT(*) FROM reminders WHERE state!='done'").fetchone()[0] >= 100:
                raise RuntimeError("Reminder list is full; dismiss or delete reminders first.")
            db.execute("INSERT INTO reminders VALUES (?,?,?,?)", tuple(reminder.values()))
        return reminder

    def alert(self, kind: str, title: str, body: str, reference: str = "") -> None:
        with self.lock, self.connect() as db:
            if db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] >= 200:
                raise RuntimeError("Companion notification queue is full.")
            db.execute("INSERT INTO alerts (id,kind,title,body,reference) VALUES (?,?,?,?,?)",
                       (uuid.uuid4().hex, kind, preview(title, 96), preview(body, 2047), reference))

    def owns_ack(self, ack: str) -> bool:
        with self.connect() as db:
            return bool(db.execute("SELECT 1 FROM alerts WHERE id=?", (ack,)).fetchone()) or (
                bool(ack) and ack == self.get("last_ack"))

    def poll(self, ack: str = "") -> dict:
        if ack:
            identifier(ack)
        with self.lock, self.connect() as db:
            head = db.execute("SELECT * FROM alerts ORDER BY seq LIMIT 1").fetchone()
            if ack and ack != self.get("last_ack"):
                if head is None or head["id"] != ack:
                    raise ValueError("Only the delivered companion alert may be dismissed.")
                db.execute("DELETE FROM alerts WHERE id=?", (ack,))
                if head["kind"] == "reminder":
                    db.execute("UPDATE reminders SET state='done' WHERE id=?", (head["reference"],))
                db.execute("UPDATE metadata SET value=? WHERE key='last_ack'", (json.dumps(ack),))
                head = db.execute("SELECT * FROM alerts ORDER BY seq LIMIT 1").fetchone()
            return {"notification": {"id": head["id"], "kind": head["kind"],
                    "sender": head["title"], "preview": preview(head["body"], 256),
                    "body": head["body"]} if head else None}

    def snooze(self, alert_id: object, seconds: object) -> None:
        alert_id = identifier(alert_id)
        if type(seconds) is not int or not 1 <= seconds <= 86400:
            raise ValueError("Invalid snooze interval.")
        with self.lock, self.connect() as db:
            row = db.execute("SELECT kind,reference FROM alerts WHERE id=?", (alert_id,)).fetchone()
            if row is None or row["kind"] != "reminder":
                raise ValueError("Only gadget reminders can be snoozed.")
            db.execute("UPDATE reminders SET due=?,state='pending' WHERE id=?",
                       (time.time() + seconds, row["reference"]))
            db.execute("DELETE FROM alerts WHERE id=?", (alert_id,))
            db.execute("UPDATE metadata SET value=? WHERE key='last_ack'", (json.dumps(alert_id),))

    def fire_due(self, now: float) -> None:
        with self.lock, self.connect() as db:
            for reminder in db.execute(
                    "SELECT * FROM reminders WHERE state='pending' AND due<=? ORDER BY due", (now,)):
                if db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] >= 200:
                    raise RuntimeError("Companion queue full; due reminders remain pending.")
                db.execute("INSERT INTO alerts (id,kind,title,body,reference) VALUES (?,?,?,?,?)",
                           (uuid.uuid4().hex, "reminder", "Reminder", reminder["title"], reminder["id"]))
                db.execute("UPDATE reminders SET state='fired' WHERE id=?", (reminder["id"],))

    def briefing(self, *, scheduled: bool = False) -> dict:
        with self.lock:
            previous = self.get("briefing")
            if previous.get("state") == "building":
                return previous
            self.put("briefing", {"state": "building", "body": "Building local morning briefing..."})
            self.brief_thread = threading.Thread(target=self.build_briefing,
                                                args=(scheduled,), name="morning-briefing", daemon=True)
            try:
                self.brief_thread.start()
            except RuntimeError:
                self.put("briefing", {"state": "failed", "body": "Could not start briefing worker."})
                raise
            return self.get("briefing")

    def build_briefing(self, scheduled: bool) -> None:
        settings = self.get("settings")
        now = datetime.now().astimezone()
        start = datetime.combine(now.date(), datetime.min.time()).astimezone()
        end = datetime.combine(now.date() + timedelta(days=1), datetime.min.time()).astimezone()
        lines = ["Morning briefing - " + now.strftime("%a %d %b"), "Times use the Mac's local timezone."]
        errors = []
        summary = []
        weather = settings["weather"]
        if weather:
            current = self.refresh_weather()
            if current["state"] == "ready":
                lines.append(f'{weather["label"]}: {current["low"]} to '
                             f'{current["high"]} C; {current["rain_probability"]}% rain chance.')
            else:
                errors.append("Weather unavailable; check Mac internet access.")
        else:
            lines.append("Weather location not configured.")
        reminders = [r for r in self.reminders() if r["due"] < end.timestamp()]
        lines.append("Gadget reminders: " + str(len(reminders)))
        lines.extend(datetime.fromtimestamp(r["due"]).strftime("%H:%M") + " " + r["title"]
                     for r in reminders)
        event_count = None
        if settings["calendars_enabled"]:
            try:
                events = self.calendar_query("events", {"ids": settings["calendar_ids"],
                    "start": start.timestamp(), "end": end.timestamp()}).get("events")
                if not isinstance(events, list):
                    raise ValueError("Invalid calendar agenda.")
                lines.append("Calendar events: " + str(len(events)))
                event_count = len(events)
                for event in events:
                    stamp = "All day" if event["all_day"] else datetime.fromtimestamp(
                        event["start"]).strftime("%H:%M")
                    lines.append(stamp + " " + event["title"] + " (" + event["calendar"] + ")")
                    if len(summary) < 2:
                        summary.append(stamp + " " + event["title"])
            except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.TimeoutExpired):
                errors.append("Calendar unavailable; check reader permissions or enabled calendars.")
        else:
            lines.append("Calendars disabled.")
        lines.extend(errors)
        if not summary:
            summary = [datetime.fromtimestamp(r["due"]).strftime("%H:%M") + " " + r["title"]
                       for r in reminders[:2]]
        body = "\n".join(lines)
        if len(body.encode()) > 2047:
            body = preview(body, 1960) + "\nAgenda truncated; open Calendar for all events."
        result = {"state": "partial" if errors else "ready", "body": body, "date": now.isoformat(),
                  "summary": preview("\n".join(summary) if summary else
                                     ("Some sources unavailable. Tap for details." if errors else
                                      "No events or gadget reminders today." if settings["calendars_enabled"] else
                                      "No gadget reminders today.\nCalendars disabled."), 256),
                  "event_count": event_count, "reminder_count": len(reminders)}
        self.put("briefing", result)
        if errors:
            logging.error("Morning briefing has unavailable sources; see device status.")
        if scheduled:
            try:
                self.alert("briefing", "Partial morning briefing" if errors else "Morning briefing", body)
            except (RuntimeError, sqlite3.Error):
                logging.error("Morning briefing notification could not be queued; digest retained in status.")

    def refresh_weather(self) -> dict:
        # Share one fetch with briefings; status reads never perform network I/O.
        with self.weather_lock:
            location = self.get("settings")["weather"]
            previous = self.get("weather")
            now = time.time()
            if not location:
                result = {"state": "not_configured"}
            elif previous.get("location") == location and now - previous.get("checked_at", 0) < (
                    900 if previous["state"] == "ready" else 300):
                return previous
            else:
                try:
                    query = urlencode({**{k: location[k] for k in ("latitude", "longitude")},
                                       "current": "temperature_2m,weather_code,is_day",
                                       "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                                       "forecast_days": 1, "timezone": "auto", "timeformat": "unixtime"})
                    with urlopen("https://api.open-meteo.com/v1/forecast?" + query, timeout=10) as response:
                        raw = response.read(65537)
                    if len(raw) > 65536:
                        raise ValueError("Weather response too large.")
                    data = json.loads(raw)
                    current, daily = data["current"], data["daily"]
                    result = {"state": "ready", "location": location, "checked_at": now,
                              "updated_at": now, "observed_at": current["time"],
                              "temperature": current["temperature_2m"], "code": current["weather_code"],
                              "is_day": current["is_day"], "low": daily["temperature_2m_min"][0],
                              "high": daily["temperature_2m_max"][0],
                              "rain_probability": daily["precipitation_probability_max"][0]}
                    for field, minimum, maximum in (("temperature", -100, 100), ("low", -100, 100),
                            ("high", -100, 100), ("rain_probability", 0, 100), ("code", 0, 99),
                            ("is_day", 0, 1), ("observed_at", now - 86400, now + 3600)):
                        value = result[field]
                        if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
                            raise ValueError("Invalid weather measurements.")
                    if result["low"] > result["high"] or any(
                            int(result[f]) != result[f] for f in ("code", "is_day")):
                        raise ValueError("Invalid weather measurements.")
                except (OSError, ValueError, KeyError, IndexError, TypeError):
                    logging.error("Current weather unavailable; inspect internet access and weather configuration.")
                    result = {**(previous if previous.get("location") == location else {}),
                              "state": "stale" if previous.get("location") == location and
                              "temperature" in previous else "unavailable",
                              "location": location, "checked_at": now,
                              "error": "Weather unavailable; check Mac internet access."}
            with self.lock:
                if self.get("settings")["weather"] != location:
                    return {"state": "pending"}
                self.put("weather", result)
            return result

    def weather_status(self, location: dict | None) -> dict:
        weather = self.get("weather")
        if not location:
            return {"state": "not_configured"}
        if weather.get("location") != location:
            return {"state": "pending", "location": location}
        if weather["state"] == "ready" and time.time() - weather["updated_at"] >= 1200:
            return {**weather, "state": "stale"}
        return weather

    def status(self) -> dict:
        settings = self.get("settings")
        reminders = self.reminders()
        with self.connect() as db:
            row = db.execute("SELECT id,recipient,text,state,detail FROM drafts ORDER BY rowid DESC LIMIT 1").fetchone()
        draft = dict(row) if row else None
        if draft:
            draft["preview"] = preview(draft["text"], 256)
        now = datetime.now().astimezone()
        return {"settings": settings,
            "clock": {"utc_offset_seconds": int(now.utcoffset().total_seconds()), "timezone": now.tzname()},
            "weather": self.weather_status(settings["weather"]), "calendars": [
            {"id": c["id"], "name": preview(c["name"], 96),
             "enabled": c["id"] in settings["calendar_ids"]} for c in self.calendars],
            "reminders": reminders[:12], "reminder_count": len(reminders),
            "briefing": self.get("briefing"), "draft": draft}

    def prepare_reply(self, inbox, notification: object, text: object) -> dict:
        identifier(notification)
        if not isinstance(text, str) or not 0 < len(text.strip().encode()) < 2048:
            raise ValueError("Reply must contain 1-2047 UTF-8 bytes.")
        target = inbox.reply_target(notification)
        draft_id = uuid.uuid4().hex
        with self.lock, self.connect() as db:
            row = db.execute("INSERT INTO drafts VALUES (?,?,?,?,'unconfirmed',?) "
                             "RETURNING id,recipient,text,state,detail",
                       (draft_id, target["chat_guid"], target["recipient"], text.strip(),
                        "Not sent. Confirm the exact conversation and text on the device.")).fetchone()
        result = self.status()
        result["draft"] = dict(row)
        result["draft"]["preview"] = preview(row["text"], 256)
        return result

    def confirm_reply(self, draft_id: object, inbox, *, expected_text: str | None = None,
                      expected_recipient: str | None = None) -> dict:
        draft_id = identifier(draft_id)
        with self.lock, self.connect() as db:
            row = db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown reply draft.")
            if expected_text is not None and (
                    row["text"] != expected_text or row["recipient"] != expected_recipient):
                raise ValueError("Reply draft changed; review the exact conversation and text again.")
            if row["state"] != "unconfirmed":
                return self.status()
            db.execute("UPDATE drafts SET state='sending',detail='Sending once; do not repeat' WHERE id=?",
                       (draft_id,))
        self.reply_thread = threading.Thread(target=self.send_reply, args=(dict(row), inbox.executable),
                                             name="confirmed-imessage", daemon=True)
        try:
            self.reply_thread.start()
        except RuntimeError:
            with self.connect() as db:
                db.execute("UPDATE drafts SET state='uncertain',detail=? WHERE id=?",
                           ("Send worker failed; no automatic retry.", draft_id))
            raise
        return self.status()

    def send_reply(self, draft: dict, executable: str) -> None:
        state, detail = "uncertain", "Send result uncertain; check Messages before resending."
        try:
            result = subprocess.run([executable, "send", "--chat-guid", draft["chat_guid"],
                                     "--text", draft["text"], "--service", "imessage",
                                     "--no-sms-fallback", "--json"],
                                    capture_output=True, text=True, timeout=25, check=False)
            data = json.loads(result.stdout) if result.returncode == 0 else None
            if isinstance(data, dict) and data.get("status") == "sent":
                state, detail = "sent", "Sent to the confirmed conversation; delivery not confirmed."
            else:
                logging.error("Confirmed iMessage send did not return a recognized success acknowledgment.")
        except (OSError, ValueError, subprocess.TimeoutExpired):
            logging.error("Confirmed iMessage send failed or was interrupted; no automatic retry.")
        with self.connect() as db:
            db.execute("UPDATE drafts SET state=?,detail=? WHERE id=?", (state, detail, draft["id"]))

    def handle(self, body: dict, inbox=None) -> dict:
        if not isinstance(body, dict):
            raise ValueError("Expected a companion request.")
        action = body.get("action")
        if action == "status" and set(body) == {"action"}:
            return self.status()
        if action == "briefing_status" and set(body) == {"action"}:
            return {"briefing": self.get("briefing")}
        if action == "reminders" and set(body) == {"action"}:
            return {"reminders": self.reminders()}
        if action == "settings" and set(body) == {"action", "settings"}:
            self.configure(body["settings"])
        elif action == "reminder" and set(body) == {"action", "title", "due"}:
            return {"reminder": self.add_reminder(body["title"], body["due"])}
        elif action == "reminder" and set(body) == {"action", "title", "after_seconds"}:
            seconds = body["after_seconds"]
            if type(seconds) is not int or not 1 <= seconds <= 365 * 86400:
                raise ValueError("Invalid reminder interval.")
            return {"reminder": self.add_reminder(body["title"], time.time() + seconds)}
        elif action == "snooze" and set(body) == {"action", "id", "seconds"}:
            self.snooze(body["id"], body["seconds"])
        elif action == "delete_reminder" and set(body) == {"action", "id"}:
            reminder_id = identifier(body["id"])
            with self.lock, self.connect() as db:
                if not db.execute("SELECT 1 FROM reminders WHERE id=?", (reminder_id,)).fetchone():
                    raise ValueError("Unknown reminder.")
                db.execute("UPDATE reminders SET state='done' WHERE id=?", (reminder_id,))
                db.execute("DELETE FROM alerts WHERE reference=?", (reminder_id,))
        elif action == "briefing" and set(body) == {"action"}:
            return {"briefing": self.briefing()}
        elif action == "calendars" and set(body) == {"action"}:
            self.refresh_calendars()
        elif action == "reply_prepare" and set(body) == {"action", "notification", "text"}:
            if inbox is None:
                raise RuntimeError("iMessages are not enabled.")
            return self.prepare_reply(inbox, body["notification"], body["text"])
        elif action == "reply_confirm" and set(body) == {"action", "id", "text", "recipient"}:
            if inbox is None:
                raise RuntimeError("iMessages are not enabled.")
            if not isinstance(body["text"], str) or not isinstance(body["recipient"], str):
                raise ValueError("Confirmation must contain the displayed recipient and text.")
            return self.confirm_reply(body["id"], inbox, expected_text=body["text"],
                                      expected_recipient=body["recipient"])
        elif action == "reply_cancel" and set(body) == {"action", "id"}:
            draft_id = identifier(body["id"])
            with self.lock, self.connect() as db:
                if not db.execute("SELECT 1 FROM drafts WHERE id=?", (draft_id,)).fetchone():
                    raise ValueError("Unknown reply draft.")
                db.execute("UPDATE drafts SET state='cancelled',detail='Not sent' "
                           "WHERE id=? AND state='unconfirmed'", (draft_id,))
        elif action == "reply_edit" and set(body) == {"action", "id", "text"}:
            draft_id = identifier(body["id"])
            text = body["text"]
            if not isinstance(text, str) or not 0 < len(text.strip().encode()) < 2048:
                raise ValueError("Invalid reply draft text.")
            with self.lock, self.connect() as db:
                changed = db.execute("UPDATE drafts SET text=? WHERE id=? AND state='unconfirmed'",
                                     (text.strip(), draft_id)).rowcount
                if changed != 1:
                    raise ValueError("Only an unsent draft can be edited.")
        elif action == "calendar_toggle" and set(body) == {"action", "id", "enabled"}:
            if not isinstance(body["id"], str) or type(body["enabled"]) is not bool:
                raise ValueError("Invalid calendar toggle.")
            settings = self.get("settings")
            ids = settings["calendar_ids"]
            ids = [c for c in ids if c != body["id"]]
            if body["enabled"]:
                ids.append(body["id"])
            self.configure({"calendar_ids": ids})
        elif action == "favourite_toggle" and set(body) == {"action", "id", "enabled"}:
            if body["id"] not in FAVOURITES or type(body["enabled"]) is not bool:
                raise ValueError("Invalid favourite quick action.")
            with self.lock:
                favourites = [f for f in self.get("settings")["favourites"] if f != body["id"]]
                if body["enabled"]:
                    favourites.append(body["id"])
                self.configure({"favourites": favourites})
        else:
            raise ValueError("Unknown companion action or request fields.")
        return self.status()

    def start(self) -> None:
        self.thread = threading.Thread(target=self.run, name="gadget-reminders", daemon=True)
        self.thread.start()

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                self.fire_due(time.time())
                settings = self.get("settings")
                cached_weather = self.get("weather")
                weather_due = settings["weather"] and (
                    cached_weather.get("location") != settings["weather"] or
                    time.time() - cached_weather.get("checked_at", 0) >= (
                        900 if cached_weather["state"] == "ready" else 300))
                if weather_due and (not self.weather_thread or not self.weather_thread.is_alive()):
                    self.weather_thread = threading.Thread(target=self.refresh_weather,
                                                           name="gadget-weather", daemon=True)
                    self.weather_thread.start()
                now = datetime.now().astimezone()
                today = now.strftime("%Y-%m-%d")
                if settings["briefing_enabled"] and (now.hour, now.minute) >= (
                        settings["hour"], settings["minute"]) and self.get("briefing_date") != today:
                    self.put("briefing_date", today)
                    self.briefing(scheduled=True)
            except (OSError, ValueError, RuntimeError, sqlite3.Error):
                logging.error("Companion scheduler unavailable; inspect private state and permissions.")
            self.stop.wait(5)

    def close(self) -> None:
        self.stop.set()
        for thread in (self.thread, self.brief_thread, self.reply_thread, self.weather_thread):
            if thread:
                thread.join(timeout=30)
                if thread.is_alive():
                    logging.error("Companion worker did not stop before shutdown.")
