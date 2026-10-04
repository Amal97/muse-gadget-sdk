# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools/muse"


class OpenAIBackendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        source = (ROOT / "components/muse/muse_openai.c").read_text()
        source = re.sub(r"^#include .*$", "", source, flags=re.MULTILINE)
        harness = (ROOT / "tests/openai_harness.c").read_text()
        (tmp / "openai.c").write_text(harness.replace("/* OPENAI_IMPLEMENTATION */", source))
        (tmp / "esp_err.h").write_text("#pragma once\n"
                                      "typedef int esp_err_t;\n#define ESP_OK 0\n#define ESP_FAIL -1\n")
        cls.binary = tmp / "openai"
        cls.normal_chrome_binary = tmp / "openai-normal-chrome"
        cjson = ROOT / "managed_components/espressif__cjson/cJSON"
        if not (cjson / "cJSON.c").exists():
            cls.tmp.cleanup()
            raise unittest.SkipTest("Run an ESP-IDF build first to download the cJSON component.")
        command = [
            *shlex.split(os.environ.get("CC", "cc")), "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-I", str(tmp), "-I", str(cjson), "-I", str(ROOT / "tests"),
            "-I", str(ROOT / "tests/link_fakes"), "-I", str(ROOT / "components/muse"),
            str(tmp / "openai.c"), str(ROOT / "components/muse/muse_openai_codec.c"),
            str(cjson / "cJSON.c"),
        ]
        for binary, flags in ((cls.binary, []),
                              (cls.normal_chrome_binary, ["-DCONFIG_MUSE_OPENCLAW_NORMAL_CHROME=1"])):
            proc = subprocess.run([*command, *flags, "-o", str(binary)], capture_output=True, text=True)
            if proc.returncode:
                cls.tmp.cleanup()
                raise RuntimeError(proc.stdout + proc.stderr)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def run_case(self, case: int, *, normal_chrome: bool = False) -> None:
        binary = self.normal_chrome_binary if normal_chrome else self.binary
        proc = subprocess.run([str(binary), str(case)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_voice_pipeline_wav_upload_chat_and_fragmented_pcm(self) -> None:
        self.run_case(0)

    def test_http_failures_are_explicit_and_next_turn_recovers(self) -> None:
        self.run_case(1)

    def test_invalid_json_and_speech_format_are_not_success(self) -> None:
        self.run_case(2)

    def test_cancel_drops_stale_events_and_audio(self) -> None:
        self.run_case(3)

    def test_typed_chat_and_muting_skip_paid_speech(self) -> None:
        self.run_case(4)

    def test_incomplete_download_is_an_error(self) -> None:
        self.run_case(5)

    def test_wav_sizes_signed_pcm_and_recording_limit(self) -> None:
        self.run_case(6)

    def test_history_retains_four_pairs_and_can_be_cleared(self) -> None:
        self.run_case(7)

    def test_clearing_active_voice_wakes_waiter_and_next_turn_recovers(self) -> None:
        self.run_case(8)

    def test_openclaw_routes_only_chat_with_separate_credentials_and_no_failure_fallback(self) -> None:
        self.run_case(9)

    def test_notifications_use_local_tls_ack_after_dismiss_and_pause_asleep_or_busy(self) -> None:
        self.run_case(10)

    def test_normal_chrome_opt_in_uses_local_skill_without_isolated_browser_fallback(self) -> None:
        self.run_case(9, normal_chrome=True)

    def test_timer_voice_shortcut_skips_chat_and_expired_timer_beats_replacement(self) -> None:
        self.run_case(11)

    def test_dictated_reply_is_bound_and_never_calls_chat_or_send(self) -> None:
        self.run_case(12)

    def test_malformed_draft_does_not_ack_original_message(self) -> None:
        self.run_case(13)

    def test_stop_uses_captured_original_bridge_credentials(self) -> None:
        self.run_case(14)

    def test_failed_snooze_preserves_card_until_success(self) -> None:
        self.run_case(15)


class FakeBoard:
    def __init__(self, device: dict) -> None:
        self.device = device
        self.writes: list[str] = []

    def status(self) -> dict:
        return {"device": self.device}

    def write_line(self, line: str) -> None:
        self.writes.append(line)
        self.device["last"] = line.split("=", 1)[0] + ": ok"


class OpenAISetupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(TOOLS))
        spec = importlib.util.spec_from_file_location("openai_setup_test", TOOLS / "openai_setup.py")
        assert spec and spec.loader
        cls.setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.setup)
        sys.path.remove(str(TOOLS))

    def test_refuses_non_openai_firmware_before_sending_secrets(self) -> None:
        board = FakeBoard({"provider": "muse"})
        with self.assertRaises(self.setup.BoardError):
            self.setup.status(board)
        self.assertEqual(board.writes, [])

    def test_command_acknowledgement_does_not_need_to_echo_key(self) -> None:
        board = FakeBoard({"provider": "openai"})
        self.setup.command(board, "openai.key", "test-secret")
        self.assertEqual(board.device["last"], "openai.key: ok")

    def test_rejects_command_injection_and_overlong_values(self) -> None:
        board = FakeBoard({"provider": "openai"})
        for value in ["bad\n>chat=hi", "bad\rkey", "bad\0key", "x" * 1200]:
            with self.assertRaises(self.setup.BoardError):
                self.setup.command(board, "openai.key", value)
        self.assertEqual(board.writes, [])

    def test_key_failure_is_not_reported_as_success(self) -> None:
        board = FakeBoard({"provider": "openai",
                           "openai": {"state": "Request failed", "detail": "OPENAI KEY INVALID"}})
        with patch.object(self.setup, "command"):
            with self.assertRaisesRegex(self.setup.BoardError, "KEY INVALID"):
                self.setup.test_key(board)

    def test_noninteractive_secret_prompt_is_refused(self) -> None:
        with patch.object(sys, "argv", ["openai_setup.py", "--key"]), \
                patch.object(sys.stdin, "isatty", return_value=False), \
                patch.object(sys, "stderr", new_callable=io.StringIO), \
                patch.object(self.setup, "Board") as board:
            self.assertEqual(self.setup.main(), 1)
            board.assert_not_called()


if __name__ == "__main__":
    unittest.main()
