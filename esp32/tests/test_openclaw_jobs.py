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
            return {"runId": JOB, "status": "started"}
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

    def finished(self):
        self.manager.thread.join(timeout=5)
        self.assertFalse(self.manager.thread.is_alive())
        return self.manager.status(JOB)

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
