# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "tools/muse/normal_chrome.py"
spec = importlib.util.spec_from_file_location("normal_chrome_tests", SOURCE)
assert spec and spec.loader
chrome = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chrome)


def running(arguments=None, version=chrome.VERSION) -> str:
    return "chrome-devtools-mcp daemon is running.\n" + f"pid=123 version={version}\nargs=" + json.dumps(
        sorted(chrome.REQUIRED) if arguments is None else arguments) + "\n"


class NormalChromeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.discovery = self.root / "DevToolsActivePort"
        self.discovery.write_text("12345\n/devtools/browser/local-test\n")
        self.cli = self.root / "node_modules/.bin/chrome-devtools"
        self.cli.parent.mkdir(parents=True)
        self.cli.touch()
        self.client = self.cli.parent.parent / "chrome-devtools-mcp/build/src/daemon/client.js"
        self.client.parent.mkdir(parents=True)
        self.client.touch()
        self.patches = [patch.object(chrome, "DISCOVERY", self.discovery),
                        patch.object(chrome, "STATE", self.root),
                        patch.object(chrome, "CLI", self.cli)]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_absent_or_invalid_normal_profile_debugging_never_starts_another_browser(self) -> None:
        self.discovery.unlink()
        with patch.object(chrome, "invoke") as invoke:
            with self.assertRaisesRegex(chrome.ChromeError, "No isolated-browser fallback"):
                chrome.execute("list_pages", [])
            invoke.assert_not_called()
        for data in ("0\n/devtools/browser/test\n", "65536\n/devtools/browser/test\n",
                     "12345\nnot-a-browser\n", "12345\n"):
            self.discovery.write_text(data)
            with patch.object(chrome, "invoke") as invoke:
                with self.assertRaises(chrome.ChromeError):
                    chrome.execute("list_pages", [])
                invoke.assert_not_called()

    def test_startup_is_scoped_auto_connect_with_telemetry_and_crux_disabled(self) -> None:
        with patch.object(chrome, "invoke", side_effect=[
                "chrome-devtools-mcp daemon is not running.\n", "", running()]) as invoke:
            result = json.loads(chrome.execute("status", []))
        self.assertTrue(result["auto_connect"])
        self.assertFalse(result["usage_statistics"])
        startup = invoke.call_args_list[1].args[0]
        self.assertIn("--autoConnect", startup)
        self.assertIn("--no-usage-statistics", startup)
        self.assertIn("--no-performance-crux", startup)
        self.assertNotIn("--isolated", startup)
        self.assertNotIn("--userDataDir", startup)
        self.assertEqual((self.root / "browser-files").stat().st_mode & 0o777, 0o700)

    def test_wrong_daemon_policy_version_or_status_is_an_explicit_error(self) -> None:
        for output in (running([]), running([*chrome.REQUIRED, "--user-data-dir=/other"]),
                       running(version="different"), "Unknown status\n",
                       "chrome-devtools-mcp daemon is running.\nargs=not-json\n"):
            with patch.object(chrome, "invoke", return_value=output):
                with self.assertRaises(chrome.ChromeError):
                    chrome.execute("status", [])

    def test_native_calls_use_an_isolated_daemon_and_disable_update_checks(self) -> None:
        with patch.object(chrome.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "{}")) as run:
            self.assertEqual(chrome.invoke(["status"]), "{}")
        command = run.call_args.args[0]
        self.assertEqual(command[:3], [str(self.cli), "--sessionId", chrome.SESSION])
        self.assertEqual(run.call_args.kwargs["env"]["CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS"], "1")
        self.assertEqual(run.call_args.kwargs["env"]["CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS"], "1")

    def test_new_page_hides_other_user_tabs_and_extension_workers(self) -> None:
        data = {"pages": [
            {"id": 1, "url": "https://private.invalid/account", "title": "Private", "selected": False},
            {"id": 2, "url": "about:blank", "title": "", "selected": True}],
            "extensionServiceWorkers": [{"url": "chrome-extension://private"}]}
        with patch.object(chrome, "invoke", return_value=running()), \
                patch.object(chrome, "submit_tool", return_value={"structuredContent": data}):
            output = chrome.execute("new_page", ['{"url":"about:blank"}'])
        self.assertNotIn("private", output)
        self.assertEqual(json.loads(output)["structuredContent"]["pages"][0]["id"], 2)

    def test_navigation_only_returns_the_requested_tab_and_close_hides_fallback_selection(self) -> None:
        pages = [{"id": 1, "url": "https://private.invalid", "selected": True},
                 {"id": 2, "url": "https://requested.invalid", "selected": False}]
        for command, args, expected in (("navigate_page", ['{"pageId":2,"url":"https://requested.invalid"}'], [2]),
                                        ("close_page", ['{"pageId":2}'], [])):
            with patch.object(chrome, "invoke", return_value=running()), \
                    patch.object(chrome, "submit_tool", return_value={"structuredContent": {"pages": pages}}):
                output = chrome.execute(command, args)
            self.assertNotIn("private", output)
            self.assertEqual([p["id"] for p in json.loads(output)["structuredContent"]["pages"]], expected)

    def test_tab_listing_is_explicit_and_keeps_page_ids_without_extension_details(self) -> None:
        data = {"pages": [{"id": 7, "url": "https://requested.invalid", "selected": True}],
                "extensionServiceWorkers": [{"url": "chrome-extension://private"}]}
        with patch.object(chrome, "invoke", return_value=running()), \
                patch.object(chrome, "submit_tool", return_value={"structuredContent": data}):
            output = chrome.execute("list_pages", [])
        self.assertEqual(json.loads(output)["structuredContent"]["pages"][0]["id"], 7)
        self.assertNotIn("chrome-extension", output)

    def test_help_and_stop_do_not_require_or_start_a_browser(self) -> None:
        self.discovery.unlink()
        with patch.object(chrome, "invoke", return_value="Usage") as invoke:
            help_text = chrome.execute("click", ["--help"])
            self.assertIn("click - <<'MUSE_CHROME_JSON'", help_text)
            self.assertIn("not positional arguments", help_text)
            self.assertIn("Usage", help_text)
            self.assertEqual(chrome.execute("stop", []), "Usage")
        self.assertEqual(invoke.call_args_list[0].args[0], ["click", "--help"])
        self.assertEqual(invoke.call_args_list[1].args[0], ["stop"])

    def test_stdin_preserves_script_quotes_without_shell_interpretation(self) -> None:
        parameters = {"pageId": 2, "function": "() => document.querySelector('input').value"}
        with patch.object(chrome.sys, "stdin", io.StringIO(json.dumps(parameters))), \
                patch.object(chrome, "invoke", return_value=running()), \
                patch.object(chrome, "submit_tool", return_value={
                    "structuredContent": {"result": "test"}}) as submit:
            chrome.execute("evaluate_script", ["-"])
        submit.assert_called_once_with("evaluate_script", parameters)

    def test_stdin_preserves_search_text_and_existing_privacy_guards(self) -> None:
        parameters = {"pageId": 2, "uid": "1_3", "value": "O'Brien \"test\" $HOME $(not-a-command)"}
        with patch.object(chrome.sys, "stdin", io.StringIO(json.dumps(parameters))), \
                patch.object(chrome, "invoke", return_value=running()), \
                patch.object(chrome, "submit_tool", return_value={
                    "structuredContent": {}}) as submit:
            chrome.execute("fill", ["-"])
        submit.assert_called_once_with("fill", parameters)
        for data in ("", "not JSON", "[]", '{"browserUrl":"http://other"}',
                     '{"background":true}', " " * 65537):
            with patch.object(chrome.sys, "stdin", io.StringIO(data)), \
                    patch.object(chrome, "invoke") as invoke, \
                    patch.object(chrome, "submit_tool") as submit:
                with self.assertRaises(chrome.ChromeError):
                    chrome.execute("new_page", ["-"])
                invoke.assert_not_called()
                submit.assert_not_called()

    def test_shell_split_json_has_stdin_recovery_guidance_without_execution(self) -> None:
        arguments = shlex.split(
            """'{"pageId":2,"function":"() => document.querySelector('input').value = 'test text'"}'""")
        self.assertGreater(len(arguments), 1)
        with patch.object(chrome, "invoke") as invoke, \
                patch.object(chrome, "submit_tool") as submit:
            with self.assertRaisesRegex(chrome.ChromeError, "MUSE_CHROME_JSON"):
                chrome.execute("evaluate_script", arguments)
            invoke.assert_not_called()
            submit.assert_not_called()

    def test_placeholder_tool_names_explain_real_commands_without_execution(self) -> None:
        for command in ("TOOL", "OPEN_TAB", "New_Page"):
            with patch.object(chrome, "invoke") as invoke, \
                    patch.object(chrome, "submit_tool") as submit:
                with self.assertRaisesRegex(chrome.ChromeError, "new_page, evaluate_script"):
                    chrome.execute(command, ['{"url":"about:blank"}'])
                invoke.assert_not_called()
                submit.assert_not_called()

    def test_script_errors_explain_callable_functions_without_retrying(self) -> None:
        with patch.object(chrome, "invoke", return_value=running()), \
                patch.object(chrome, "submit_tool", return_value={
                    "isError": True, "content": [{"type": "text", "text": "fn is not a function"}]}) as submit:
            with self.assertRaisesRegex(chrome.ChromeError, "callable JavaScript function"):
                chrome.execute("evaluate_script", ['{"pageId":2,"function":"document.title = 1"}'])
            submit.assert_called_once()

    def test_connection_overrides_and_background_or_isolated_contexts_are_rejected(self) -> None:
        for args in (['{"browserUrl":"http://other"}'], ['{"channel":"beta"}'],
                     ['{"sessionId":"bad"}'], ['{"isolatedContext":"other"}'], ['{"background":true}'],
                     ["--url", "https://example.com"], ["[]"], ["not-json"]):
            with patch.object(chrome, "invoke") as invoke:
                with self.assertRaises(chrome.ChromeError):
                    chrome.execute("new_page", args)
                invoke.assert_not_called()

    def test_invalid_tool_results_and_ambiguous_created_tabs_are_not_success(self) -> None:
        for result in ({"isError": True, "content": []}, {"structuredContent": {"errorMessage": "Failed"}},
                       {}, {"structuredContent": {}}, {"structuredContent": {"pages": []}}):
            with patch.object(chrome, "invoke", return_value=running()), \
                    patch.object(chrome, "submit_tool", return_value=result):
                with self.assertRaises(chrome.ChromeError):
                    chrome.execute("new_page", ['{"url":"about:blank"}'])

    def test_missing_cli_command_failures_and_timeouts_are_explicit_without_retries(self) -> None:
        self.cli.unlink()
        with self.assertRaisesRegex(chrome.ChromeError, "Install"):
            chrome.invoke(["status"])
        self.cli.touch()
        with patch.object(chrome.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "Denied")) as run:
            with self.assertRaisesRegex(chrome.ChromeError, "Denied"):
                chrome.invoke(["status"])
            self.assertEqual(run.call_count, 1)
        with patch.object(chrome.subprocess, "run", side_effect=subprocess.TimeoutExpired("test", 40)) as run:
            with self.assertRaisesRegex(chrome.ChromeError, "do not repeat"):
                chrome.invoke(["status"])
            self.assertEqual(run.call_count, 1)

    def test_tool_requests_use_existing_daemon_only_with_json_parameters_on_stdin(self) -> None:
        response = {"structuredContent": {"pages": []}}
        with patch.object(chrome.shutil, "which", return_value="/usr/bin/node"), \
                patch.object(chrome.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], 0, json.dumps(response), "")) as run:
            self.assertEqual(chrome.submit_tool("list_pages", {}), response)
        payload = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(payload["session"], chrome.SESSION)
        self.assertEqual(payload["tool"], "list_pages")
        self.assertEqual(payload["parameters"], {})
        script = run.call_args.args[0][-1]
        self.assertIn("sendCommand", script)
        self.assertNotIn("startDaemon", script)
        with self.assertRaisesRegex(chrome.ChromeError, "existing daemon client"):
            chrome.invoke(["new_page", "about:blank"])

    def test_daemon_disconnect_invalid_json_and_tool_timeouts_do_not_restart_or_retry(self) -> None:
        for result in (subprocess.CompletedProcess([], 1, "", "Daemon is not running."),
                       subprocess.CompletedProcess([], 0, "not JSON", ""),
                       subprocess.CompletedProcess([], 0, "[]", "")):
            with patch.object(chrome.shutil, "which", return_value="/usr/bin/node"), \
                    patch.object(chrome.subprocess, "run", return_value=result) as run:
                with self.assertRaises(chrome.ChromeError):
                    chrome.submit_tool("new_page", {"url": "about:blank"})
                self.assertEqual(run.call_count, 1)
        with patch.object(chrome.shutil, "which", return_value="/usr/bin/node"), \
                patch.object(chrome.subprocess, "run", side_effect=subprocess.TimeoutExpired("test", 40)) as run:
            with self.assertRaisesRegex(chrome.ChromeError, "do not repeat"):
                chrome.submit_tool("new_page", {"url": "about:blank"})
            self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
