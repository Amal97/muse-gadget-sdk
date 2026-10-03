#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Provision the optional OpenClaw chat bridge without printing its token."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from chat import Board, BoardError, pick_port
from openai_setup import command, status, wait_wifi


def configure(board: Board, url: str, state: Path) -> None:
    device = status(board)
    if not device.get("openclaw", {}).get("supported"):
        raise BoardError("Rebuild with MUSE_OPENCLAW and your bridge public CA, then flash first.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as error:
        raise BoardError("Use https://COMPUTER-IP:8765/v1/chat/completions.") from error
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.path != "/v1/chat/completions"
            or parts.query or parts.fragment or len(url.encode()) > 255
            or not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?/v1/chat/completions", url)
            or (port is not None and not 1 <= port <= 65535)):
        raise BoardError("Use https://COMPUTER-IP:8765/v1/chat/completions.")
    token = json.loads((state / "bridge.json").read_text()).get("device_token")
    if (not isinstance(token, str) or not 32 <= len(token) <= 1023
            or any(not 0x20 < ord(c) < 0x7f for c in token)):
        raise BoardError("Invalid bridge token; run openclaw_bridge.py prepare.")
    command(board, "openclaw.token", token)
    command(board, "openclaw.url", url)
    device = status(board)
    if device.get("chat_provider") != "openclaw" or not device["openclaw"]["token_set"]:
        raise BoardError("OpenClaw settings were not confirmed.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port")
    parser.add_argument("--url", help="HTTPS bridge chat endpoint")
    parser.add_argument("--bridge-state", type=Path, default=Path.home() / ".openclaw/muse-esp32")
    parser.add_argument("--disable", action="store_true", help="explicitly return to direct OpenAI chat")
    parser.add_argument("--test", action="store_true", help="generate a short OpenClaw reply (billed)")
    args = parser.parse_args()
    if bool(args.url) == args.disable:
        parser.error("Choose --url or --disable.")
    try:
        with Board(args.port or pick_port()) as board:
            if not status(board).get("openclaw", {}).get("supported"):
                raise BoardError("Flash OpenClaw-capable standalone firmware first.")
            if args.disable:
                command(board, "openclaw.url", "")
                command(board, "openclaw.token", "")
                print("Direct OpenAI chat selected; voice settings unchanged.")
            else:
                configure(board, args.url, args.bridge_state)
                print("OpenClaw chat selected; OpenAI transcription and speech unchanged.")
            if args.test:
                wait_wifi(board)
                reply = board.chat("Reply with one short sentence confirming you can hear me.")
                if not reply.complete or not reply.intact() or not reply.text:
                    raise BoardError("The chat test did not return a complete reply.")
                print(reply.text)
    except (BoardError, OSError, ValueError, EOFError, KeyboardInterrupt) as error:
        print(f"Setup stopped: {error or 'cancelled'}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
