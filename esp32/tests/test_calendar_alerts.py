# SPDX-License-Identifier: Apache-2.0
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/muse"))
from openclaw_companion import Companion
sys.path.pop(0)


class CalendarAlertsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Companion(Path(self.tmp.name))
        self.now = 1700000000.0
        self.clock = patch("openclaw_companion.time.time", side_effect=lambda: self.now)
        self.clock.start()
        self.events = [self.event()]
        self.reader = patch.object(self.store, "calendar_query", side_effect=self.query)
        self.reader.start()
        self.store.configure({"calendars_enabled": True, "calendar_ids": ["selected"],
                              "calendar_alerts_enabled": True, "calendar_lead_minutes": 30,
                              "quiet_start_hour": 0, "quiet_end_hour": 0})

    def tearDown(self):
        self.reader.stop()
        self.clock.stop()
        self.store.close()
        self.tmp.cleanup()

    def event(self, **changes):
        return {"id": "event-1", "calendar_id": "selected", "calendar": "Test calendar",
                "title": "CALENDAR_ACCEPTANCE_TEST", "start": self.now + 1800,
                "end": self.now + 3600, "all_day": False, "cancelled": False, **changes}

    def query(self, command, query=None):
        if command == "calendars":
            return {"calendars": [{"id": "selected", "name": "Test calendar"}]}
        self.assertEqual(command, "events")
        self.assertEqual(query["ids"], ["selected"])
        self.assertEqual(query["end"] - query["start"], 86400)
        return {"events": self.events}

    def refresh(self):
        self.assertEqual(self.store.refresh_calendar_alerts()["state"], "ready")

    def test_exact_lead_boundary_dedup_and_restart(self):
        self.refresh()
        self.store.fire_calendar_alerts(self.now - 0.01)
        self.assertIsNone(self.store.poll()["notification"])
        self.store.fire_calendar_alerts(self.now)
        first = self.store.poll()["notification"]
        self.assertEqual(first["kind"], "calendar")
        self.assertEqual(first["expires_at"], self.events[0]["start"])
        self.store.fire_calendar_alerts(self.now)
        self.assertEqual(self.store.poll()["notification"]["id"], first["id"])
        self.store.close()
        self.store = Companion(Path(self.tmp.name))
        self.store.fire_calendar_alerts(self.now)
        self.assertEqual(self.store.poll()["notification"]["id"], first["id"])

    def test_cancelled_rescheduled_and_all_day_events_never_create_obsolete_alerts(self):
        self.events = [self.event(start=self.now + 3600)]
        self.refresh()
        self.events = [self.event(start=self.now + 5400, end=self.now + 7200),
                       self.event(id="all-day", all_day=True),
                       self.event(id="cancelled", cancelled=True)]
        self.refresh()
        self.now += 1800
        self.refresh()
        self.store.fire_calendar_alerts(self.now)
        self.assertIsNone(self.store.poll()["notification"])
        self.now += 1800
        self.refresh()
        self.store.fire_calendar_alerts(self.now)
        self.assertEqual(self.store.poll()["notification"]["expires_at"], 1700005400)

    def test_expiry_and_cancellation_preserve_cached_ack_ownership(self):
        self.refresh()
        self.store.fire_calendar_alerts(self.now)
        first = self.store.poll()["notification"]
        self.store.alert("job", "Unrelated test", "Do not delete this alert")
        self.events = []
        self.refresh()
        self.assertTrue(self.store.owns_ack(first["id"]))
        self.assertEqual(self.store.poll(first["id"])["notification"]["kind"], "job")
        self.assertEqual(self.store.poll(first["id"])["notification"]["kind"], "job")
        with self.assertRaises(ValueError):
            self.store.poll("f" * 32)

    def test_event_start_expiry_is_durable_even_if_device_was_offline(self):
        self.refresh()
        self.store.fire_calendar_alerts(self.now)
        first = self.store.poll()["notification"]
        self.now = self.events[0]["start"]
        self.assertIsNone(self.store.poll()["notification"])
        self.assertTrue(self.store.owns_ack(first["id"]))
        self.assertIsNone(self.store.poll(first["id"])["notification"])

    def test_snooze_has_one_new_alert_and_cannot_outlive_event_start(self):
        self.refresh()
        self.store.fire_calendar_alerts(self.now)
        first = self.store.poll()["notification"]
        with self.assertRaises(ValueError):
            self.store.snooze(first["id"], 1800)
        self.store.snooze(first["id"], 300)
        self.assertIsNone(self.store.poll(first["id"])["notification"])
        self.now += 299
        self.refresh()
        self.store.fire_calendar_alerts(self.now)
        self.assertIsNone(self.store.poll()["notification"])
        self.now += 1
        self.store.fire_calendar_alerts(self.now)
        next_alert = self.store.poll()["notification"]
        self.assertNotEqual(first["id"], next_alert["id"])
        self.assertEqual(self.store.poll(first["id"])["notification"]["id"], next_alert["id"])

    def test_stale_or_unavailable_agenda_does_not_generate_alerts(self):
        self.refresh()
        self.now += 300
        self.assertEqual(self.store.status()["calendar_alerts"]["state"], "stale")
        self.store.fire_calendar_alerts(self.now)
        self.assertIsNone(self.store.poll()["notification"])
        self.events[0]["start"] = float("nan")
        with self.assertLogs(level="ERROR"):
            self.assertEqual(self.store.refresh_calendar_alerts()["state"], "unavailable")
        self.store.fire_calendar_alerts(self.now)
        self.assertIsNone(self.store.poll()["notification"])

    def test_disabling_alerts_removes_pending_calendar_alerts_without_breaking_ack(self):
        self.refresh()
        self.store.fire_calendar_alerts(self.now)
        first = self.store.poll()["notification"]
        self.store.configure({"calendar_alerts_enabled": False})
        self.store.fire_calendar_alerts(self.now)
        self.assertEqual(self.store.status()["calendar_alerts"]["state"], "disabled")
        self.assertIsNone(self.store.poll(first["id"])["notification"])

    def test_quiet_hours_and_preferences_are_exact(self):
        settings = {"quiet_start_hour": 22, "quiet_end_hour": 8}
        for hour, minute, expected in ((21, 59, False), (22, 0, True), (0, 0, True),
                                       (7, 59, True), (8, 0, False)):
            stamp = datetime(2026, 1, 1, hour, minute).timestamp()
            self.assertEqual(Companion.quiet_time(stamp, settings), expected)
        self.assertFalse(Companion.quiet_time(self.now, {"quiet_start_hour": 0, "quiet_end_hour": 0}))
        for changes in ({"calendar_lead_minutes": 0}, {"calendar_lead_minutes": 121},
                        {"calendar_lead_minutes": True}, {"quiet_start_hour": 24},
                        {"quiet_end_hour": -1}, {"calendar_alerts_enabled": "yes"}):
            with self.assertRaises(ValueError):
                self.store.configure(changes)

    def test_quiet_hours_suppress_new_calendar_alerts_but_not_manual_reminders(self):
        self.refresh()
        self.store.add_reminder("Explicit test reminder", self.now + 1)
        self.now += 1
        with patch.object(self.store, "quiet_time", return_value=True):
            self.store.fire_calendar_alerts(self.now)
        self.store.fire_due(self.now)
        self.assertEqual(self.store.poll()["notification"]["kind"], "reminder")
