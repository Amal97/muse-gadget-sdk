#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Narrow HTTPS chat bridge to a loopback-only OpenClaw gateway."""
from __future__ import annotations

import argparse
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import ssl
import subprocess
import sys
import threading
import time

from openclaw_messages import MessageInbox
from openclaw_jobs import GatewayRPC, JobManager
from openclaw_companion import Companion
import normal_chrome

STATE = Path.home() / ".openclaw/muse-esp32"
GATEWAY_CONFIG = Path.home() / ".openclaw/openclaw.json"
IDENTITY = "muse-openclaw.local"
CHAT_PATH = "/v1/chat/completions"
NOTIFICATION_PATH = "/v1/notifications"
JOB_PATHS = ("/v1/jobs/start", "/v1/jobs/status", "/v1/jobs/cancel")
COMPANION_PATH = "/v1/companion"
JSON_CAP = 32768
TEXT_CAP = 2048


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object.")
    return value


def object_field(value: dict, key: str) -> dict:
    field = value.get(key, {})
    if not isinstance(field, dict):
        raise ValueError(f"OpenClaw configuration field '{key}' must be an object.")
    return field


def gateway_credentials(path: Path, *, allow_computer_control: bool = False) -> tuple[int, str]:
    config = read_object(path)
    gateway = object_field(config, "gateway")
    if gateway.get("bind", "loopback") != "loopback":
        raise ValueError("Keep the OpenClaw gateway bound to loopback.")
    endpoints = object_field(object_field(gateway, "http"), "endpoints")
    if object_field(endpoints, "chatCompletions").get("enabled") is not True:
        raise ValueError("Enable the OpenClaw chatCompletions HTTP endpoint.")
    auth = object_field(gateway, "auth")
    token = auth.get("token")
    if auth.get("mode") != "token" or not isinstance(token, str) or not token:
        raise ValueError("Configure token authentication on the local OpenClaw gateway.")
    port = gateway.get("port", 18789)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("Invalid OpenClaw gateway port.")
    agents = object_field(config, "agents").get("list", [])
    if not isinstance(agents, list):
        raise ValueError("OpenClaw agents.list must be an array.")
    agent = next((a for a in agents if isinstance(a, dict) and a.get("id") == "esp32"), None)
    if agent is None:
        raise ValueError("Create a dedicated 'esp32' agent.")
    tools = object_field(agent, "tools")
    denied = tools.get("deny", [])
    if not isinstance(denied, list):
        raise ValueError("The ESP32 agent's tools.deny must be an array.")
    if allow_computer_control:
        if tools.get("profile") != "full" or "*" in denied:
            raise ValueError("Computer-control mode requires the ESP32 agent's full tool profile.")
    elif "*" not in denied:
        raise ValueError("Create a dedicated 'esp32' agent with tools.deny=['*'].")
    return port, token


def validate_messages(body: object) -> list[dict[str, str]]:
    if not isinstance(body, dict):
        raise ValueError("Expected a JSON object.")
    messages = body.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 10:
        raise ValueError("Expected 1-10 messages.")
    result = []
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("Invalid message.")
        role, content = message.get("role"), message.get("content")
        if role not in ("system", "user", "assistant") or not isinstance(content, str):
            raise ValueError("Only text system, user, and assistant messages are supported.")
        if not 0 < len(content.encode("utf-8")) < TEXT_CAP:
            raise ValueError("Message text must be 1-2047 UTF-8 bytes.")
        result.append({"role": role, "content": content})
    if result[-1]["role"] != "user":
        raise ValueError("The last message must be from the user.")
    return result


