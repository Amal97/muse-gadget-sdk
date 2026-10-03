#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Configure standalone OpenAI firmware over USB; no runtime computer bridge."""
from __future__ import annotations

import argparse
import getpass
import sys
import time

from chat import Board, BoardError, pick_port


def status(board: Board) -> dict:
    frame = board.status()
    if frame is None:
        raise BoardError("The board is not answering. Close other serial monitors and try again.")
    device = frame.get("device", {})
    if device.get("provider") != "openai":
        raise BoardError("This board is not running standalone OpenAI firmware. Flash it first.")
    return device


def command(board: Board, name: str, value: str | None = None) -> None:
    line = name if value is None else f"{name}={value}"
    if "\n" in line or "\r" in line or "\0" in line or len(line.encode("utf-8")) >= 1200:
        raise BoardError("Invalid setup value: contains a line break or is too long.")
    board.write_line(line)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = status(board).get("last", "")
        if result.startswith(name + ": "):
            if result != name + ": ok":
                raise BoardError(result)
            return
        time.sleep(0.1)
    raise BoardError(f"{name} was not acknowledged; configuration is unconfirmed.")


def wait_wifi(board: Board, timeout: float = 45) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        device = status(board)
        if device["wifi"]["state"] == "connected":
            return
        time.sleep(1)
    raise BoardError("Wi-Fi did not connect. Check the network and password in the device settings.")


def test_key(board: Board, timeout: float = 60) -> None:
    command(board, "openai.test")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        api = status(board)["openai"]
        if api["state"] == "Connected":
            return
        if api["state"] != "Working":
            raise BoardError(api["detail"] or api["state"])
        time.sleep(0.5)
    raise BoardError("OpenAI key test timed out. Check internet access and the device's OpenAI settings.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port")
    parser.add_argument("--wifi", action="store_true", help="prompt for Wi-Fi credentials")
    parser.add_argument("--key", action="store_true", help="prompt privately for an API key")
    parser.add_argument("--test", action="store_true", help="test API access without generating a reply")
    args = parser.parse_args()
    try:
        if (args.wifi or args.key) and not sys.stdin.isatty():
            raise BoardError("Run credential setup in an interactive terminal so secret prompts stay hidden.")
        with Board(args.port or pick_port()) as board:
            device = status(board)
            if args.wifi:
                ssid = input("Wi-Fi network (2.4 GHz): ")
                password = getpass.getpass("Wi-Fi password (empty for open network): ")
                if not ssid or len(ssid.encode("utf-8")) > 32 or len(password.encode("utf-8")) > 64:
                    raise BoardError("Wi-Fi network must be 1-32 bytes; password must be at most 64 bytes.")
                command(board, "wifi.ssid", ssid)
                command(board, "wifi.pass", password)
                command(board, "wifi.connect")
                print("Joining Wi-Fi...")
                wait_wifi(board)
            if args.key:
                key = getpass.getpass("OpenAI API key (stored on the board; not encrypted): ")
                if not key or len(key) > 1023 or any(not 0x20 < ord(c) < 0x7F for c in key):
                    raise BoardError("API key must be 1-1023 printable ASCII characters, without spaces.")
                command(board, "openai.key", key)
                key = ""
                print("API key saved on the board.")
            if args.test:
                wait_wifi(board)
                test_key(board)
                print("OpenAI API key accepted. Hold the board's talk button to speak.")
            else:
                device = status(board)
                print(f"Wi-Fi: {device['wifi']['state']}; API key: "
                      f"{'set' if device['openai']['key_set'] else 'not set'}.")
    except (BoardError, EOFError, KeyboardInterrupt) as error:
        print(f"Setup stopped: {error or 'cancelled'}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
