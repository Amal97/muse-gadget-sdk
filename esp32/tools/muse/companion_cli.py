#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Call the private gadget companion API with one JSON request, or JSON on stdin."""
import argparse
from contextlib import closing
import http.client
import ipaddress
import json
from pathlib import Path
import socket
import sqlite3
import ssl
import sys
from urllib.parse import urlsplit

STATE = Path.home() / ".openclaw/muse-esp32"
JSON_CAP = 32768


def call(body: dict, state: Path = STATE, *, controller: bool = False) -> dict:
    config = json.loads((state / "bridge.json").read_text())
    with closing(sqlite3.connect((state / "companion.sqlite").resolve().as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT value FROM metadata WHERE key='endpoint'").fetchone()
    if row is None:
        raise ValueError("Companion endpoint has not been configured by the bridge.")
    endpoint = urlsplit(json.loads(row[0]))
    if endpoint.scheme != "https" or endpoint.path != "/v1/companion" or endpoint.query or (
            endpoint.username or endpoint.password or not endpoint.hostname
            or not endpoint.hostname.lower().endswith(".local") and
            not ipaddress.ip_address(endpoint.hostname).is_private):
        raise ValueError("Invalid private companion endpoint.")
    path = "/v1/copilot/controller" if controller else endpoint.path
    token = json.loads((state / "copilot-controller.json").read_text())["token"] if controller else config["device_token"]
    context = ssl.create_default_context(cafile=str(state / "ca.pem"))
    addresses = socket.getaddrinfo(endpoint.hostname, endpoint.port or 8765, socket.AF_INET, socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(address[4][0]).is_private for address in addresses):
        raise ValueError("Companion hostname did not resolve to a private address.")
    raw = socket.create_connection(addresses[0][4], timeout=30)
    try:
        tls = context.wrap_socket(raw, server_hostname="muse-openclaw.local")
    except (OSError, ValueError):
        raw.close()
        raise
    connection = http.client.HTTPConnection("muse-openclaw.local", endpoint.port or 8765, timeout=30)
    connection.sock = tls
    try:
        connection.request("POST", path, json.dumps(body).encode(), {
            "Content-Type": "application/json", "Authorization": "Bearer " + token})
        response = connection.getresponse()
        data = response.read(JSON_CAP)
        if len(data) >= JSON_CAP:
            raise ValueError("Companion response exceeds the supported limit.")
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError("Invalid companion response.")
        if response.status != 200:
            error = value.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            if not isinstance(message, str) or not message.strip():
                raise RuntimeError(f"Companion HTTP {response.status}; invalid bridge error response.")
            raise RuntimeError(f"Companion HTTP {response.status}: {message}")
        return value
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", nargs="?")
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--copilot-controller", action="store_true", help="Use the private desktop controller credential.")
    args = parser.parse_args()
    try:
        body = json.loads(args.request if args.request else sys.stdin.read(JSON_CAP))
        if not isinstance(body, dict):
            raise ValueError("Expected one JSON request object.")
        print(json.dumps(call(body, args.state, controller=args.copilot_controller), ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, KeyError, sqlite3.Error, http.client.HTTPException) as error:
        print(f"Companion request failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
