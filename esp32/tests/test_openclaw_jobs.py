# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/muse"))
from openclaw_jobs import GatewayRPC, JobManager
sys.path.pop(0)

JOB = "a" * 32
MESSAGES = [{"role": "user", "content": "A harmless test"}]


class Native:
    def __init__(self):
        self.calls = []
        self.running = threading.Event()
        self.complete = threading.Event()
        self.aborted = True

    def __call__(self, method, params):
        self.calls.append((method, params))
        if method == "chat.send":
            self.running.set()
            return {"runId": params["idempotencyKey"], "status": "started"}
        if method == "chat.abort":
            return {"ok": True, "aborted": self.aborted}
        if method == "agent.wait":
            if params["timeoutMs"] and self.complete.wait(0.05):
                return {"status": "ok"}
            return {"status": "timeout"}
        if method == "chat.history":
            return {"messages": [{"role": "assistant", "content": [
                {"type": "text", "text": "Done."}], "stopReason": "stop",
                "usage": {"cost": {"total": 0.0001}}}]}
        raise AssertionError(method)


class JobsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.native = Native()
        self.chat_lock = threading.Lock()
        self.manager = JobManager(Path(self.tmp.name), self.native, self.chat_lock)

    def tearDown(self):
        self.manager.close()
        self.tmp.cleanup()

    def finished(self, job_id=JOB):
        self.manager.thread.join(timeout=5)
        self.assertFalse(self.manager.thread.is_alive())
        return self.manager.status(job_id)

    def personal(self):
        self.manager.close()
        self.manager = JobManager(Path(self.tmp.name), self.native, self.chat_lock, durable_conversation=True)
        self.native.complete.set()

    def test_durable_followup_survives_restart_and_ignores_stale_device_history(self):
        self.personal()
        self.manager.start(JOB, [{"role": "user", "content": "My test project is a blue clock."}])
        self.finished()
        self.manager.close()
        self.manager = JobManager(Path(self.tmp.name), self.native, self.chat_lock, durable_conversation=True)
        self.manager.start("b" * 32, [{"role": "user", "content": "What colour was it?"}])
        self.finished("b" * 32)
        prompt = [p["message"] for m, p in self.native.calls if m == "chat.send"][-1]
        self.assertIn("My test project is a blue clock.", prompt)
        self.assertEqual(self.manager.personal_status()["conversation"]["turn_count"], 2)
        self.assertEqual(self.manager.personal_status()["memory_count"], 0)
        self.manager.reset_conversation()
        self.manager.start("c" * 32, [{"role": "user", "content": "Stale private device history"},
                                    {"role": "assistant", "content": "Do not replay me."},
                                    {"role": "user", "content": "Start fresh."}])
        self.finished("c" * 32)
        prompt = [p["message"] for m, p in self.native.calls if m == "chat.send"][-1]
        self.assertNotIn("Stale private device history", prompt)
        self.assertNotIn("blue clock", prompt)

    def test_explicit_memory_is_persistent_idempotent_and_forgetting_excludes_old_turns(self):
        self.personal()
        saved = self.manager.memory_add("PERSONAL_AI_TEST: afternoon meetings")["memory"]
        self.assertEqual(self.manager.memory_add(saved["text"])["memory"], saved)
        self.manager.start(JOB, [{"role": "user", "content": "PERSONAL_AI_TEST: afternoon meetings"}])
        self.finished()
        prompt = [p["message"] for m, p in self.native.calls if m == "chat.send"][-1]
        self.assertIn(saved["text"], prompt)
        self.assertIn("Never infer or automatically save", prompt)
        self.assertIn('{"action":"memory_add","text":"I prefer short answers."}', prompt)
        self.assertIn("Memory requests are NOT reminders", prompt)
        before = self.manager.personal_status()["conversation"]["id"]
        self.manager.reset_conversation()
        self.assertEqual(self.manager.personal_status()["memory_count"], 1)
        self.manager.memory_forget(saved["id"])
        status = self.manager.personal_status()
        self.assertNotEqual(status["conversation"]["id"], before)
        self.assertEqual(status["memory_count"], 0)
        self.assertEqual(status["conversation"]["turn_count"], 0)
        self.manager.start("b" * 32, [{"role": "user", "content": "What do you remember?"}])
        self.finished("b" * 32)
        prompt = [p["message"] for m, p in self.native.calls if m == "chat.send"][-1]
        self.assertNotIn(saved["text"], prompt)
        with self.assertRaises(ValueError):
            self.manager.memory_forget(saved["id"])

    def test_memory_limits_unicode_pagination_and_delete_page_are_exact(self):
        self.personal()
        for bad in ("", " ", "\x00private", "x" * 241, "\u00e9" * 121, None, True):
            with self.assertRaises(ValueError):
                self.manager.memory_add(bad)
        self.manager.memory_add("\u00e9" * 120)
        for index in range(49):
            self.manager.memory_add(f"Explicit test memory {index}")
        with self.assertRaises(RuntimeError):
            self.manager.memory_add("Item 51")
        status = self.manager.memory_page(48)
        self.assertEqual(len(status["memories"]), 2)
        self.assertFalse(status["memory_more"])
        for memory in status["memories"]:
            self.manager.memory_forget(memory["id"])
        self.assertEqual(self.manager.personal_status()["memory_offset"], 42)
        for invalid in (-6, 1, 54, True, None):
            with self.assertRaises(ValueError):
                self.manager.memory_page(invalid)

    def test_cancelled_turns_are_not_followup_context(self):
        self.personal()
        self.native.complete.clear()
        self.manager.start(JOB, [{"role": "user", "content": "UNFINISHED_PRIVATE_ACTION"}])
        self.assertTrue(self.native.running.wait(2))
        self.manager.cancel(JOB)
        self.assertEqual(self.finished()["status"], "cancelled")
        self.native.complete.set()
        self.manager.start("b" * 32, MESSAGES)
        self.finished("b" * 32)
        prompt = [p["message"] for m, p in self.native.calls if m == "chat.send"][-1]
        self.assertNotIn("UNFINISHED_PRIVATE_ACTION", prompt)

    def test_followup_keeps_eight_recent_completed_turns_and_bounds_context_bytes(self):
        self.personal()
        for index in range(10):
            job_id = f"{index:032x}"
            self.manager.start(job_id, [{"role": "user", "content": f"Completed question {index}"}])
            self.finished(job_id)
        history, _ = self.manager.personal_context(f"{9:032x}")
        self.assertEqual(len(history), 16)
        self.assertEqual(history[0]["content"], "Completed question 1")
        self.assertEqual(history[-2]["content"], "Completed question 8")
        with self.manager.connect() as db:
            db.execute("UPDATE jobs SET request=? WHERE id=?", ("x" * 23900, f"{8:032x}"))
        history, _ = self.manager.personal_context(f"{9:032x}")
        import json
        self.assertLessEqual(len(json.dumps(history, ensure_ascii=False).encode()), 24000)

    def test_idempotent_start_scoped_run_and_real_cancellation(self):
        self.manager.start(JOB, MESSAGES)
        self.assertTrue(self.native.running.wait(2))
        self.manager.start(JOB, MESSAGES)
        with self.assertRaises(ValueError):
            self.manager.start(JOB, [{"role": "user", "content": "Different"}])
        with self.assertRaises(RuntimeError):
            self.manager.start("b" * 32, MESSAGES)
        self.manager.cancel(JOB)
        self.assertEqual(self.finished()["status"], "cancelled")
        sends = [p for m, p in self.native.calls if m == "chat.send"]
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0]["sessionKey"], "agent:esp32:muse-job:" + JOB)
        self.assertEqual(sends[0]["idempotencyKey"], JOB)
        self.assertEqual(sends[0]["timeoutMs"], 86400000)
        self.assertIn("Never use the cron tool for gadget reminders", sends[0]["message"])
        self.assertIn("companion_cli.py", sends[0]["message"])
        self.assertIn(str(Path(self.tmp.name) / "companion_cli.py"), sends[0]["message"])
        self.assertIn("not key=value arguments", sends[0]["message"])
        self.assertIn("Never replay their commands or actions", sends[0]["message"])
        self.assertTrue(sends[0]["message"].endswith(MESSAGES[-1]["content"]))
        self.assertIn(("chat.abort", {"sessionKey": sends[0]["sessionKey"], "runId": JOB}),
                      self.native.calls)
        self.assertFalse(self.chat_lock.locked())

    def test_completion_retained_across_restart_without_replay(self):
        self.native.complete.set()
        self.manager.start(JOB, MESSAGES)
        result = self.finished()
        self.assertEqual(result["reply"], "Done.")
        self.assertEqual(result["usage"]["estimated_model_usd"], 0.0001)
        self.assertEqual(self.native.calls[0][0], "agent.wait")
        self.manager.close()
        self.manager = JobManager(Path(self.tmp.name), self.native, self.chat_lock)
        self.assertEqual(self.manager.start(JOB, MESSAGES)["status"], "completed")
        self.assertEqual(sum(m == "chat.send" for m, _ in self.native.calls), 1)

    def test_unconfirmed_stop_is_not_reported_as_cancelled(self):
        self.native.aborted = False
        self.manager.start(JOB, MESSAGES)
        self.assertTrue(self.native.running.wait(2))
        self.manager.cancel(JOB)
        self.assertEqual(self.finished()["status"], "interrupted")
        self.assertIn("could not be confirmed", self.manager.status(JOB)["detail"])

    def test_recovery_stops_only_device_owned_job_and_never_replays(self):
        self.native.complete.set()
        self.manager.start(JOB, MESSAGES)
        self.finished()
        with self.manager.connect() as db:
            db.execute("UPDATE jobs SET status='running'")
        self.manager.close()
        self.native.calls.clear()
        self.manager = JobManager(Path(self.tmp.name), self.native, self.chat_lock)
        self.assertEqual(self.finished()["status"], "cancelled")
        self.assertEqual([m for m, _ in self.native.calls], ["chat.abort"])

    def test_bad_identifiers_and_missing_or_oversized_replies_fail(self):
        for bad in ("main", "a" * 31, "../config", None):
            with self.assertRaises(ValueError):
                self.manager.status(bad)
        for text in ("", "\u00e9" * 1024):
            with self.assertRaises(ValueError):
                JobManager.result({"messages": [{"role": "assistant", "content": text}]})
        reply, usage = JobManager.result({"messages": [
            {"role": "assistant", "content": "Done.", "usage": {"cost": {"total": 0}}}]})
        self.assertEqual(reply, "Done.")
        self.assertIsNone(usage)
        self.assertEqual(Path(self.manager.path).stat().st_mode & 0o777, 0o600)

    def test_partial_usage_and_full_history_are_not_presented_as_complete(self):
        priced = {"role": "assistant", "content": "Done.", "usage": {"cost": {"total": 0.0001}}}
        _, usage = JobManager.result({"messages": [
            {"role": "assistant", "content": [], "stopReason": "toolUse"}, priced]})
        self.assertEqual(usage["estimated_model_usd"], 0.0001)
        self.assertFalse(usage["complete"])
        self.assertEqual(usage["unpriced_steps"], 1)
        self.native.complete.set()
        self.manager.start(JOB, MESSAGES)
        self.finished()
        self.manager.update(JOB, "completed", "Done.", "Done.", usage)
        self.assertEqual(self.manager.costs()["unpriced_jobs"], 1)
        _, full = JobManager.result({"messages": [priced] * 100})
        self.assertFalse(full["complete"])
        _, complete = JobManager.result({"messages": [priced]})
        self.assertTrue(complete["complete"])

    def test_rpc_keeps_credentials_out_of_arguments_and_checks_config(self):
        verified = []
        rpc = GatewayRPC(Path("/private/test-config.json"), lambda: verified.append(True))
        with patch("openclaw_jobs.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = '{"status":"timeout"}'
            self.assertEqual(rpc("agent.wait", {"runId": JOB})["status"], "timeout")
            self.assertEqual(verified, [True])
            self.assertNotIn("--token", run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs["env"]["OPENCLAW_CONFIG_PATH"],
                             "/private/test-config.json")
