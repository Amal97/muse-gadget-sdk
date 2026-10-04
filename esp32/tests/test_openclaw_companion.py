# SPDX-License-Identifier: Apache-2.0
import json
import io
from pathlib import Path
import sys
import tempfile
import time
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/muse"))
from openclaw_companion import Companion
sys.path.pop(0)


class CompanionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        self.store = Companion(self.state)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def weather_fixture(self):
        self.store.configure({"weather": {"label": "Test city", "latitude": -36.85, "longitude": 174.76}})
        return {"current": {"time": int(time.time()), "temperature_2m": 18.5, "weather_code": 2, "is_day": 1},
                "daily": {"temperature_2m_min": [12.2], "temperature_2m_max": [21.4],
                          "precipitation_probability_max": [25]}}

    def test_home_weather_fetch_is_cached_persisted_and_status_is_network_free(self):
        data = self.weather_fixture()
        with patch("openclaw_companion.urlopen", return_value=io.BytesIO(json.dumps(data).encode())) as fetch:
            result = self.store.refresh_weather()
            self.assertEqual(result["state"], "ready")
            self.assertEqual(result["temperature"], 18.5)
            self.assertIn("current=temperature_2m%2Cweather_code%2Cis_day", fetch.call_args.args[0])
            self.store.status()
            self.store.build_briefing(False)
            self.assertEqual(fetch.call_count, 1)
        self.store.close()
        self.store = Companion(self.state)
        with patch("openclaw_companion.urlopen") as fetch:
            status = self.store.status()
            self.assertEqual(status["weather"]["temperature"], 18.5)
            self.assertIsInstance(status["clock"]["utc_offset_seconds"], int)
            self.assertTrue(-50400 <= status["clock"]["utc_offset_seconds"] <= 50400)
            self.assertIn("summary", status["briefing"])
            fetch.assert_not_called()

    def test_home_weather_failure_retains_explicitly_stale_measurements_and_retries_are_bounded(self):
        data = self.weather_fixture()
        with patch("openclaw_companion.urlopen", return_value=io.BytesIO(json.dumps(data).encode())):
            original = self.store.refresh_weather()
        with patch("openclaw_companion.time.time", return_value=original["checked_at"] + 901), \
                patch("openclaw_companion.urlopen", side_effect=OSError("offline")) as fetch, \
                self.assertLogs(level="ERROR"):
            stale = self.store.refresh_weather()
            self.assertEqual(stale["state"], "stale")
            self.assertEqual(stale["updated_at"], original["updated_at"])
            self.assertEqual(stale["temperature"], original["temperature"])
            self.store.refresh_weather()
            self.assertEqual(fetch.call_count, 1)
        self.store.configure({"weather": {"label": "Another city", "latitude": 0, "longitude": 0}})
        self.assertEqual(self.store.status()["weather"]["state"], "pending")
        self.assertNotIn("temperature", self.store.status()["weather"])
        self.store.configure({"weather": None})
        self.assertEqual(self.store.status()["weather"], {"state": "not_configured"})

    def test_home_weather_invalid_or_oversized_measurements_are_not_success(self):
        for field, value in (("temperature_2m", True), ("temperature_2m", float("nan")),
                             ("weather_code", 2.5), ("time", 0), ("is_day", 3)):
            with self.subTest(field=field, value=value):
                data = self.weather_fixture()
                self.store.put("weather", {"state": "pending"})
                data["current"][field] = value
                with patch("openclaw_companion.urlopen", return_value=io.BytesIO(json.dumps(data).encode())), \
                        self.assertLogs(level="ERROR"):
                    self.assertEqual(self.store.refresh_weather()["state"], "unavailable")
        with patch("openclaw_companion.urlopen", return_value=io.BytesIO(b" " * 65537)), self.assertLogs(level="ERROR"):
            self.store.put("weather", {"state": "pending"})
            self.assertEqual(self.store.refresh_weather()["state"], "unavailable")

    def test_home_weather_age_and_briefing_agenda_preview(self):
        data = self.weather_fixture()
        with patch("openclaw_companion.urlopen", return_value=io.BytesIO(json.dumps(data).encode())):
            original = self.store.refresh_weather()
        with patch("openclaw_companion.time.time", return_value=original["updated_at"] + 1201):
            self.assertEqual(self.store.status()["weather"]["state"], "stale")
        with patch.object(self.store, "calendar_query", return_value={
                "calendars": [{"id": "test", "name": "Test"}]}):
            self.store.configure({"calendars_enabled": True, "calendar_ids": ["test"]})
        with patch.object(self.store, "calendar_query", return_value={"events": [
                {"all_day": True, "title": "Planning " + "é" * 300, "calendar": "Test", "start": time.time()}]}):
            self.store.build_briefing(False)
        briefing = self.store.status()["briefing"]
        self.assertEqual(briefing["event_count"], 1)
        self.assertTrue(briefing["summary"].startswith("All day Planning"))
        self.assertLessEqual(len(briefing["summary"].encode()), 256)

    def test_home_summary_does_not_claim_disabled_calendars_are_empty(self):
        self.store.build_briefing(False)
        self.assertIn("Calendars disabled", self.store.status()["briefing"]["summary"])
        self.assertIsNone(self.store.status()["briefing"]["event_count"])

    def test_calendar_reader_uses_authorized_app_and_private_exchange(self):
        def launch(arguments, **kwargs):
            self.assertEqual(arguments[:2], ["/usr/bin/open", "-n"])
            self.assertNotIn("-W", arguments)
            request = Path(arguments[-1])
            self.assertEqual(request.stat().st_mode & 0o777, 0o600)
            self.assertEqual(request.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(json.loads(request.read_text()), {"command": "status", "query": None})
            (request.parent / "response.json").write_text('{"authorized":true}')
            return SimpleNamespace(returncode=0)
        with patch("openclaw_companion.subprocess.run", side_effect=launch):
            self.assertTrue(self.store.calendar_query("status")["authorized"])
        with patch("openclaw_companion.subprocess.run", return_value=SimpleNamespace(returncode=0)), \
                patch("openclaw_companion.time.monotonic", side_effect=[0, 16]):
            with self.assertRaisesRegex(RuntimeError, "did not respond"):
                self.store.calendar_query("status")

    def test_calendar_exchange_waits_for_response_after_early_launch_return(self):
        workers = []
        def launch(arguments, **kwargs):
            response = Path(arguments[-1]).parent / "response.json"
            def publish():
                if response.parent.exists():
                    response.write_text('{"authorized":true,"authorization":3}')
            worker = threading.Timer(0.1, publish)
            workers.append(worker)
            worker.start()
            return SimpleNamespace(returncode=0)
        try:
            with patch("openclaw_companion.subprocess.run", side_effect=launch):
                self.assertTrue(self.store.calendar_query("status")["authorized"])
        finally:
            for worker in workers:
                worker.join()

    def test_stale_confirmation_never_sends_and_keeps_draft_unconfirmed(self):
        inbox = Mock(executable="/test/imsg")
        inbox.reply_target.return_value = {"chat_guid": "iMessage;-;exact", "recipient": "+15555550100"}
        draft = self.store.prepare_reply(inbox, "a" * 32, "Hello.")["draft"]
        with patch("openclaw_companion.subprocess.run") as send:
            for text, recipient in (("Changed.", draft["recipient"]), (draft["text"], "+15555550101")):
                with self.assertRaisesRegex(ValueError, "changed"):
                    self.store.handle({"action": "reply_confirm", "id": draft["id"],
                                       "text": text, "recipient": recipient}, inbox)
            send.assert_not_called()
        self.assertEqual(self.store.status()["draft"]["state"], "unconfirmed")

    def test_calendar_selection_is_explicit_persistent_and_individually_toggleable(self):
        calendars = {"calendars": [{"id": "home-id", "name": "Home"}, {"id": "work-id", "name": "Work"}]}
        with patch.object(self.store, "calendar_query", return_value=calendars):
            self.store.configure({"calendars_enabled": True, "calendar_ids": "all"})
            self.assertEqual(self.store.get("settings")["calendar_ids"], ["home-id", "work-id"])
            self.store.handle({"action": "calendar_toggle", "id": "work-id", "enabled": False})
            self.assertEqual(self.store.get("settings")["calendar_ids"], ["home-id"])
            self.assertEqual([c["name"] for c in self.store.status()["calendars"]], ["Home", "Work"])
            self.store.configure({"calendars_enabled": False})
        self.store.close()
        self.store = Companion(self.state)
        self.assertFalse(self.store.get("settings")["calendars_enabled"])
        self.assertEqual(self.store.get("settings")["calendar_ids"], ["home-id"])
        self.assertEqual([c["name"] for c in self.store.status()["calendars"]], ["Home", "Work"])
        with patch.object(self.store, "calendar_query") as reader:
            self.store.build_briefing(False)
            reader.assert_not_called()
        self.assertIn("Calendars disabled", self.store.get("briefing")["body"])

    def test_reminder_queue_snooze_and_ack_are_durable_and_idempotent(self):
        now = time.time()
        reminder = self.store.add_reminder("Stretch", now + 60)
        self.store.fire_due(now + 61)
        alert = self.store.poll()["notification"]
        self.assertEqual(alert["kind"], "reminder")
        self.store.snooze(alert["id"], 300)
        self.assertIsNone(self.store.poll(alert["id"])["notification"])
        self.assertGreater(self.store.reminders()[0]["due"], now + 290)
        self.store.close()
        self.store = Companion(self.state)
        self.store.fire_due(now + 400)
        alert = self.store.poll()["notification"]
        self.assertEqual(self.store.reminders()[0]["id"], reminder["id"])
        self.store.poll(alert["id"])
        self.store.poll(alert["id"])
        self.assertEqual(self.store.reminders(), [])

    def test_full_queue_does_not_lose_due_reminders(self):
        self.store.add_reminder("Keep me", time.time() + 1)
        with self.store.connect() as db:
            db.executemany("INSERT INTO alerts (id,kind,title,body,reference) VALUES (?,'job','Test','Test','')",
                           [(f"{i:032x}",) for i in range(200)])
        with self.assertRaises(RuntimeError):
            self.store.fire_due(time.time() + 2)
        self.assertEqual(self.store.reminders()[0]["state"], "pending")

    def test_draft_requires_confirmation_and_sends_to_bound_guid_once(self):
        inbox = Mock()
        inbox.executable = "/test/imsg"
        inbox.reply_target.return_value = {"chat_guid": "iMessage;+;exact-group",
                                          "recipient": "Group: +15555550100; +15555550101"}
        with patch("openclaw_companion.subprocess.run", return_value=SimpleNamespace(
                returncode=0, stdout='{"status":"sent"}')) as send:
            draft = self.store.prepare_reply(inbox, "a" * 32, "Hello group.")["draft"]
            send.assert_not_called()
            self.assertEqual(draft["state"], "unconfirmed")
            self.store.confirm_reply(draft["id"], inbox)
            self.store.reply_thread.join(timeout=2)
            self.store.confirm_reply(draft["id"], inbox)
            self.assertEqual(send.call_count, 1)
            arguments = send.call_args.args[0]
            self.assertIn("--chat-guid", arguments)
            self.assertIn("iMessage;+;exact-group", arguments)
            self.assertIn("--no-sms-fallback", arguments)
            self.assertNotIn("--to", arguments)
            self.assertEqual(self.store.status()["draft"]["state"], "sent")

    def test_uncertain_send_and_restart_are_not_retried(self):
        inbox = Mock(executable="/test/imsg")
        inbox.reply_target.return_value = {"chat_guid": "iMessage;-;exact", "recipient": "+15555550100"}
        draft = self.store.prepare_reply(inbox, "b" * 32, "Hello.")["draft"]
        with patch("openclaw_companion.subprocess.run", return_value=SimpleNamespace(
                returncode=0, stdout='{"unexpected":"result"}')) as send, self.assertLogs(level="ERROR"):
            self.store.confirm_reply(draft["id"], inbox)
            self.store.reply_thread.join(timeout=2)
            self.store.confirm_reply(draft["id"], inbox)
            self.assertEqual(send.call_count, 1)
        self.assertEqual(self.store.status()["draft"]["state"], "uncertain")
        with self.store.connect() as db:
            db.execute("UPDATE drafts SET state='sending'")
        self.store.close()
        self.store = Companion(self.state)
        self.assertEqual(self.store.status()["draft"]["state"], "uncertain")

    def test_briefing_is_deterministic_and_unavailable_sources_are_explicit(self):
        self.store.configure({"weather": {"label": "Test city", "latitude": -36.85, "longitude": 174.76}})
        with patch("openclaw_companion.urlopen", side_effect=OSError("Offline")), self.assertLogs(level="ERROR"):
            self.store.build_briefing(True)
        result = self.store.get("briefing")
        self.assertEqual(result["state"], "partial")
        self.assertIn("Weather unavailable", result["body"])
        self.assertEqual(self.store.poll()["notification"]["kind"], "briefing")
        self.assertLess(len(result["body"].encode()), 2048)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_bad_settings_intervals_and_unknown_fields_are_rejected(self):
        for changes in ({"hour": 24}, {"minute": True}, {"calendars_enabled": 1},
                        {"weather": {"label": "City", "latitude": float("nan"), "longitude": 0}},
                        {"favourites": ["invented"]}, {"unknown": True}):
            with self.assertRaises(ValueError):
                self.store.configure(changes)
        for seconds in (False, 0, -1, "60"):
            with self.assertRaises(ValueError):
                self.store.handle({"action": "reminder", "title": "Test", "after_seconds": seconds})
        with self.assertRaises(ValueError):
            self.store.handle({"action": "briefing", "extra": "field"})
        self.assertFalse(self.store.get("settings")["briefing_enabled"])
        self.assertFalse(self.store.get("settings")["calendars_enabled"])
