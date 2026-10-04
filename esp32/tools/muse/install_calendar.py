#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the local, read-only EventKit helper using Apple's existing developer tools."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path.home() / ".openclaw/muse-esp32")
    parser.add_argument("--request-access", action="store_true")
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    app = args.state / "Muse Calendar Reader.app"
    contents = app / "Contents"
    executable = contents / "MacOS/calendar-reader"
    try:
        executable.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / "calendar-reader-Info.plist", contents / "Info.plist")
        subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-O", "-o", str(executable),
                        "-Xlinker", "-sectcreate", "-Xlinker", "__TEXT",
                        "-Xlinker", "__info_plist", "-Xlinker", str(contents / "Info.plist"),
                        str(source / "calendar_reader.swift")], check=True)
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(app)], check=True)
        print("Installed Muse Calendar Reader. It only reads explicitly enabled event calendars.")
        if args.request_access:
            subprocess.run(["/usr/bin/open", "-n", str(app), "--args", "request-access"], check=True)
            print("Allow calendar access in the macOS permission dialog; then configure enabled calendars.")
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"Calendar helper installation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
