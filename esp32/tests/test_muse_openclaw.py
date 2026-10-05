# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import copy
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shlex
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

TOOLS = Path(__file__).resolve().parents[1] / "tools/muse"


def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + ".py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(TOOLS))
bridge = load_tool("openclaw_bridge")
setup = load_tool("openclaw_setup")
sys.path.remove(str(TOOLS))


class Upstream(BaseHTTPRequestHandler):
    records: list[tuple[str, dict, dict]] = []
    response_status = 200
    content = "Hello from the restricted companion."

    def log_message(self, *args) -> None:
        pass

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.records.append((self.path, dict(self.headers), body))
        payload = json.dumps({"choices": [{"message": {"content": self.content},
                                          "finish_reason": "stop"}]}).encode()
        self.send_response(self.response_status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class BridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.state = Path(cls.tmp.name) / "state"
        bridge.prepare(cls.state)
        cls.config_path = Path(cls.tmp.name) / "openclaw.json"
        cls.upstream = HTTPServer(("127.0.0.1", 0), Upstream)
        cls.upstream_thread = threading.Thread(target=cls.upstream.serve_forever)
        cls.upstream_thread.start()
        cls.config = {
            "gateway": {"bind": "loopback", "port": cls.upstream.server_port,
                        "auth": {"mode": "token", "token": "private-gateway-key"},
                        "http": {"endpoints": {"chatCompletions": {"enabled": True}}}},
            "agents": {"list": [{"id": "esp32", "tools": {"deny": ["*"]}}]},
        }
        cls.config_path.write_text(json.dumps(cls.config))
        cls.server = bridge.BridgeServer(("127.0.0.1", 0), cls.state, cls.config_path)
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.start()
        cls.token = bridge.read_object(cls.state / "bridge.json")["device_token"]
        cls.context = ssl.create_default_context(cafile=str(cls.state / "ca.pem"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.upstream_thread.join()
        cls.tmp.cleanup()

    def setUp(self) -> None:
        Upstream.records.clear()
        Upstream.response_status = 200
        Upstream.content = "Hello from the restricted companion."
        self.config_path.write_text(json.dumps(self.config))

    def request(self, body: dict, path: str = bridge.CHAT_PATH,
                token: str | None = None, extra_headers: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode()
        connection = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=5)
        with self.context.wrap_socket(connection, server_hostname=bridge.IDENTITY) as tls:
            headers = {"Content-Type": "application/json", "Content-Length": str(len(data)),
                       "Authorization": "Bearer " + (self.token if token is None else token),
                       **(extra_headers or {})}
            text = f"POST {path} HTTP/1.1\r\nHost: {bridge.IDENTITY}\r\n"
            text += "".join(f"{key}: {value}\r\n" for key, value in headers.items())
            tls.sendall(text.encode() + b"\r\n" + data)
            response = http.client.HTTPResponse(tls)
            response.begin()
            return response.status, json.loads(response.read())

    def test_real_https_routes_only_to_restricted_agent_and_separates_credentials(self) -> None:
        code, result = self.request(
            {"model": "openclaw:main", "user": "forged-session", "stream": True,
             "messages": [{"role": "user", "content": "Hello"}]},
            extra_headers={"x-openclaw-agent-id": "main", "x-openclaw-session-key": "main"})
        self.assertEqual(code, 200)
        self.assertEqual(result["choices"][0]["message"]["content"], Upstream.content)
        path, headers, body = Upstream.records[0]
        self.assertEqual(path, bridge.CHAT_PATH)
        self.assertEqual(headers["Authorization"], "Bearer private-gateway-key")
        self.assertEqual(headers["x-openclaw-agent-id"], "esp32")
        self.assertNotIn("x-openclaw-session-key", headers)
        self.assertEqual(body["model"], "openclaw:esp32")
        self.assertFalse(body["stream"])
        self.assertNotIn("user", body)
        self.assertNotIn(self.token, json.dumps(body))

    def test_bad_auth_and_admin_routes_never_reach_gateway(self) -> None:
        self.assertEqual(self.request({}, token="wrong")[0], 401)
        self.assertEqual(self.request({}, path="/tools/invoke")[0], 404)
        self.assertEqual(self.request({}, path=bridge.CHAT_PATH + "?model=main")[0], 404)
        self.assertEqual(Upstream.records, [])

    def test_response_limit_is_enforced_at_exact_byte_boundary(self) -> None:
        companion = Mock()
        overhead = len(json.dumps({"payload": ""}).encode())
        with patch.object(self.server, "companion", companion):
            companion.handle.return_value = {"payload": "x" * (bridge.JSON_CAP - overhead - 1)}
            code, result = self.request({"action": "calendars"}, path=bridge.COMPANION_PATH)
            self.assertEqual(code, 200)
            self.assertEqual(len(json.dumps({"payload": result["payload"]}).encode()), bridge.JSON_CAP - 1)
            companion.handle.return_value = {"payload": "x" * bridge.JSON_CAP}
            with self.assertLogs(level="ERROR"):
                code, result = self.request({"action": "calendars"}, path=bridge.COMPANION_PATH)
            self.assertEqual(code, 413)
            self.assertLess(len(json.dumps(result).encode()), bridge.JSON_CAP)

    def test_notification_endpoint_is_authenticated_local_and_opt_in(self) -> None:
        self.assertEqual(self.request({"ack": ""}, path=bridge.NOTIFICATION_PATH)[0], 404)
        inbox = Mock()
        inbox.poll.return_value = {"notification": {"id": "a" * 32, "sender": "Test", "preview": "Hello"}}
        with patch.object(self.server, "inbox", inbox):
            self.assertEqual(self.request({"ack": ""}, path=bridge.NOTIFICATION_PATH, token="wrong")[0], 401)
            self.assertEqual(self.request({}, path=bridge.NOTIFICATION_PATH)[0], 400)
            self.assertEqual(self.request({"ack": "", "model": "main"}, path=bridge.NOTIFICATION_PATH)[0], 400)
            code, result = self.request({"ack": ""}, path=bridge.NOTIFICATION_PATH)
            self.assertEqual(code, 200)
            self.assertEqual(result["notification"]["preview"], "Hello")
            inbox.poll.assert_called_once_with("")
            inbox.poll.side_effect = RuntimeError("Watcher unavailable.")
            with self.assertLogs(level="ERROR"):
                self.assertEqual(self.request({"ack": ""}, path=bridge.NOTIFICATION_PATH)[0], 503)
            inbox.poll.side_effect = ValueError("Unknown acknowledgment.")
            self.assertEqual(self.request({"ack": "bad"}, path=bridge.NOTIFICATION_PATH)[0], 400)
        self.assertEqual(Upstream.records, [])

    def test_notification_polling_does_not_wait_for_a_long_chat(self) -> None:
        started, release = threading.Event(), threading.Event()
        result = []

        def reply(*args, **kwargs):
            started.set()
            if not release.wait(4):
                raise RuntimeError("Test chat timed out.")
            return b'{"choices":[{"message":{"content":"Done"}}]}'

        inbox = Mock()
        inbox.poll.return_value = {"notification": None}
        with patch.object(bridge, "agent_reply", side_effect=reply), \
                patch.object(self.server, "inbox", inbox):
            thread = threading.Thread(target=lambda: result.append(
                self.request({"messages": [{"role": "user", "content": "Long action"}]})))
            thread.start()
            try:
                self.assertTrue(started.wait(2))
                self.assertEqual(self.request({"ack": ""}, path=bridge.NOTIFICATION_PATH)[0], 200)
                self.assertFalse(release.is_set())
                self.assertEqual(self.request({"messages": [{"role": "user", "content": "Other"}]})[0], 409)
            finally:
                release.set()
                thread.join(timeout=5)
            self.assertEqual(result[0][0], 200)

    def test_request_limits_are_utf8_bytes_and_invalid_roles_are_rejected(self) -> None:
        for content, expected in (("x" * 2047, 200), ("x" * 2048, 400),
                                  ("\u00e9" * 1023, 200), ("\u00e9" * 1024, 400)):
            self.assertEqual(self.request({"messages": [{"role": "user", "content": content}]})[0],
                             expected)
        self.assertEqual(self.request({"messages": [{"role": "tool", "content": "test"}]})[0], 400)
        self.assertEqual(self.request({"messages": [{"role": "user", "content": "x"}] * 11})[0], 400)
        self.assertEqual(self.request({"padding": "x" * bridge.JSON_CAP})[0], 413)

    def test_personal_memory_and_conversation_routes_are_authenticated_and_strict(self):
        companion = bridge.Companion(self.state)
        companion.put("endpoint", f"https://127.0.0.1:{self.server.server_port}{bridge.COMPANION_PATH}")
        jobs = bridge.JobManager(self.state, Mock(), self.server.chat_lock, durable_conversation=True)
        try:
            with patch.object(self.server, "companion", companion), patch.object(self.server, "jobs", jobs):
                path = bridge.COMPANION_PATH
                self.assertEqual(self.request({"action": "memory_add", "text": "TEST_MEMORY"},
                                              path=path, token="wrong")[0], 401)
                self.assertEqual(jobs.personal_status()["memory_count"], 0)
                code, saved = self.request({"action": "memory_add", "text": "TEST_MEMORY"}, path=path)
                self.assertEqual(code, 200)
                self.assertEqual(saved["memory_count"], 1)
                code, status = self.request({"action": "status"}, path=path)
                self.assertEqual(code, 200)
                self.assertEqual(status["memories"][0]["text"], "TEST_MEMORY")
                self.assertTrue(status["conversation"]["persistent"])
                for body in ({"action": "memory_add"}, {"action": "memory_list", "offset": True},
                             {"action": "conversation_reset", "extra": 1}):
                    self.assertEqual(self.request(body, path=path)[0], 400)
                cli = load_tool("companion_cli")
                with self.assertRaisesRegex(RuntimeError, "HTTP 400: Invalid personal companion request fields"):
                    cli.call({"action": "memory_add", "content": "Wrong field"}, self.state)
                before = status["conversation"]["id"]
                code, result = self.request({"action": "memory_forget", "id": saved["memory"]["id"]}, path=path)
                self.assertEqual(code, 200)
                self.assertEqual(result["memory_count"], 0)
                self.assertNotEqual(result["conversation"]["id"], before)
                self.assertEqual(self.request({"action": "conversation_reset"}, path=path)[0], 200)
                self.assertEqual(Upstream.records, [])
        finally:
            jobs.close()
            companion.close()

    def test_upstream_errors_and_invalid_replies_are_explicit(self) -> None:
        body = {"messages": [{"role": "user", "content": "Hi"}]}
        Upstream.response_status = 500
        with self.assertLogs(level="ERROR"):
            code, result = self.request(body)
        self.assertEqual(code, 502)
        self.assertNotIn("choices", result)
        Upstream.response_status = 200
        for content in ("", "x" * 2048, "No response from OpenClaw."):
            Upstream.content = content
            with self.assertLogs(level="ERROR"):
                self.assertEqual(self.request(body)[0], 502)

    def test_changed_agent_policy_or_public_gateway_is_refused(self) -> None:
        for field in ("agent", "bind", "endpoint", "shape"):
            config = copy.deepcopy(self.config)
            if field == "agent":
                config["agents"]["list"][0]["tools"]["deny"] = []
            elif field == "bind":
                config["gateway"]["bind"] = "lan"
            elif field == "shape":
                config["gateway"]["http"] = []
            else:
                config["gateway"]["http"]["endpoints"]["chatCompletions"]["enabled"] = False
            self.config_path.write_text(json.dumps(config))
            with self.assertRaises(ValueError):
                bridge.gateway_credentials(self.config_path)

    def test_computer_control_requires_explicit_opt_in_and_a_full_agent_profile(self) -> None:
        with self.assertRaisesRegex(ValueError, "full tool profile"):
            bridge.gateway_credentials(self.config_path, allow_computer_control=True)
        config = copy.deepcopy(self.config)
        config["agents"]["list"][0]["tools"] = {
            "profile": "full", "exec": {"host": "gateway", "security": "full", "ask": "off"},
        }
        self.config_path.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, "tools.deny"):
            bridge.gateway_credentials(self.config_path)
        self.assertEqual(bridge.gateway_credentials(self.config_path, allow_computer_control=True)[0],
                         self.upstream.server_port)
        body = {"model": "openclaw:main", "messages": [{"role": "user", "content": "Test"}]}
        with self.assertLogs(level="ERROR"):
            self.assertEqual(self.request(body)[0], 502)
        self.server.allow_computer_control = True
        try:
            self.assertEqual(self.request(body, token="wrong")[0], 401)
            self.assertEqual(self.request(body, path="/tools/invoke")[0], 404)
            self.assertEqual(self.request(body)[0], 200)
            self.assertEqual(Upstream.records[-1][2]["model"], "openclaw:esp32")
            self.assertEqual(Upstream.records[-1][1]["x-openclaw-agent-id"], "esp32")
        finally:
            self.server.allow_computer_control = False
        config["gateway"]["bind"] = "lan"
        self.config_path.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, "loopback"):
            bridge.gateway_credentials(self.config_path, allow_computer_control=True)

    def test_server_identity_is_verified_and_prepare_preserves_existing_trust(self) -> None:
        connection = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=5)
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.context.wrap_socket(connection, server_hostname="wrong-host.local")
        before = (self.state / "ca.pem").read_bytes()
        bridge.prepare(self.state)
        self.assertEqual((self.state / "ca.pem").read_bytes(), before)
        self.assertEqual((self.state / "bridge.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.state / "server-key.pem").stat().st_mode & 0o777, 0o600)


class FakeBoard:
    def __init__(self, supported: bool = True) -> None:
        self.device = {"provider": "openai", "chat_provider": "openai",
                       "openclaw": {"supported": supported, "token_set": False}}
        self.writes: list[str] = []

    def status(self) -> dict:
        return {"device": self.device}

    def write_line(self, line: str) -> None:
        self.writes.append(line)
        name, value = line.split("=", 1)
        self.device["last"] = name + ": ok"
        if name == "openclaw.token":
            self.device["openclaw"]["token_set"] = bool(value)
        elif name == "openclaw.url":
            self.device["chat_provider"] = "openclaw" if value else "openai"


class SetupTest(unittest.TestCase):
    def test_unsupported_firmware_rejected_before_reading_or_sending_credentials(self) -> None:
        board = FakeBoard(False)
        with self.assertRaises(setup.BoardError):
            setup.configure(board, "https://host:8765/v1/chat/completions", Path("/missing"))
        self.assertEqual(board.writes, [])

    def test_setup_validates_url_before_sending_token(self) -> None:
        board = FakeBoard()
        for url in ("http://host/v1/chat/completions", "https://user@host/v1/chat/completions",
                    "https://host/v1/chat/completions?key=bad",
                    "https://host:0/v1/chat/completions", "https://host:65536/v1/chat/completions",
                    "https://host:bad/v1/chat/completions", "https://host/v1/chat/completions\n"):
            with self.assertRaises(setup.BoardError):
                setup.configure(board, url, Path("/missing"))
        self.assertEqual(board.writes, [])

    def test_configure_saves_separate_bridge_token_before_enabling_route(self) -> None:
        board = FakeBoard()
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "bridge.json").write_text(json.dumps({"device_token": "x" * 43}))
            setup.configure(board, "https://host:8765/v1/chat/completions", state)
        self.assertEqual(board.writes, ["openclaw.token=" + "x" * 43,
                                       "openclaw.url=https://host:8765/v1/chat/completions"])
        self.assertEqual(board.device["chat_provider"], "openclaw")

    def test_production_settings_validate_urls_tokens_and_persistence_failures(self) -> None:
        root = TOOLS.parents[1]
        source = (root / "components/muse/muse_settings.c").read_text()
        start = source.index("void muse_settings_openclaw(")
        end = source.index("void muse_settings_set_volume(", start)
        harness = (root / "tests/openclaw_settings_harness.c").read_text()
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            (tmp / "settings.c").write_text(
                harness.replace("/* OPENCLAW_SETTINGS_IMPLEMENTATION */", source[start:end]))
            (tmp / "esp_err.h").write_text("#pragma once\ntypedef int esp_err_t;\n#define ESP_OK 0\n")
            binary = tmp / "settings"
            result = subprocess.run([
                *shlex.split(os.environ.get("CC", "cc")), "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                "-I", str(tmp), "-I", str(root / "tests"),
                "-I", str(root / "components/muse"), str(tmp / "settings.c"), "-o", str(binary),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