def agent_reply(config_path: Path, messages: list[dict[str, str]], *,
                allow_computer_control: bool = False) -> bytes:
    port, token = gateway_credentials(config_path, allow_computer_control=allow_computer_control)
    payload = json.dumps({"model": "openclaw:esp32", "messages": messages,
                          "stream": False, "max_completion_tokens": 240}).encode()
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=70)
    try:
        connection.request("POST", CHAT_PATH, payload, {
            "Content-Type": "application/json", "Authorization": "Bearer " + token,
            "x-openclaw-agent-id": "esp32",
        })
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"OpenClaw gateway returned HTTP {response.status}.")
        data = response.read(JSON_CAP)
        if len(data) >= JSON_CAP:
            raise ValueError("OpenClaw response exceeds the device limit.")
        body = json.loads(data)
        choices = body.get("choices") if isinstance(body, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("OpenClaw returned no valid choice.")
        choice = choices[0]
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not 0 < len(content.encode("utf-8")) < TEXT_CAP:
            raise ValueError("OpenClaw reply is empty or too long.")
        if content == "No response from OpenClaw." or choice.get("finish_reason") == "length":
            raise ValueError("OpenClaw did not complete the reply.")
        return json.dumps({"choices": [{"message": {"role": "assistant", "content": content},
                                        "finish_reason": "stop"}]}, ensure_ascii=False).encode()
    finally:
        connection.close()


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: Path, config_path: Path, *,
                 allow_computer_control: bool = False, imessages: bool = False,
                 companion: bool = False) -> None:
        gateway_credentials(config_path, allow_computer_control=allow_computer_control)
        token = read_object(state / "bridge.json").get("device_token")
        if not isinstance(token, str) or len(token) < 32 or not token.isascii():
            raise ValueError("Invalid bridge token. Run the prepare command.")
        self.device_token = token
        self.config_path = config_path
        self.allow_computer_control = allow_computer_control
        self.chat_lock = threading.Lock()
        self.clients = threading.BoundedSemaphore(8)
        self.inbox = MessageInbox(state) if imessages else None
        self.jobs = None
        self.companion = None
        self.monitor_stop = threading.Event()
        self.monitor_thread = None
        self.connectivity = {"gateway": "Not checked", "browser": "Not checked", "checked_at": None}
        if companion:
            self.verify_jobs()
        self.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.tls.minimum_version = ssl.TLSVersion.TLSv1_2
        self.tls.load_cert_chain(state / "server.pem", state / "server-key.pem")
        super().__init__(address, BridgeHandler)
        if companion:
            try:
                self.companion = Companion(state)
                host = f"[{address[0]}]" if ":" in address[0] else address[0]
                self.companion.put("endpoint", f"https://{host}:{self.server_address[1]}{COMPANION_PATH}")
                self.jobs = JobManager(
                    state, GatewayRPC(config_path, self.verify_jobs), self.chat_lock,
                    durable_conversation=True,
                    notify=lambda job_id, status, text: self.companion.alert(
                        "job", "Task completed" if status == "completed" else "Task needs attention",
                        text, job_id))
                self.companion.start()
                self.monitor_thread = threading.Thread(target=self.monitor,
                                                       name="companion-connectivity", daemon=True)
                self.monitor_thread.start()
            except (OSError, ValueError, RuntimeError, sqlite3.Error):
                if self.jobs is not None:
                    self.jobs.close()
                if self.companion is not None:
                    self.companion.close()
                super().server_close()
                raise
        if self.inbox is not None:
            self.inbox.start()

    def verify_jobs(self) -> tuple[int, str]:
        credentials = gateway_credentials(
            self.config_path, allow_computer_control=self.allow_computer_control)
        if self.allow_computer_control:
            agents = read_object(self.config_path)["agents"]["list"]
            agent = next(a for a in agents if isinstance(a, dict) and a.get("id") == "esp32")
            if "process" not in object_field(agent, "tools").get("deny", []):
                raise ValueError("Device jobs require tools.deny to include 'process' "
                                 "on the ESP32 agent for foreground cancellation.")
        return credentials

    def server_close(self) -> None:
        self.monitor_stop.set()
        if self.jobs is not None:
            self.jobs.close()
        if self.companion is not None:
            self.companion.close()
        if self.monitor_thread is not None:
            self.monitor_thread.join(timeout=70)
            if self.monitor_thread.is_alive():
                logging.error("Connectivity monitor did not stop before shutdown.")
        if self.inbox is not None:
            self.inbox.close()
        super().server_close()

    def monitor(self) -> None:
        rpc = GatewayRPC(self.config_path, self.verify_jobs)
        while not self.monitor_stop.is_set():
            status = {"gateway": "Unavailable", "browser": "Unavailable", "checked_at": time.time()}
            try:
                result = rpc("agent.wait", {"runId": "muse-connectivity-probe", "timeoutMs": 0})
                if result.get("status") != "timeout":
                    raise ValueError("Invalid gateway probe response.")
                status["gateway"] = "Reachable"
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
                logging.warning("Gateway connectivity probe failed.")
            try:
                result = json.loads(normal_chrome.execute("list_pages", ["{}"]))
                if not isinstance(result, dict):
                    raise ValueError("Invalid browser probe response.")
                status["browser"] = "Connected"
            except (OSError, ValueError, normal_chrome.ChromeError):
                logging.warning("Normal Chrome connectivity probe failed; verify browser consent.")
            self.connectivity = status
            self.monitor_stop.wait(60)

    def process_request(self, request, client_address) -> None:
        if not self.clients.acquire(blocking=False):
            self.shutdown_request(request)
            logging.warning("Bridge connection limit reached.")
            return
        try:
            super().process_request(request, client_address)
        except RuntimeError:
            self.clients.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.clients.release()

    def get_request(self) -> tuple[ssl.SSLSocket, tuple[str, int]]:
        connection, address = super().get_request()
        connection.settimeout(15)
        try:
            return self.tls.wrap_socket(connection, server_side=True), address
        except OSError:
            connection.close()
            logging.warning("Bridge rejected a failed TLS handshake.")
            raise


class BridgeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        logging.info("Bridge HTTP %s", code)

    def send_json(self, status: int, data: bytes) -> None:
        if len(data) >= JSON_CAP:
            logging.error("Bridge response exceeds the device JSON limit.")
            status = 413
            data = json.dumps({"error": {"message": "Response exceeds the device JSON limit."}}).encode()
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def reject(self, status: int, message: str) -> None:
        self.send_json(status, json.dumps({"error": {"message": message}}).encode())

    def do_POST(self) -> None:
        server = self.server
        if not isinstance(server, BridgeServer):
            raise RuntimeError("Invalid bridge server.")
        if self.path not in (CHAT_PATH, NOTIFICATION_PATH, COMPANION_PATH, *JOB_PATHS):
            self.reject(404, "Unknown bridge endpoint.")
            return
        authorization = self.headers.get("Authorization", "").encode("utf-8")
        if not secrets.compare_digest(authorization, ("Bearer " + server.device_token).encode()):
            self.reject(401, "Invalid device token.")
            return
        if self.headers.get("Transfer-Encoding") is not None:
            self.reject(400, "Chunked requests are not supported.")
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            self.reject(415, "Expected application/json.")
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size < JSON_CAP:
                self.reject(413, "Request exceeds the device limit.")
                return
            data = self.rfile.read(size)
            if len(data) != size:
                raise ValueError("Incomplete request.")
            body = json.loads(data)
            if self.path == COMPANION_PATH:
                if server.companion is None:
                    self.reject(404, "Device companion features are not enabled.")
                    return
                if isinstance(body, dict) and body.get("action") == "job_cancel":
                    if set(body) != {"action", "id"} or server.jobs is None:
                        raise ValueError("Invalid job cancellation request.")
                    result = {"last_job": server.jobs.cancel(body["id"])}
                elif isinstance(body, dict) and body.get("action") in (
                        "memory_add", "memory_forget", "memory_list", "conversation_reset"):
                    if server.jobs is None:
                        raise RuntimeError("Personal companion features are not enabled.")
                    action = body["action"]
                    if action == "memory_add" and set(body) == {"action", "text"}:
                        result = server.jobs.memory_add(body["text"])
                    elif action == "memory_forget" and set(body) == {"action", "id"}:
                        result = server.jobs.memory_forget(body["id"])
                    elif action == "memory_list" and set(body) == {"action", "offset"}:
                        result = server.jobs.memory_page(body["offset"])
                    elif action == "conversation_reset" and set(body) == {"action"}:
                        result = server.jobs.reset_conversation()
                    else:
                        expected = {"memory_add": "action,text", "memory_forget": "action,id",
                                    "memory_list": "action,offset", "conversation_reset": "action"}[action]
                        self.reject(400, "Invalid personal companion request fields. Expected: " + expected)
                        return
                else:
                    result = server.companion.handle(body, server.inbox)
                if server.jobs is not None:
                    result.update(server.jobs.personal_status())
                if isinstance(body, dict) and body.get("action") == "status":
                    result.update({"connectivity": server.connectivity,
                                   "served_at": time.time(),
                                   "last_job": server.jobs.latest() if server.jobs else None,
                                   "usage": server.jobs.costs() if server.jobs else None})
                self.send_json(200, json.dumps(result, ensure_ascii=False).encode())
                return
            if self.path in JOB_PATHS:
                if server.jobs is None:
                    self.reject(404, "Device companion jobs are not enabled.")
                    return
                if not isinstance(body, dict):
                    raise ValueError("Expected a device job request.")
                if self.path == JOB_PATHS[0]:
                    if set(body) != {"id", "messages"}:
                        raise ValueError("Expected device job identifier and messages.")
                    result = server.jobs.start(body["id"], validate_messages(body))
                else:
                    if set(body) != {"id"}:
                        raise ValueError("Expected a device job identifier.")
                    operation = server.jobs.status if self.path == JOB_PATHS[1] else server.jobs.cancel
                    result = operation(body["id"])
                self.send_json(200, json.dumps(result, ensure_ascii=False).encode())
                return
            if self.path == NOTIFICATION_PATH:
                if not isinstance(body, dict) or set(body) != {"ack"}:
                    raise ValueError("Expected a notification acknowledgment.")
                if server.inbox is None and server.companion is None:
                    self.reject(404, "Incoming iMessages are not enabled.")
                    return
                ack = body["ack"]
                if not isinstance(ack, str):
                    raise ValueError("Invalid notification acknowledgment.")
                if ack:
                    from openclaw_jobs import identifier
                    identifier(ack)
                local_ack = bool(server.companion and server.companion.owns_ack(ack))
                if local_ack:
                    server.companion.poll(ack)
                if ack and not local_ack:
                    if server.inbox is None:
                        raise ValueError("Unknown notification acknowledgment.")
                    server.inbox.poll(ack)
                result = server.companion.poll() if server.companion else {"notification": None}
                if result["notification"] is None and server.inbox is not None:
                    result = server.inbox.poll("")
                    if server.companion and result["notification"]:
                        result["notification"]["kind"] = "imessage"
                self.send_json(200, json.dumps(result, ensure_ascii=False).encode())
                return
            messages = validate_messages(body)
        except (ValueError, UnicodeError):
            self.reject(400, "Invalid bridge request.")
            return
        except (OSError, RuntimeError, sqlite3.Error, subprocess.TimeoutExpired):
            logging.error("Device service unavailable; check the bridge log and permissions.")
            self.reject(503, "Device service unavailable; check the bridge log.")
            return
        if not server.chat_lock.acquire(blocking=False):
            self.reject(409, "Another chat request is still running.")
            return
        try:
            reply = agent_reply(server.config_path, messages,
                                allow_computer_control=server.allow_computer_control)
        except (OSError, http.client.HTTPException, RuntimeError, ValueError):
            logging.exception("OpenClaw chat request failed.")
            self.reject(502, "OpenClaw request failed; check the bridge log.")
            return
        finally:
            server.chat_lock.release()
        self.send_json(200, reply)


