#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Use consent-enabled normal Chrome, never an isolated browser profile."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

STATE = Path.home() / ".openclaw/muse-esp32"
CLI = STATE / "browser-tools/node_modules/.bin/chrome-devtools"
DISCOVERY = Path.home() / "Library/Application Support/Google/Chrome/DevToolsActivePort"
SESSION = "e532c0de"
VERSION = "1.10.1"
REQUIRED = {"--auto-connect", "--channel=stable",
            "--no-usage-statistics", "--no-performance-crux"}
RESERVED = {"session-id", "sessionId", "auto-connect", "autoConnect", "browser-url",
            "browserUrl", "ws-endpoint", "wsEndpoint", "user-data-dir", "userDataDir",
            "channel", "isolated", "isolatedContext", "isolated-context", "headless", "usage-statistics", "usageStatistics",
            "performance-crux", "performanceCrux", "config"}


class ChromeError(Exception):
    pass


def invoke(arguments: list[str], *, timeout: int = 40) -> str:
    if not arguments or (arguments[0] not in ("status", "start", "stop")
                         and arguments[1:] != ["--help"]):
        raise ChromeError("Browser actions must use the existing daemon client, not CLI auto-start.")
    if not CLI.is_file():
        raise ChromeError("Install the pinned Chrome tools in ~/.openclaw/muse-esp32/browser-tools first.")
    environment = {**os.environ, "CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS": "1",
                   "CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS": "1"}
    try:
        result = subprocess.run([str(CLI), "--sessionId", SESSION, *arguments],
                                capture_output=True, text=True, timeout=timeout, env=environment)
    except subprocess.TimeoutExpired as error:
        raise ChromeError("Chrome operation timed out. Check Chrome's Allow prompt; do not repeat "
                          "an uncertain form submission or other action automatically.") from error
    if result.returncode:
        raise ChromeError(result.stderr.strip() or result.stdout.strip() or
                          f"Chrome command failed with exit {result.returncode}.")
    return result.stdout


def debugging_enabled() -> None:
    if not DISCOVERY.is_file():
        raise ChromeError("Open your normal Chrome, enable chrome://inspect/#remote-debugging, "
                          "and allow the connection. No isolated-browser fallback is used.")
    lines = DISCOVERY.read_text().splitlines()
    if len(lines) != 2 or not lines[0].isdigit() or not 1 <= int(lines[0]) <= 65535 \
            or not lines[1].startswith("/devtools/browser/"):
        raise ChromeError("Normal Chrome's debugging discovery is invalid. Re-enable it in Chrome.")


def daemon_running() -> bool:
    output = invoke(["status"], timeout=10)
    if "daemon is not running." in output:
        return False
    if "daemon is running." not in output:
        raise ChromeError("Unrecognized Chrome daemon status.")
    lines = output.splitlines()
    line = next((line for line in lines if line.startswith("args=")), None)
    if line is None:
        raise ChromeError("Chrome daemon did not report its configuration.")
    try:
        arguments = json.loads(line[5:])
    except ValueError as error:
        raise ChromeError("Invalid Chrome daemon configuration.") from error
    if not isinstance(arguments, list) or not all(isinstance(a, str) for a in arguments):
        raise ChromeError("Invalid Chrome daemon argument shape.")
    if not REQUIRED.issubset(arguments) or any(
            a.startswith(("--user-data-dir", "--browser-url", "--ws-endpoint", "--config"))
            for a in arguments):
        raise ChromeError("ESP32 Chrome daemon is not in private normal-Chrome mode. "
                          "Run this helper with stop, then retry.")
    if not any(f"version={VERSION}" in line for line in lines):
        raise ChromeError("Chrome daemon version differs from the pinned tools; stop and restart it.")
    return True


def ensure_daemon() -> None:
    debugging_enabled()
    if daemon_running():
        return
    files = STATE / "browser-files"
    files.mkdir(parents=True, exist_ok=True, mode=0o700)
    invoke(["start", "--autoConnect", "--channel", "stable", "--no-usage-statistics",
            "--no-performance-crux", "--workspace", str(files)], timeout=15)
    if not daemon_running():
        raise ChromeError("Chrome daemon failed to stay running.")


