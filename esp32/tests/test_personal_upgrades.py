# SPDX-License-Identifier: Apache-2.0
from datetime import datetime
import json
import math
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/muse"))
from openclaw_companion import Companion
from openclaw_jobs import JobManager
from openclaw_bridge import BridgeServer
import companion_cli
sys.path.pop(0)


class PersonalUpgradesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        self.companion = Companion(self.state)
        self.jobs = JobManager(self.state, Mock(), threading.Lock(), durable_conversation=True)
        self.turn = "b" * 32
        conversation = self.jobs.personal_status()["conversation"]["id"]
        with self.jobs.connect() as db:
            db.execute("INSERT INTO jobs (id,fingerprint,status,started,updated,detail,reply,request,conversation)"
                       " VALUES (?,?,'completed',?,?,'Done',?,?,?)",
                       (self.turn, "fixture", time.time(), time.time(), "Try a short focus session.",
                        "I prefer short focus sessions.", conversation))

    def tearDown(self):
        self.companion.close()
        self.jobs.close()
        self.tmp.cleanup()

    def save(self, kind, saved_id="c" * 32, text="Review a short focus session"):
        body = {"action": "save_shortcut", "id": saved_id, "turn": self.turn, "kind": kind, "text": text}
        if kind == "reminder":
            body["after_seconds"] = 600
        return self.companion.save_shortcut(body, self.jobs)

    def test_reviewed_saves_are_idempotent_and_undo_each_exact_item(self):
        for index, kind in enumerate(("memory", "reminder", "task")):
            saved_id = "cde"[index] * 32
            result = self.save(kind, saved_id)
            self.assertEqual(result["saved_item"]["id"], saved_id)
            self.assertEqual(result["saved_item"]["state"], "saved")
            self.save(kind, saved_id)
            self.assertEqual(len(self.companion.tasks()), int(kind == "task"))
            self.assertEqual(len(self.companion.reminders()), int(kind == "reminder"))
            self.companion.undo_save(saved_id, self.jobs)
            self.companion.undo_save(saved_id, self.jobs)
            self.assertEqual(self.companion.status()["saved_item"]["state"], "undone")
            self.assertEqual(self.companion.tasks(), [])
            self.assertEqual(self.companion.reminders(), [])
            self.assertEqual(self.jobs.personal_status()["memory_count"], 0)
            # Forgetting intentionally starts a fresh conversation.
            with self.jobs.connect() as db:
                db.execute("UPDATE jobs SET conversation=? WHERE id=?",
                           (self.jobs.personal_status()["conversation"]["id"], self.turn))

    def test_old_turn_rebound_action_and_duplicate_memory_cannot_be_undone_as_new(self):
        self.jobs.memory_add("Review a short focus session")
        result = self.save("memory")
        self.assertFalse(result["saved_item"]["created"])
        with self.assertRaisesRegex(ValueError, "already existed"):
            self.companion.undo_save("c" * 32, self.jobs)
        self.assertEqual(self.jobs.personal_status()["memory_count"], 1)
        with self.assertRaisesRegex(ValueError, "rebound"):
            self.save("memory", text="Different reviewed text")
        self.jobs.reset_conversation()
        with self.assertRaisesRegex(ValueError, "no longer available"):
            self.save("task", "d" * 32)
        self.assertEqual(self.companion.tasks(), [])

    def test_saved_tasks_and_reminders_survive_restart_and_can_be_paged_and_completed(self):
        self.save("task")
        self.save("reminder", "d" * 32)
        self.companion.close()
        self.companion = Companion(self.state)
        self.assertEqual(len(self.companion.tasks()), 1)
        self.assertEqual(len(self.companion.reminders()), 1)
        for index in range(14):
            self.companion.add_task(f"Priority {index}")
        first = self.companion.status()
        self.assertEqual(len(first["tasks"]), 12)
        self.assertTrue(first["task_more"])
        page = self.companion.handle({"action": "task_page", "offset": 12})
        self.assertEqual(len(page["tasks"]), 3)
        self.assertEqual(page["task_offset"], 12)
        task_id = page["tasks"][0]["id"]
        self.companion.handle({"action": "task_done", "id": task_id})
        self.assertEqual(len(self.companion.tasks()), 14)
        self.companion.handle({"action": "task_reopen", "id": task_id})
        self.assertEqual(len(self.companion.tasks()), 15)
        self.companion.handle({"action": "task_delete", "id": task_id})
        self.assertEqual(len(self.companion.tasks()), 14)
        for invalid in (True, 1, -12, 204):
            with self.assertRaises(ValueError):
                self.companion.handle({"action": "task_page", "offset": invalid})

    def test_routines_are_once_per_local_day_bounded_catchup_and_quiet(self):
        self.companion.configure({"briefing_enabled": True, "evening_enabled": True})
        morning = datetime(2026, 10, 5, 8, 0).timestamp()
        evening = datetime(2026, 10, 5, 20, 0).timestamp()
        with patch.object(self.companion, "briefing") as brief, patch.object(self.companion, "evening") as wrap:
            self.companion.fire_routines(morning - 60)
            brief.assert_not_called()
            self.companion.fire_routines(morning)
            self.companion.fire_routines(morning + 300)
            self.assertEqual(brief.call_count, 1)
            self.companion.fire_routines(evening)
            self.companion.fire_routines(evening + 300)
            self.assertEqual(wrap.call_count, 1)
            self.companion.fire_routines(datetime(2026, 10, 6, 10, 0).timestamp())
            self.assertEqual(brief.call_count, 1)
            self.companion.configure({"evening_hour": 22})
            self.companion.fire_routines(datetime(2026, 10, 6, 22, 0).timestamp())
            self.assertEqual(wrap.call_count, 1)
        self.companion.alert("briefing", "Routine", "Private daily summary")
        self.companion.alert("reminder", "Reminder", "Explicit reminder")
        with patch("openclaw_companion.time.time", return_value=datetime(2026, 10, 6, 23, 0).timestamp()):
            delivered = self.companion.poll()["notification"]
            self.assertEqual(delivered["kind"], "reminder")
            self.assertIsNone(self.companion.poll(delivered["id"])["notification"])
        with patch("openclaw_companion.time.time", return_value=datetime(2026, 10, 7, 8, 0).timestamp()):
            self.assertEqual(self.companion.poll()["notification"]["kind"], "briefing")

    def test_local_digests_include_priorities_and_unavailable_calendar_is_explicit(self):
        self.companion.add_task("Prepare a question for tomorrow")
        self.companion.build_briefing(False)
        self.assertIn("Prepare a question", self.companion.get("briefing")["body"])
        self.companion.build_evening(False)
        self.assertIn("Prepare a question", self.companion.get("evening")["body"])
        with patch.object(self.companion, "calendar_query", return_value={"calendars": []}):
            self.companion.configure({"calendars_enabled": True})
        with patch.object(self.companion, "calendar_query", side_effect=RuntimeError("denied")), self.assertLogs(level="ERROR"):
            self.companion.build_evening(False)
        result = self.companion.get("evening")
        self.assertEqual(result["state"], "partial")
        self.assertIn("unavailable", result["body"])
        self.assertLessEqual(len(result["body"].encode()), 2047)

    def test_meeting_notes_are_bound_to_a_live_occurrence_and_used_in_alert(self):
        now = time.time()
        key = "f" * 64
        with patch.object(self.companion, "calendar_query",
                          return_value={"calendars": [{"id": "fixture", "name": "Fixture calendar"}]}):
            self.companion.configure({"calendars_enabled": True, "calendar_alerts_enabled": True,
                                      "calendar_ids": ["fixture"], "meeting_prep_enabled": True,
                                      "quiet_start_hour": 0, "quiet_end_hour": 0})
        self.companion.put("calendar_alerts", {"state": "ready", "checked_at": now, "selection": ["fixture"]})
        with self.companion.connect() as db:
            db.execute("INSERT INTO calendar_events VALUES (?,?,?,?,?,'scheduled',?)",
                       (key, now + 1200, now + 2400, "Planning", "Fixture calendar", now))
        self.companion.handle({"action": "meeting_note", "key": key, "text": "Bring the test results."})
        self.companion.fire_calendar_alerts(now)
        notice = self.companion.poll()["notification"]
        self.assertIn("Bring the test results.", notice["body"])
        self.assertEqual(notice["expires_at"], math.ceil(now + 1200))
        with self.assertRaises(ValueError):
            self.companion.handle({"action": "meeting_note", "key": "0" * 64, "text": "Not this meeting"})
        with patch("openclaw_companion.time.time", return_value=now + 1201):
            self.assertIsNone(self.companion.poll()["notification"])
        with self.companion.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM meeting_notes").fetchone()[0], 0)

    def test_bonjour_resolves_only_private_addresses_and_keeps_certificate_identity(self):
        (self.state / "bridge.json").write_text(json.dumps({"device_token": "test-token" * 4}))
        self.companion.put("endpoint", "https://test-mac.local:8765/v1/companion")
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.42", 8765))]
        connection = Mock()
        response = Mock(status=200)
        response.read.return_value = b'{"ok":true}'
        connection.getresponse.return_value = response
        context = Mock()
        raw = Mock()
        with patch("companion_cli.socket.getaddrinfo", return_value=addresses), \
                patch("companion_cli.socket.create_connection", return_value=raw) as connect, \
                patch("companion_cli.ssl.create_default_context", return_value=context), \
                patch("companion_cli.http.client.HTTPConnection", return_value=connection):
            self.assertEqual(companion_cli.call({"action": "status"}, self.state), {"ok": True})
            connect.assert_called_once_with(("192.168.1.42", 8765), timeout=30)
            context.wrap_socket.assert_called_once_with(raw, server_hostname="muse-openclaw.local")
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 8765))]
        with patch("companion_cli.socket.getaddrinfo", return_value=public), \
                patch("companion_cli.socket.create_connection") as connect, \
                patch("companion_cli.ssl.create_default_context"):
            with self.assertRaisesRegex(ValueError, "private"):
                companion_cli.call({"action": "status"}, self.state)
            connect.assert_not_called()

    def test_dhcp_bridge_rejects_non_private_clients(self):
        server = BridgeServer.__new__(BridgeServer)
        server.private_clients = True
        for address in ("127.0.0.1", "192.168.1.2", "10.0.0.2", "172.16.1.2"):
            self.assertTrue(server.verify_request(Mock(), (address, 8765)))
        with self.assertLogs(level="WARNING"):
            self.assertFalse(server.verify_request(Mock(), ("8.8.8.8", 8765)))

    def test_failed_save_receipt_rolls_back_all_target_types(self):
        import sqlite3
        with self.companion.connect() as db:
            db.execute("CREATE TRIGGER reject_save BEFORE INSERT ON saved_items "
                       "BEGIN SELECT RAISE(ABORT,'receipt failure'); END")
        for kind in ("memory", "task", "reminder"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.save(kind)
            self.assertEqual(self.jobs.personal_status()["memory_count"], 0)
            self.assertEqual(self.companion.tasks(), [])
            self.assertEqual(self.companion.reminders(), [])

    def test_failed_undo_receipt_preserves_memory_and_can_retry(self):
        import sqlite3
        self.save("memory")
        conversation = self.jobs.personal_status()["conversation"]["id"]
        with self.companion.connect() as db:
            db.execute("CREATE TRIGGER reject_undo BEFORE UPDATE ON saved_items "
                       "BEGIN SELECT RAISE(ABORT,'receipt failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.companion.undo_save("c" * 32, self.jobs)
        self.assertEqual(self.jobs.personal_status()["memory_count"], 1)
        self.assertEqual(self.jobs.personal_status()["conversation"]["id"], conversation)
        with self.companion.connect() as db:
            db.execute("DROP TRIGGER reject_undo")
        self.companion.undo_save("c" * 32, self.jobs)
        self.assertEqual(self.jobs.personal_status()["memory_count"], 0)
