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

from openclaw_messages import MessageInbox

STATE = Path.home() / ".openclaw/muse-esp32"
GATEWAY_CONFIG = Path.home() / ".openclaw/openclaw.json"
IDENTITY = "muse-openclaw.local"
CHAT_PATH = "/v1/chat/completions"
NOTIFICATION_PATH = "/v1/notifications"
JSON_CAP = 16384
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
                 allow_computer_control: bool = False, imessages: bool = False) -> None:
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
        self.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.tls.minimum_version = ssl.TLSVersion.TLSv1_2
        self.tls.load_cert_chain(state / "server.pem", state / "server-key.pem")
        super().__init__(address, BridgeHandler)
        if self.inbox is not None:
            self.inbox.start()

    def server_close(self) -> None:
        if self.inbox is not None:
            self.inbox.close()
        super().server_close()

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
        if self.path not in (CHAT_PATH, NOTIFICATION_PATH):
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
            if self.path == NOTIFICATION_PATH:
                if not isinstance(body, dict) or set(body) != {"ack"}:
                    raise ValueError("Expected a notification acknowledgment.")
                if server.inbox is None:
                    self.reject(404, "Incoming iMessages are not enabled.")
                    return
                result = server.inbox.poll(body["ack"])
                self.send_json(200, json.dumps(result, ensure_ascii=False).encode())
                return
            messages = validate_messages(body)
        except (ValueError, UnicodeError):
            self.reject(400, "Invalid bridge request.")
            return
        except (RuntimeError, sqlite3.Error):
            logging.error("Incoming iMessages unavailable; check watcher status and Messages permissions.")
            self.reject(503, "Incoming iMessages unavailable; check the bridge log.")
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
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.action == "prepare":
            prepare(args.state)
        else:
            with BridgeServer((args.bind, args.port), args.state, args.openclaw_config,
                              allow_computer_control=args.allow_computer_control,
                              imessages=args.imessages) as server:
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
