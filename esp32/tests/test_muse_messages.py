# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "tools/muse/openclaw_messages.py"
spec = importlib.util.spec_from_file_location("muse_messages", SOURCE)
assert spec and spec.loader
messages = importlib.util.module_from_spec(spec)
spec.loader.exec_module(messages)


class InboxTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "chat.db"
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("CREATE TABLE message (ROWID INTEGER PRIMARY KEY, service TEXT, "
                       "is_from_me INTEGER, associated_message_type INTEGER, is_system_message INTEGER)")
            db.execute("INSERT INTO message VALUES (100, 'iMessage', 0, 0, 0)")
        self.inbox = messages.MessageInbox(self.root, messages_db=self.source)
        self.inbox.error = None

    def tearDown(self) -> None:
        self.inbox.close()
        self.tmp.cleanup()

    def add(self, rowid: int, *, service: str = "iMessage", outgoing: int = 0,
            reaction: int = 0, system: int = 0, text: str = "Hello") -> dict:
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("INSERT INTO message VALUES (?, ?, ?, ?, ?)",
                       (rowid, service, outgoing, reaction, system))
        return {"id": rowid, "sender": "Test sender", "text": text}

    def test_initial_baseline_skips_history_and_queue_survives_restart(self) -> None:
        self.inbox.ingest({"id": 100})
        self.assertEqual(self.inbox.poll(""), {"notification": None})
        self.inbox.ingest(self.add(101))
        first = self.inbox.poll("")["notification"]
        restarted = messages.MessageInbox(self.root, messages_db=self.source)
        restarted.error = None
        self.assertEqual(restarted.poll("")["notification"], first)
        self.assertEqual(restarted.cursor(), 101)
        self.assertEqual((self.root / "messages.sqlite").stat().st_mode & 0o777, 0o600)
        restarted.close()

    def group_fixture(self) -> str:
        self.inbox.ingest(self.add(101))
        notification = self.inbox.poll("")["notification"]["id"]
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("CREATE TABLE chat (ROWID INTEGER PRIMARY KEY,guid TEXT,service_name TEXT,"
                       "display_name TEXT,chat_identifier TEXT)")
            db.execute("CREATE TABLE chat_message_join (chat_id INTEGER,message_id INTEGER)")
            db.execute("CREATE TABLE handle (ROWID INTEGER PRIMARY KEY,id TEXT)")
            db.execute("CREATE TABLE chat_handle_join (chat_id INTEGER,handle_id INTEGER)")
            db.execute("INSERT INTO chat VALUES (1,'iMessage;+;fixture-group','iMessage','Fixture group','group')")
            db.execute("INSERT INTO chat_message_join VALUES (1,101)")
            db.executemany("INSERT INTO handle VALUES (?,?)", [(1, "+15555550100"), (2, "fixture@example.invalid")])
            db.executemany("INSERT INTO chat_handle_join VALUES (1,?)", [(1,), (2,)])
        return notification

    def test_reply_uses_exact_group_guid_and_all_participants_not_preview_sender(self) -> None:
        target = self.inbox.reply_target(self.group_fixture())
        self.assertEqual(target, {"chat_guid": "iMessage;+;fixture-group",
                                 "recipient": "Fixture group: +15555550100; fixture@example.invalid"})
        self.assertNotIn("Test sender", target["recipient"])

    def test_ambiguous_or_missing_group_participants_are_never_guessed(self) -> None:
        notification = self.group_fixture()
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("INSERT INTO chat VALUES (2,'iMessage;+;other','iMessage','','other')")
            db.execute("INSERT INTO chat_message_join VALUES (2,101)")
        with self.assertRaisesRegex(ValueError, "safely resolved"):
            self.inbox.reply_target(notification)
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("DELETE FROM chat_message_join WHERE chat_id=2")
            db.execute("DELETE FROM handle WHERE ROWID=2")
        with self.assertRaisesRegex(ValueError, "participants"):
            self.inbox.reply_target(notification)

    def test_sms_conversation_is_rejected_even_for_an_imessage_preview(self) -> None:
        notification = self.group_fixture()
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("UPDATE chat SET service_name='SMS',guid='SMS;-;fixture'")
        with self.assertRaisesRegex(ValueError, "safely resolved"):
            self.inbox.reply_target(notification)

    def test_filters_outgoing_sms_reactions_and_system_messages_without_replaying(self) -> None:
        for rowid, options in ((101, {"outgoing": 1}), (102, {"service": "SMS"}),
                               (103, {"reaction": 2000}), (104, {"system": 1})):
            self.inbox.ingest(self.add(rowid, **options))
        self.assertEqual(self.inbox.cursor(), 104)
        self.assertEqual(self.inbox.poll(""), {"notification": None})
        self.inbox.ingest(self.add(105))
        self.assertEqual(self.inbox.poll("")["notification"]["sender"], "Test sender")

    def test_ack_is_ordered_idempotent_and_does_not_drop_the_next_notification(self) -> None:
        self.inbox.ingest(self.add(101, text="First"))
        self.inbox.ingest(self.add(102, text="Second"))
        first = self.inbox.poll("")["notification"]
        second = self.inbox.poll(first["id"])["notification"]
        self.assertEqual(second["preview"], "Second")
        self.assertEqual(self.inbox.poll(first["id"])["notification"], second)
        with self.assertRaises(ValueError):
            self.inbox.poll("f" * 32)
        self.assertEqual(self.inbox.poll("")["notification"], second)
        self.assertEqual(self.inbox.poll(second["id"]), {"notification": None})
        self.assertEqual(self.inbox.poll(second["id"]), {"notification": None})

    def test_ack_commits_even_if_watcher_is_unavailable_and_can_be_retried(self) -> None:
        self.inbox.ingest(self.add(101))
        event = self.inbox.poll("")["notification"]
        self.inbox.error = "Watcher is unavailable."
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            self.inbox.poll(event["id"])
        self.inbox.error = None
        self.assertEqual(self.inbox.poll(event["id"]), {"notification": None})

    def test_utf8_preview_limits_and_attachment_placeholder(self) -> None:
        self.inbox.ingest(self.add(101, text="\u00e9" * 300))
        first = self.inbox.poll("")["notification"]
        self.assertLessEqual(len(first["preview"].encode()), 256)
        self.assertTrue(first["preview"].endswith("..."))
        self.assertNotIn("\ufffd", first["preview"])
        self.inbox.ingest(self.add(102, text=""))
        self.assertEqual(self.inbox.poll(first["id"])["notification"]["preview"],
                         "[Attachment or non-text message]")

    def test_full_queue_pauses_cursor_instead_of_silently_losing_messages(self) -> None:
        for rowid in range(101, 301):
            self.inbox.ingest(self.add(rowid))
        record = self.add(301)
        with self.assertRaisesRegex(RuntimeError, "queue is full"):
            self.inbox.ingest(record)
        self.assertEqual(self.inbox.cursor(), 300)
        event = self.inbox.poll("")["notification"]
        self.inbox.poll(event["id"])
        self.inbox.ingest(record)
        self.assertEqual(self.inbox.cursor(), 301)

    def test_invalid_event_or_ack_is_an_error_without_advancing_cursor(self) -> None:
        for event in ([], {}, {"id": True}, {"id": -1}):
            with self.assertRaises(ValueError):
                self.inbox.ingest(event)
        event = self.add(101)
        event["sender"] = None
        with self.assertRaises(ValueError):
            self.inbox.ingest(event)
        self.assertEqual(self.inbox.cursor(), 100)
        for ack in (None, 1, "bad", "G" * 32):
            with self.assertRaises(ValueError):
                self.inbox.poll(ack)

    def test_real_watcher_subprocess_is_stopped_and_event_is_persisted(self) -> None:
        event = self.add(101)
        executable = self.root / "fake-imsg"
        executable.write_text(f"#!{sys.executable}\n"
                              "import time\n"
                              f"print({json.dumps(json.dumps(event))}, flush=True)\n"
                              "time.sleep(30)\n")
        os.chmod(executable, 0o700)
        self.inbox.executable = str(executable)
        self.inbox.start()
        deadline = time.monotonic() + 3
        while self.inbox.cursor() < 101 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.inbox.cursor(), 101)
        process = self.inbox.process
        self.assertIsNotNone(process)
        self.assertEqual(self.inbox.poll("")["notification"]["preview"], "Hello")
        self.inbox.close()
        self.assertIsNotNone(process.poll())
        self.assertFalse(self.inbox.thread.is_alive())


if __name__ == "__main__":
    unittest.main()