def prepare(state: Path) -> None:
    previous_mask = os.umask(0o077)
    try:
        prepare_identity(state)
    finally:
        os.umask(previous_mask)


def prepare_identity(state: Path) -> None:
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (state / "bridge.json").exists():
        for name in ("ca.pem", "server.pem", "server-key.pem"):
            if not (state / name).is_file():
                raise ValueError("Bridge state is incomplete; restore its certificates before continuing.")
        print(f"Keeping existing bridge identity. Public CA: {state / 'ca.pem'}")
        return
    if any(state.iterdir()):
        raise ValueError("State directory is not empty; refusing to replace a possibly trusted identity.")
    commands = [
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
         "-keyout", str(state / "ca-key.pem"), "-out", str(state / "ca.pem"),
         "-subj", "/CN=Muse-OpenClaw-CA", "-addext", "basicConstraints=critical,CA:TRUE",
         "-addext", "keyUsage=critical,keyCertSign,cRLSign"],
        ["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(state / "server-key.pem"), "-out", str(state / "server.csr"),
         "-subj", f"/CN={IDENTITY}"],
        ["openssl", "x509", "-req", "-in", str(state / "server.csr"),
         "-CA", str(state / "ca.pem"), "-CAkey", str(state / "ca-key.pem"),
         "-CAcreateserial", "-out", str(state / "server.pem"), "-days", "825",
         "-extfile", str(state / "server.ext")],
    ]
    (state / "server.ext").write_text(
        f"subjectAltName=DNS:{IDENTITY}\nbasicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n")
    for command in commands:
        subprocess.run(command, check=True, capture_output=True, text=True)
    (state / "bridge.json").write_text(json.dumps({"device_token": secrets.token_urlsafe(32)}) + "\n")
    (state / "server.csr").unlink()
    (state / "server.ext").unlink()
    print(f"Bridge identity prepared. Embed only the public CA: {state / 'ca.pem'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "serve"))
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--openclaw-config", type=Path, default=GATEWAY_CONFIG)
    parser.add_argument("--bind", default="127.0.0.1", help="LAN IP for device access; default loopback")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--allow-computer-control", action="store_true",
                        help="explicitly permit the ESP32 agent's full computer-control tool profile")
    parser.add_argument("--imessages", action="store_true",
                        help="queue local incoming-iMessage previews (requires Messages Full Disk Access)")
    parser.add_argument("--companion", action="store_true",
                        help="enable jobs, reminders, confirmed replies and local morning briefings")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.action == "prepare":
            prepare(args.state)
        else:
            with BridgeServer((args.bind, args.port), args.state, args.openclaw_config,
                              allow_computer_control=args.allow_computer_control,
                              imessages=args.imessages, companion=args.companion) as server:
                if args.allow_computer_control:
                    logging.warning("Computer control enabled: device requests can run tools on this computer.")
                logging.info("Muse HTTPS chat bridge listening on %s:%d", args.bind, args.port)
                server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, sqlite3.Error, subprocess.CalledProcessError) as error:
        logging.error("Bridge stopped: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
