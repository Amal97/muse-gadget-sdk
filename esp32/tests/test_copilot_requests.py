# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import concurrent.futures
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools/muse"
sys.path.insert(0, str(TOOLS))
import copilot_requests as copilot
sys.path.pop(0)


class CopilotRequestsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        self.now = 1700000000
        self.clock = patch.object(copilot.time, "time", side_effect=lambda: self.now)
        self.clock.start()
        self.store = copilot.CopilotRequests(self.state)
        self.controller = "a" * 32
        self.store.controller({"action": "open", "controller": self.controller, "workspace": "/safe/project"})

    def tearDown(self):
        self.clock.stop()
        self.tmp.cleanup()

    def create(self, request_id="b" * 32, **fields):
        body = {"action": "create", "controller": self.controller, "id": request_id,
                "session": "test-session", "kind": "permission", "body": "Workspace: /safe/project\nprintf harmless",
                "choices": [], "allow_freeform": False, "respondable": True, **fields}
        return self.store.controller(body)

    def voice(self, answer, request_id="b" * 32):
        return self.store.device({"action": "copilot_voice", "id": request_id, "text": answer})

    def take(self, request_id="b" * 32):
        return self.store.controller({"action": "take", "controller": self.controller, "id": request_id})

    def test_notification_shape_complete_body_and_preview_byte_budget(self):
        content = "x" * 2047
        self.create(body=content)
        notice = self.store.poll()["notification"]
        self.assertEqual(notice["kind"], "copilot_allow")
        self.assertEqual(notice["body"], content)
        self.assertEqual(len(notice["preview"].encode()), 256)
        self.assertTrue(notice["respondable"])
        self.assertEqual(notice["expires_at"], self.now + 600)
        self.assertTrue(self.store.owns_ack(notice["id"]))
        self.assertEqual(notice["choices"], [])
        self.assertFalse(notice["allow_freeform"])

    def test_touch_choices_preserve_labels_and_validate_request_scope(self):
        self.create(kind="question", choices=["Brief", "Detailed", "Third"], allow_freeform=True)
        notice = self.store.poll()["notification"]
        self.assertEqual(notice["choices"], ["Brief", "Detailed", "Third"])
        self.assertTrue(notice["allow_freeform"])
        for option in (0, 4, -1, True, 2.0, "2"):
            with self.assertRaises(ValueError):
                self.store.device({"action": "copilot_choice", "id": "b" * 32, "option": option})
        with self.assertLogs(level="INFO") as logs:
            self.store.device({"action": "copilot_choice", "id": "b" * 32, "option": 2})
        self.assertIn("(touch)", logs.output[0])
        self.assertEqual(self.take()["result"], {"answer": "Detailed", "wasFreeform": False})
        with self.assertRaises(ValueError):
            self.store.device({"action": "copilot_choice", "id": "b" * 32, "option": 1})

    def test_touch_cannot_approve_permissions_or_answer_opaque_questions(self):
        self.create()
        with self.assertRaises(ValueError):
            self.store.device({"action": "copilot_choice", "id": "b" * 32, "option": 1})
        self.create("c" * 32, kind="question", choices=["Brief"], allow_freeform=True, respondable=False)
        self.store.poll("b" * 32)
        notice = self.store.poll()["notification"]
        self.assertEqual(notice["choices"], [])
        self.assertFalse(notice["allow_freeform"])
        with self.assertRaises(ValueError):
            self.store.device({"action": "copilot_choice", "id": "c" * 32, "option": 1})

    def test_only_explicit_approval_returns_approve_once_and_consumes_once(self):
        self.create()
        for answer in ("yes", "maybe", "don't approve", "approve?", "always allow", "approve and run the next command"):
            with self.assertRaises(ValueError):
                self.voice(answer)
            self.assertEqual(self.take()["state"], "pending")
        result = self.voice("Approve.")
        self.assertEqual(result["copilot"]["state"], "answered")
        self.assertEqual(self.take()["result"], {"kind": "approve-once"})
        self.assertEqual(self.take(), {"id": "b" * 32, "state": "consumed"})
        with self.assertRaises(ValueError):
            self.voice("approve")
        self.assertIsNone(self.store.poll()["notification"])

    def test_rejection_and_duplicate_late_ack_never_replays_work(self):
        self.create()
        self.store.poll("b" * 32)
        self.assertEqual(self.take()["result"]["kind"], "reject")
        self.store.poll("b" * 32)
        self.assertEqual(self.take()["state"], "consumed")

    def test_first_valid_device_or_desktop_response_wins_atomically(self):
        self.create()
        def answer(index):
            try:
                if index % 2:
                    return self.voice("approve")
                return self.store.controller({"action": "answer", "controller": self.controller,
                                              "id": "b" * 32, "text": "deny"})
            except ValueError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(answer, range(24)))
        self.assertEqual(sum(value is not None for value in results), 1)
        self.assertIn(self.take()["result"]["kind"], ("approve-once", "reject"))

    def test_ids_cannot_be_rebound_or_taken_by_a_different_session_owner(self):
        self.create()
        self.assertEqual(self.create()["state"], "pending")
        with self.assertRaises(ValueError):
            self.create(body="different work")
        other = "c" * 32
        self.store.controller({"action": "open", "controller": other, "workspace": "/another/project"})
        with self.assertRaises(ValueError):
            self.store.controller({"action": "take", "controller": other, "id": "b" * 32})
        self.assertEqual(self.take()["state"], "pending")

    def test_disconnect_exact_lease_boundary_fails_closed(self):
        self.create()
        self.now += copilot.LEASE_SECONDS - 1
        self.assertIsNotNone(self.store.poll()["notification"])
        self.now += 1
        with self.assertRaises(ValueError):
            self.voice("approve")
        self.assertIsNone(self.store.poll()["notification"])
        with self.assertRaises(ValueError):
            self.take()

    def test_heartbeat_does_not_extend_request_ttl(self):
        self.create()
        for _ in range(60):
            self.now += 10
            self.store.controller({"action": "heartbeat", "controller": self.controller})
        self.assertEqual(self.take()["state"], "expired")
        with self.assertRaises(ValueError):
            self.voice("approve")

    def test_restart_invalidates_pending_and_already_answered_unconsumed_work(self):
        self.create()
        self.create("c" * 32)
        self.voice("approve")
        replacement = copilot.CopilotRequests(self.state)
        self.assertEqual(replacement.token, self.store.token)
        self.assertIsNone(replacement.poll()["notification"])
        with self.assertRaises(ValueError):
            replacement.device({"action": "copilot_voice", "id": "c" * 32, "text": "approve"})
        with replacement.connect() as db:
            self.assertEqual([row["state"] for row in db.execute("SELECT state FROM requests")],
                             ["interrupted", "interrupted"])

    def test_cancel_discards_approval_before_consumption_and_close_cancels_all(self):
        self.create()
        self.voice("approve")
        self.store.controller({"action": "cancel", "controller": self.controller, "id": "b" * 32})
        self.assertEqual(self.take()["state"], "cancelled")
        self.create("c" * 32)
        self.store.controller({"action": "close", "controller": self.controller})
        self.assertIsNone(self.store.poll()["notification"])

    def test_non_displayable_or_oversized_work_cannot_be_approved_from_gadget(self):
        for body in ("x" * 2048, "hidden\x1bcommand", "command \u202e", "command \u00e9"):
            with self.assertRaises(ValueError):
                self.create(body=body)
        self.create(body="Review on computer", respondable=False)
        with self.assertRaises(ValueError):
            self.voice("approve")
        self.store.controller({"action": "answer", "controller": self.controller, "id": "b" * 32, "text": "approve"})
        self.assertEqual(self.take()["result"], {"kind": "approve-once"})

    def test_opaque_permission_can_still_be_denied(self):
        self.create(respondable=False)
        self.voice("deny")
        self.assertEqual(self.take()["result"]["kind"], "reject")

    def test_choices_numbers_and_freeform_are_bound_to_exact_question(self):
        self.create(kind="question", body="Choose format\n1. Short\n2. Detailed", choices=["Short", "Detailed"])
        self.voice("option two.")
        self.assertEqual(self.take()["result"], {"answer": "Detailed", "wasFreeform": False})
        self.create("c" * 32, kind="question", body="Describe your preference", allow_freeform=True)
        self.voice("Keep the current theme", "c" * 32)
        self.assertEqual(self.take("c" * 32)["result"], {"answer": "Keep the current theme", "wasFreeform": True})

    def test_unknown_choice_and_out_of_range_numbers_do_not_submit_answers(self):
        self.create(kind="question", choices=["Short", "Detailed"])
        for answer in ("option three", "maybe", "0"):
            with self.assertRaises(ValueError):
                self.voice(answer)
        self.assertEqual(self.take()["state"], "pending")
        self.voice("Detailed.")
        self.assertEqual(self.take()["result"]["answer"], "Detailed")

    def test_punctuation_only_choice_differences_are_not_voice_reviewable(self):
        with self.assertRaises(ValueError):
            self.create(kind="question", choices=["Yes", "Yes."])

    def test_skipping_question_is_not_a_fabricated_freeform_answer(self):
        self.create(kind="question", choices=["Short", "Detailed"])
        self.store.poll("b" * 32)
        self.assertEqual(self.take()["state"], "cancelled")

    def test_strict_fields_and_private_state(self):
        self.create()
        with self.assertRaises(ValueError):
            self.store.device({"action": "copilot_voice", "id": "b" * 32, "text": "approve", "session": "forged"})
        for file in ("copilot.sqlite", "copilot-controller.json"):
            self.assertEqual((self.state / file).stat().st_mode & 0o777, 0o600)
        self.assertNotIn(self.store.token, json.dumps(self.store.poll()))

    def test_many_choices_remain_available_for_desktop_only_answers(self):
        choices = [f"Option {i}" for i in range(20)]
        self.create(kind="question", body="Review on computer", choices=choices, respondable=False)
        with self.assertRaises(ValueError):
            self.voice("option twenty")
        self.store.controller({"action": "answer", "controller": self.controller, "id": "b" * 32, "text": choices[-1]})
        self.assertEqual(self.take()["result"], {"answer": choices[-1], "wasFreeform": False})


if __name__ == "__main__":
    unittest.main()