def submit_tool(command: str, parameters: dict) -> dict:
    node = shutil.which("node")
    client = CLI.parent.parent / "chrome-devtools-mcp/build/src/daemon/client.js"
    if node is None or not client.is_file():
        raise ChromeError("The pinned Chrome daemon client or Node.js is missing.")
    script = """
import fs from 'node:fs';
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const {sendCommand} = await import(input.client);
try {
    const response = await sendCommand(
        {method: 'invoke_tool', tool: input.tool, args: input.parameters}, input.session, 35000);
    if (!response.success) throw new Error(String(response.error));
    process.stdout.write(response.result);
    process.exit(0);
} catch (error) {
    console.error(error.message);
    process.exit(1);
}
"""
    environment = {**os.environ, "CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS": "1",
                   "CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS": "1"}
    payload = {"client": client.resolve().as_uri(), "session": SESSION,
               "tool": command, "parameters": parameters}
    try:
        result = subprocess.run([node, "--input-type=module", "-e", script],
                                input=json.dumps(payload), capture_output=True,
                                text=True, timeout=40, env=environment)
    except subprocess.TimeoutExpired as error:
        raise ChromeError("Chrome tool timed out; do not repeat an uncertain action automatically.") from error
    if result.returncode:
        raise ChromeError(result.stderr.strip() or "Chrome daemon request failed; no fallback was attempted.")
    try:
        response = json.loads(result.stdout)
    except ValueError as error:
        raise ChromeError("Chrome returned invalid tool JSON.") from error
    if not isinstance(response, dict):
        raise ChromeError("Chrome returned an invalid tool result.")
    return response


def execute(command: str, arguments: list[str]) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", command):
        raise ChromeError("Invalid Chrome tool name. Use real commands such as new_page, "
                          "evaluate_script, take_snapshot, navigate_page, click, fill, "
                          "close_page, or list_pages. Read the normal-chrome skill for "
                          "their JSON parameters; TOOL and OPEN_TAB are not commands.")
    if arguments == ["--help"]:
        return invoke([command, "--help"], timeout=10)
    if command == "stop":
        if arguments:
            raise ChromeError("stop takes no arguments.")
        return invoke(["stop"], timeout=10)
    if command == "status":
        if arguments:
            raise ChromeError("status takes no arguments.")
        ensure_daemon()
        return json.dumps({"running": True, "browser": "normal-chrome",
                           "auto_connect": True, "usage_statistics": False})
    if command == "start":
        raise ChromeError("No explicit start is needed; tools connect to normal Chrome automatically.")
    if len(arguments) > 1:
        raise ChromeError("Pass tool parameters as one JSON object, not positional arguments or flags.")
    try:
        parameters = json.loads(arguments[0]) if arguments else {}
    except ValueError as error:
        raise ChromeError("Tool parameters must be a JSON object.") from error
    if not isinstance(parameters, dict):
        raise ChromeError("Tool parameters must be a JSON object.")
    if any(key in RESERVED for key in parameters):
        raise ChromeError("Browser connection and privacy options cannot be overridden.")
    if command == "new_page" and parameters.get("background"):
        raise ChromeError("Create the new tab in the foreground so only its metadata is returned.")
    ensure_daemon()
    result = submit_tool(command, parameters)
    if result.get("isError"):
        hint = (" evaluate_script.function must be a callable JavaScript function, "
                "such as () => document.title, not a bare assignment. Read the "
                "normal-chrome skill for JSON-escaped string examples."
                if command == "evaluate_script" else "")
        raise ChromeError("Chrome tool failed: " + json.dumps(result.get("content", [])) + hint)
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        raise ChromeError("Chrome did not return the structured data needed for private tab routing.")
    if structured.get("errorMessage"):
        raise ChromeError("Chrome tool failed: " + str(structured["errorMessage"]))
    if command in ("new_page", "list_pages") and not isinstance(structured.get("pages"), list):
        raise ChromeError("Chrome did not return the page data needed for private tab routing.")
    if command != "list_pages":
        pages = structured.get("pages")
        if isinstance(pages, list):
            if command == "new_page":
                pages = [p for p in pages if isinstance(p, dict) and p.get("selected") is True]
                if len(pages) != 1:
                    raise ChromeError("Cannot identify the newly opened tab; do not open another automatically.")
            elif command in ("select_page", "navigate_page"):
                pages = [p for p in pages if isinstance(p, dict) and p.get("id") == parameters.get("pageId")]
            else:
                pages = []
            structured["pages"] = pages
    for key in ("extensionPages", "extensionServiceWorkers", "thirdPartyDeveloperTools"):
        structured.pop(key, None)
    if any(isinstance(part, dict) and part.get("type") == "image" for part in result.get("content", [])):
        raise ChromeError("Request screenshots with filePath under ~/.openclaw/muse-esp32/browser-files.")
    return json.dumps({"structuredContent": structured}, ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", help="Chrome tool, status, or stop")
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help="one JSON parameter object, or --help")
    args = parser.parse_args()
    try:
        print(execute(args.command, args.arguments))
    except (ChromeError, OSError) as error:
        print(f"Normal Chrome: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
