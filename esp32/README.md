<!--
Copyright (c) Meta Platforms, Inc. and affiliates.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# ESP32 Device SDK

For the complete installation and usage walkthrough for this fork's
OpenAI/OpenClaw personal companion, start with the
[project README](../README.md). This document retains the detailed firmware,
original Muse, board, and integration reference.

## Guide contents

- [What you need](#what-you-need)
- [Original Muse setup](#or-do-it-yourself)
- [Standalone OpenAI mode](#standalone-openai-mode)
- [OpenClaw setup](#optional-openclaw-chat-on-your-computer)
- [Computer control](#opting-into-computer-control)
- [Normal Chrome](#optional-normal-signed-in-chrome-macos-chrome-144)
- [iMessage](#optional-imessage-sending-and-incoming-previews-macos)
- [Companion/Home features](#optional-gadget-companion-features-openclaw-builds)
- [Copilot](#copilot-approvals-and-questions-from-vs-code)
- [Task dashboard and health](#copilot-task-dashboard-and-connection-health)
- [Routines and priorities](#personal-routines-and-local-priorities)
- [Memory and conversation saves](#personal-memory-and-durable-conversations)
- [Calendar alerts](#proactive-calendar-alerts)
- [Boards](#boards)
- [Development](#hack-and-extend-it)

Flash this open source firmware onto any ESP32-compatible board to connect
Muse to your home Wi-Fi. On boards with the home-network tunnel, Muse can reach
the devices you already own and anything you build with a local HTTP API.

Then hack on it: add a display, a button, or support for a board we haven't
tried yet, and build your own Muse gadget.

> **Note:** Built by hackers, for hackers, just for fun. Flashing custom
> firmware can brick boards and void warranties. Proceed at your own risk!

## What you need

For use without a Muse account, see [Standalone OpenAI mode](#standalone-openai-mode).
The Muse app and SDK token below are only required for the default Muse mode.

- **An ESP32 board.** The quickest start is the **ESP32-C5 DevKitC-1**, which
  works as-is with its built-in status light and BOOT button. The other boards
  that already work are listed [below](#boards).
- **A USB cable that carries data**, not just power.
- **A computer** running macOS or Linux.
- **An SDK token** from [gadgets.muse.ai](https://gadgets.muse.ai/settings/sdk-tokens)
  (Account > SDK tokens). Every gadget needs one to pair, including ones you
  build for yourself. Read the [Gadget SDK Terms](https://gadgets.muse.ai/sdk-terms)
  before you use it.
- **The Muse app** on your phone, to set up the device once it's flashed.

## Get going with Muse Code

The fastest way to build is to let [Muse Code](https://developer.meta.com/ai/lp/muse-code/)
do it. Install it:

```sh
curl -fsSL https://dev.meta.ai/install.sh | sh
```

Then plug in your board, and start Muse Code from this directory:

```sh
git clone https://github.com/facebookincubator/muse-gadget-sdk
cd muse-gadget-sdk/esp32
muse --disable-sandbox
```

The first time, Muse Code asks you to trust the workspace and sign in.
`--disable-sandbox` lets it reach your board's USB serial port and download
the ESP-IDF toolchain. You still approve each command before it runs. Then ask:

> Build this firmware for my ESP32-C5 DevKitC-1 and flash it.

Muse Code reads [`AGENTS.md`](AGENTS.md), which has everything needed to set up
the toolchain, build for your board, flash it, and read its logs. From there,
keep going:

> Watch the serial log and tell me when it's ready to pair.

> I have a Waveshare ESP32-S3 AMOLED board. Build the UI for it.

> Add support for my board. It's an ESP32-S3 with 16 MB flash, a button on
> GPIO 0 and no PSRAM.

> Make the status light half as bright.

Other coding agents work too. Any agent that reads `AGENTS.md` can build and
flash from this repository. Flashing needs access to your board's USB serial
port, so if your agent runs in a sandbox, let it run the flash and monitor
commands outside the sandbox.

## Or do it yourself

### 1. Install ESP-IDF v6.0.1

This firmware is built with Espressif's ESP-IDF, **version 6.0.1**. Other
versions aren't supported. If you already have it installed, skip ahead.

On macOS, install the prerequisites first with `brew install cmake ninja
dfu-util python3`. On Linux, follow
[Espressif's prerequisites](https://docs.espressif.com/projects/esp-idf/en/v6.0.1/esp32c5/get-started/linux-macos-setup.html).
Then:

```sh
git clone -b v6.0.1 --recursive https://github.com/espressif/esp-idf.git ~/esp/esp-idf-v6
~/esp/esp-idf-v6/install.sh esp32c5,esp32s3,esp32c6,esp32
. ~/esp/esp-idf-v6/export.sh
```

Run the last line in every new terminal you build from.

### 2. Build

From this directory, set your SDK token, then build:

```sh
idf.py menuconfig   # ESP32 Device SDK > Muse Gadgets SDK token
idf.py build
```

This builds for the ESP32-C5 DevKitC-1. The firmware lands in
`build/muse-gadget.bin`.

### 3. Flash

Plug in the board and flash it:

```sh
idf.py -p /dev/cu.usbmodem1101 flash monitor
```

Replace the port with your board's. On macOS, `ls /dev/cu.usb*` lists the
connected ports; on Linux, look for `/dev/ttyACM*` or `/dev/ttyUSB*`. The
monitor shows the device's log; press `Ctrl-]` to quit. If flashing can't
connect, hold **BOOT**, tap **RESET**, release **BOOT**, and try again.

Reflashing keeps your pairing and Wi-Fi settings. To start completely fresh,
run `idf.py -p PORT erase-flash` first.

### 4. Set it up with Muse

Once flashed, the status light breathes **orange**: the device is ready for
setup. In the Muse app, turn on **Settings > Devices > Developer mode**, then
add the device (**Settings > Devices > Add Device**, the **+** icon in the top
right). It shows up as `MuseGadget-XXXXXX`. When the light breathes **blue**, press the **BOOT**
button to confirm it's really you. The light turns **green** when Muse is
connected.

| Light | What it means |
|---|---|
| Orange, breathing | Ready for setup |
| Blue, breathing | Press the button to confirm pairing |
| Blue | Joining Wi-Fi and connecting to Muse |
| Green | Connected |
| Yellow, blinking | Reconnecting |
| Purple | Not paired |
| Red, blinking | Something went wrong: check the log |

To reset the device and set it up again, hold the button for 5 seconds.

Pairing requires a press of the button on the device, and every setup creates
a fresh encrypted session. Because these are community devices, pairing has no
manufacturer verification and can't prevent an active man-in-the-middle
attack. Set it up on a network you trust.

## Standalone OpenAI mode

The Waveshare ESP32-S3-Touch-AMOLED-1.75C and 1.75 can connect directly to
OpenAI without a Muse account, app, or computer bridge. This is an opt-in
firmware build; the default Muse build is unchanged.

You need 2.4 GHz Wi-Fi with internet access and an OpenAI API key with access
to the configured models and API billing enabled. A ChatGPT subscription does
not include API usage. Hold the talk button for up to 15 seconds, then release:
the board uploads a 16 kHz WAV, transcribes it, requests a short text answer,
and plays AI-generated speech. The display shows captions; muting skips the
speech API and pages the text at reading pace.

### Build and flash

With ESP-IDF v6.0.1 activated, from this directory:

```sh
idf.py -B build-openai-waveshare-s3-175c -DIDF_TARGET=esp32s3 \
  -DSDKCONFIG=build-openai-waveshare-s3-175c/sdkconfig \
  -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;devices/sdkconfig.muse;devices/sdkconfig.muse-waveshare-s3-175c;devices/sdkconfig.openai" \
  build
idf.py -B build-openai-waveshare-s3-175c -p /dev/cu.usbmodem2101 flash
```

Replace the serial port with yours. For the **1.75 (not 1.75C)**, replace
`waveshare-s3-175c` with `waveshare-s3-175` in all three places in the build
command and in the flash command. Do not reuse a Muse-mode build directory.

### Set up Wi-Fi and your API key

Swipe left to **Settings > Wi-Fi** to join a network, then open
**Settings > OpenAI** to enter a key and test API access. For easier entry,
use the USB setup tool in a local terminal:

```sh
python tools/muse/openai_setup.py --port /dev/cu.usbmodem2101 --wifi --key --test
```

It prompts privately for the Wi-Fi password and API key, sends them only over
USB, and confirms the device saved the key. It is a setup tool, not a runtime
bridge: unplug the computer afterward. Run it in the ESP-IDF environment,
which already includes pyserial. Do not pass secrets as command-line arguments
or paste them into chat. **The key is stored in NVS, not in source code or the
build configuration; this build does not encrypt it or burn security eFuses.**
Anyone with physical access may be able to extract it. Use a dedicated key,
configure project usage alerts/limits, and revoke the key if the device is lost.

**Test API key** checks chat-model access without generating a response.
Voice turns incur separate transcription, chat and speech charges. The
defaults are `gpt-4o-mini-transcribe`, `gpt-4o-mini`, `gpt-4o-mini-tts`, and
the `coral` voice. Change them under **Muse** in `menuconfig` and rebuild.
Model availability depends on your API account.

The last four completed user/assistant exchanges are kept in RAM. **New
conversation** clears them and cancels the current request; rebooting also
forgets them. **Remove API key** deletes the saved key and clears the
conversation. Requests use certificate-verified HTTPS and require network
time (SNTP). The public GTS Root R4 certificate in
[`components/muse/openai_root.pem`](components/muse/openai_root.pem) comes
from [Google's PKI repository](https://pki.goog/repo/certs/gtsr4.pem).
The complete root is embedded rather than ESP-IDF's compact bundle, so
certificate dates remain verified even with the cross-signed server chain.
If OpenAI changes its root CA, update this public certificate and reflash;
never bypass certificate checks. Quota, authentication, connectivity, invalid responses and
timeouts are shown explicitly; failed recordings are not retried automatically.
A new press cancels playback, but an HTTPS request already in progress can
take up to its 30-second socket timeout to stop. Retry after it finishes.

This mode does not provide Muse tools, home-device control, app pairing,
continuous realtime conversation, or OTA updates. Audio and text are sent
to OpenAI; the AI models do not run locally.

### Optional OpenClaw chat on your computer

Keep the same Muse avatar, screen, captions, settings and push-to-talk controls,
but use OpenClaw for text conversations. OpenAI still transcribes recordings
and generates speech. This does not require a Muse account, and is not a
replacement Arduino firmware for a generic ESP32 board.

The included Python-standard-library HTTPS bridge exposes the chat endpoint
and an opt-in local notification endpoint, authenticates the device with its
own token, and forwards chat to a
loopback-only OpenClaw gateway. By default it requires a dedicated `esp32`
agent with `tools.deny: ["*"]`. Clients cannot select your main agent or
access other gateway endpoints. Computer-control tools require the explicit
opt-in described below. OpenClaw's operator gateway token stays on the computer.
Do not expose either service to the internet or enable router port forwarding.

1. Install/onboard [OpenClaw](https://docs.openclaw.ai/start/getting-started)
   on your computer and configure a billed model provider. Preserve existing
   agents and credentials. Enable
   `gateway.http.endpoints.chatCompletions.enabled`, keep `gateway.bind` set
   to `loopback`, and use `gateway.auth.mode: "token"` with a token in the
   local configuration. Create an `esp32` agent with a separate workspace
   and a model such as `openai/gpt-4o-mini`, then set its `tools.deny` to
   `["*"]`. On OpenClaw 2026.2.9 the agents are in `agents.list`; find the
   new agent's index before setting `agents.list.INDEX.tools`.
   Start/restart the gateway after changing configuration.
2. Prepare a private bridge identity:

   ```sh
   python3 tools/muse/openclaw_bridge.py prepare
   ```

   This requires `openssl`, saves state in `~/.openclaw/muse-esp32`, and
   does not print tokens. Keep the existing CA when rerunning setup.
3. Build a separate firmware directory, embedding **only the public CA**:

   ```sh
   mkdir -p build-openclaw-waveshare-s3-175c
   printf 'CONFIG_MUSE_OPENCLAW_CA_CERT="%s"\n' \
     "$HOME/.openclaw/muse-esp32/ca.pem" \
     > build-openclaw-waveshare-s3-175c/sdkconfig.local
   idf.py -B build-openclaw-waveshare-s3-175c -DIDF_TARGET=esp32s3 \
     -DSDKCONFIG=build-openclaw-waveshare-s3-175c/sdkconfig \
     -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;devices/sdkconfig.muse;devices/sdkconfig.muse-waveshare-s3-175c;devices/sdkconfig.openai;devices/sdkconfig.openclaw;build-openclaw-waveshare-s3-175c/sdkconfig.local" \
     build
   idf.py -B build-openclaw-waveshare-s3-175c -p PORT flash
   ```

   For the 1.75 board, replace `waveshare-s3-175c` with `waveshare-s3-175`.
   Never commit bridge tokens, private keys, or local build configuration.
   The embedded CA and the fixed certificate identity `muse-openclaw.local`
   are both verified, even when connecting to a numeric LAN IP. Certificate
   date checks remain enabled; no insecure TLS fallback is used.
4. Run the bridge on your computer's private LAN address:

   ```sh
   python3 tools/muse/openclaw_bridge.py serve --bind COMPUTER-IP
   ```

   The default bind is loopback, which is useful for local tests but is not
   reachable from the ESP32. Allow the bridge port (8765) through your local
   firewall only on trusted networks. For DHCP recovery on macOS, use
   `serve --bind 0.0.0.0 --bonjour` instead. Provision the board URL with
   `https://$(scutil --get LocalHostName).local:8765/v1/chat/completions`
   and keep the other service flags unchanged. Bonjour mode accepts only
   loopback/RFC1918 IPv4 clients; the helper rejects non-private DNS results.
   Certificate verification still uses `muse-openclaw.local`, with the same
   embedded CA and credentials. It never falls back to HTTP or insecure TLS.
   The Mac and gadget must share a network that permits Bonjour/mDNS.
   Without Bonjour, reserve the computer's LAN address in your router or
   update the bridge bind and board URL when it changes.
5. With the existing OpenAI key and Wi-Fi saved on the board, provision
   the separate bridge token privately over USB:

   ```sh
   python tools/muse/openclaw_setup.py --port PORT \
     --url https://COMPUTER-IP:8765/v1/chat/completions --test
   ```

   `--test` generates a short, billed model reply through the actual ESP32
   connection. **Settings > OpenClaw > Test OpenAI key** tests OpenAI only.
   USB status reports `provider: "openai"` for the voice pipeline and
   `chat_provider: "openclaw"` for conversations.

Your computer must remain awake and reachable on the same network. There
is no automatic fallback to OpenAI chat when OpenClaw fails: failures are
displayed explicitly. The OpenClaw build's settings are labeled **OpenClaw**:
**Voice key** is the required OpenAI credential for speech recognition and
spoken replies, and **Mac bridge** and **Bridge token** configure chat/tools.
The standalone-backend switch is not shown, and leaving the bridge-address
editor empty preserves the current connection. For an intentional advanced
switch back to standalone chat, `openclaw_setup.py --port PORT --disable`
explicitly removes the bridge settings. The separate direct-OpenAI and
original Muse builds retain their existing settings.
Host UI coverage executes the production settings builders for all three
backends: `python3 -m unittest tests/test_muse_settings_ui.py`.

The OpenAI key and bridge token remain unencrypted in device NVS. Audio
goes to OpenAI; conversation text goes through the computer to OpenClaw's
configured provider. OpenClaw may save transcripts/session files on the
computer. In companion mode the Mac supplies bounded durable reference
history, while each task uses an independent OpenClaw session.
**New conversation** clears recent context, not explicitly saved personal
memories or previously saved computer logs. Existing
OpenClaw tools are unavailable in the default chat-only configuration.
Provider availability, billing, and regional restrictions still apply.
The generated server certificate expires after 825 days; renew it using
the existing CA before expiry. Replacing the CA requires rebuilding and
reflashing the board.

Host coverage includes `python3 -m unittest tests/test_muse_openai.py
tests/test_muse_openclaw.py tests/test_muse_messages.py`.

#### Opting into computer control

**This grants the device remote control of your computer, not just chat.**
Voice is not speaker authentication: anyone who can use the gadget or its
bridge token can send requests. Tool results may include computer data that
is sent to OpenClaw's configured model provider. Unrestricted execution can
modify or delete files, run programs, access your user account's data, and
change other OpenClaw settings. Keep the bridge private and protect the device.

To opt in, configure **only the dedicated `esp32` agent** with
`tools.profile: "full"` and remove its `tools.deny: ["*"]`. Set its sandbox
mode to `off` for host access. Then run the bridge with:

```sh
python3 tools/muse/openclaw_bridge.py serve --bind COMPUTER-IP \
  --allow-computer-control
```

Add that same flag to any bridge login-service configuration and restart
both services after policy changes. Without the flag, the bridge refuses
to serve an agent with computer-control tools, including after a live
configuration change.

Shell commands can retain OpenClaw's approval requirements. If you
deliberately want **unrestricted execution without approval prompts**, set
that agent's `tools.exec` to
`{"host":"gateway","security":"full","ask":"off"}` and set **only its**
host exec-approvals entry to
`{"security":"full","ask":"off","askFallback":"full"}`. Use
`openclaw approvals get/set` to preserve the existing approval file and
other agents' policies. Do not change global defaults or the main agent.

The firmware's OpenClaw prompt permits available tools and requires tool
results before claiming an action succeeded. It requests the managed
`openclaw` browser profile by default instead of assuming a browser relay is attached.
If your existing browser configuration uses `attachOnly`, start a separate
browser instance with that profile's debugging port and user-data directory
(and arrange login startup if desired); keep the debugging endpoint on
loopback. Do not turn off attach-only globally or reuse your everyday
browser profile merely to enable the gadget.
macOS privacy prompts, installed applications, browser availability, and
your account's filesystem/admin permissions still apply; this does not
grant root access or bypass macOS protections. Long actions are still
subject to the device's existing request timeouts.

While a device-owned job runs, the companion plugin records actual OpenClaw
tool events locally and the bridge forwards the latest event to the Talk
caption and dashboard. `Requested:` identifies a recorded tool call, not proof
that it executed. `Tool returned:` means a result was recorded, not necessarily
that the requested action succeeded; error results are labelled `Tool error:`.
Tool payloads, shell commands, page contents, and private reasoning are not
included in tool-activity captions. If an activity record cannot be read, the device
explicitly reports that activity is unavailable while still waiting for the
final result. Updates are polled, so quick intermediate steps may not appear;
there are no invented stages or elapsed-time progress estimates. Older bridges
that provide no activity detail display `No activity reported by Mac`.
Both the updated plugin/bridge and a firmware reflash are required for these
captions. Jobs without a tool event continue to show the request-acknowledged
status rather than an invented action. Activity files are private, scoped to
`agent:esp32:muse-job:<job-id>`, and removed when the job worker finishes.
If the bridge uses a custom `--state`, set the plugin's `activityDirectory`
to that state's absolute `job-activity` directory.

To revoke computer control, restore the ESP32 agent's `tools.deny: ["*"]`,
remove `--allow-computer-control` from the bridge service, and restart it.
Restore that agent's previous exec-approval policy as well. Use the
USB `openclaw_setup.py --port PORT --disable` command to disconnect the board
from OpenClaw entirely; the simplified OpenClaw settings omit that switch.

#### Native Mac voice controls

The companion plugin's optional `mac_control` tool exposes fixed native actions
for applications, output volume/mute, Bluetooth, and Wi-Fi. It does not accept
arbitrary scripts or change the ESP32's own sound settings.

From `esp32/`, install the updated plugin:

```sh
openclaw plugins install ./tools/muse/chrome-plugin
```

For an existing copied installation, first preserve that plugin's old directory
outside OpenClaw's extension discovery paths. Do not overwrite another plugin
or leave duplicate copies with the same ID active. You can then use
`openclaw plugins install --link ./tools/muse/chrome-plugin` to load this checkout
directly; keep its path stable when using a linked install. Do not move or erase
bridge credentials, certificates, personal state, or other agents' files.

For Bluetooth **power changes**, install the declared Homebrew dependency:

```sh
brew bundle --file tools/muse/chrome-plugin/Brewfile
```

Audio/apps/Wi-Fi do not require `blueutil`; Bluetooth status is read through
macOS System Profiler without sending paired-device details to the model.
Power changes may need Bluetooth permission for the actual gateway/tool host.
If macOS blocks or times out, the tool reports the error, not successful control.
Never use sudo or bypass the privacy permission to hide a failure.

Copy `tools/muse/mac-control-skill/SKILL.md` into the dedicated agent's workspace
at `skills/mac-control/SKILL.md`. Add **`mac_control`** to only the `esp32`
agent's `tools.alsoAllow`, preserving existing entries, model, exec approvals,
and denied tools. Keep its already opted-in full profile and sandbox policy.
Existing Chrome users keep `normal_chrome` allowed as well. The plugin ID remains
`muse-normal-chrome` for compatibility; allowing its older browser tool does not
automatically enable the new Mac tool.

Have the agent read the Mac-control skill and use `mac_control` for system actions,
and `normal_chrome` for websites. Restart the gateway after configuration changes.
No new firmware flash or touchscreen page is needed: use the existing Talk button.

Example voice requests:

- "Open Calculator on my Mac."
- "Is Safari running?"
- "Set my Mac volume to 30 percent."
- "Mute my Mac." / "Unmute my Mac."
- "Is Bluetooth on?" / "Turn Bluetooth on."
- "Is Mac Wi-Fi on?"

App launches, sound changes, and radio power changes read back the resulting state
before returning verified success. A running app is not proof of window readiness;
Wi-Fi power is not proof of internet connectivity. Native calls are bounded and
cancellable, but Stop cannot undo a completed action.

Bluetooth/Wi-Fi **off** is a two-request operation. The first request prepares a
three-minute, single-use confirmation and explains the disconnection risk without
changing power. In a separate gadget request, say "Confirm turning Bluetooth off"
or the matching Wi-Fi confirmation. The agent retrieves the exact pending ID;
you do not need to speak it. Same-request confirmations, wrong targets, expired
requests, and replayed confirmations are rejected. Say to cancel instead if needed.
Pending state persists privately under `~/.openclaw/muse-esp32/mac-control/`.

Wi-Fi off can disconnect the gadget before its final reply arrives. Restoring
connectivity may require local access. Voice is not speaker authentication, and
the existing unrestricted exec policy can bypass tool-level instructions; this
is not an OS security sandbox. The new tool does not provide file deletion,
app quitting, shutdown/restart, password entry, or general UI automation.

Coverage: `npm --prefix tools/muse/chrome-plugin test`. Live validation should
read statuses and test app/sound actions without disabling connectivity.

#### Optional normal signed-in Chrome (macOS, Chrome 144+)

For browser-wide access to existing sessions without attaching individual
tabs, Chrome supports [consent-enabled auto-connect](https://developer.chrome.com/docs/devtools/agents/use-cases/auto-connect).
This exposes signed-in sites and all normal tabs in the connected Chrome
Stable profile. The agent can act with those accounts; requested page
content may enter OpenClaw's configured model context and local transcripts.
Use only on a trusted device. Do not copy cookies, clone Chrome profiles,
or disable Chrome's permission checks.

1. Install the pinned official Chrome tools in private Mac state:

   ```sh
   mkdir -p "$HOME/.openclaw/muse-esp32/browser-tools"
   cp tools/muse/chrome/package.json "$HOME/.openclaw/muse-esp32/browser-tools/"
   npm install --prefix "$HOME/.openclaw/muse-esp32/browser-tools" --ignore-scripts
   cp tools/muse/normal_chrome.py "$HOME/.openclaw/muse-esp32/"
   ```

   The official CLI interface is experimental and pinned to version 1.10.1.
   Upgrades require checking CLI/daemon-client syntax and output and rerunning
   the adapter tests.
2. Install the local structured Chrome plugin:

   ```sh
   openclaw plugins install ./tools/muse/chrome-plugin
   ```

   Copy `tools/muse/normal-chrome-skill/SKILL.md` into the **ESP32 agent's**
   workspace at `skills/normal-chrome/SKILL.md`. Add `normal_chrome` to
   **only that agent's** `tools.alsoAllow`, preserving existing entries.
   Keep `"browser"` in its `tools.deny` list to prevent isolated-profile
   fallback, alongside its other intentional denials. Keep its full tool
   profile and the main agent's/global browser settings unchanged.
   Instruct the agent to use the structured `normal_chrome` tool, not browser
   shell commands. Restart the gateway.

   The optional plugin is restricted to the `esp32` agent with computer
   control enabled and the tool explicitly allowed. It sends parameters to
   the existing helper over stdin without a shell, avoiding nested
   JavaScript/JSON quoting failures. It requires real snapshot IDs before
   typing/clicking and blocks submission after a failed fill until typing
   succeeds. Its gadget-only hooks redirect legacy browser exec calls to
   the structured tool; other agents and unrelated shell tasks are unchanged.
   This does not add new account permissions or bypass Chrome consent.
   As with all full-exec configurations, these tool rules are not an OS-level
   security sandbox.
3. In your ordinary signed-in Chrome, enable
   `chrome://inspect/#remote-debugging`. Keep Chrome open and click **Allow**
   when Chrome requests the browser-wide connection. Chrome may require fresh
   consent after restarting; no per-tab attachment or new website login is
   required while your existing sessions remain valid.
4. Add `CONFIG_MUSE_OPENCLAW_NORMAL_CHROME=y` to the hybrid build's local
   SDK configuration defaults, rebuild, verify it is enabled in the generated
   SDK configuration, and flash. The option defaults off for other setups.
5. Disable any dedicated ESP32 isolated-Chrome login launcher if no longer
   wanted. Do not automatically close its existing windows or erase its
   profile, which may contain user work.

The helper uses a dedicated local Chrome-tools daemon, auto-connects only
to the normal Stable profile, disables usage statistics and CrUX URL
reporting, and saves requested screenshots/downloads under
`~/.openclaw/muse-esp32/browser-files`. It never launches a replacement
browser when normal Chrome or permission is unavailable. Repeated tool
calls reuse the connection. Opening a tab exposes only the new tab's
metadata to the agent; closing a tab does not disclose the newly selected
unrelated tab. New tabs open in the foreground. Explicitly listing tabs
returns their titles/URLs, so do not request or log unrelated browser data.
The agent calls `normal_chrome` with `command` and named parameters, such as
`{"command":"fill","pageId":1,"uid":"ACTUAL_SNAPSHOT_UID","value":"search text"}`.
Use actual returned IDs, not example values.
For manual diagnostics, helper parameters are supplied as a JSON object,
not the native CLI's positional arguments. For text or scripts containing quotes, use the helper's shell-safe
stdin format instead of wrapping JSON in shell single quotes:

```sh
python3 "$HOME/.openclaw/muse-esp32/normal_chrome.py" fill - <<'MUSE_CHROME_JSON'
{"pageId":1,"uid":"ACTUAL_SNAPSHOT_UID","value":"The requested search text"}
MUSE_CHROME_JSON
```

Replace the example page ID and UID with values returned by the current
page snapshot. The quoted heredoc delimiter prevents shell expansion; valid
JSON escaping is still required. The original single-JSON-argument interface
remains supported. Prefer snapshot-based `fill`, `click`, and `press_key` for
searching, and take a fresh snapshot to verify results.
Actions use the pinned package's existing-daemon client directly,
so losing the daemon cannot trigger the CLI's default isolated-browser launch.

Test with `python3 tools/muse/normal_chrome.py status`. A running daemon
does not by itself prove Chrome consent or connectivity: also perform a safe
blank-tab action. `python3 tools/muse/normal_chrome.py stop` disconnects the
adapter without closing normal Chrome. To revoke access, disable remote
debugging in Chrome and stop the adapter. Restore the ESP32 agent's former
browser policy, remove its `normal_chrome` opt-in, and disable the plugin with
`openclaw plugins disable muse-normal-chrome` followed by a gateway restart.
Clear the firmware opt-in if returning to the isolated
setup. Host coverage: `python3 -m unittest tests/test_normal_chrome.py
tests/test_muse_openai.py`; structured-tool coverage:
`npm --prefix tools/muse/chrome-plugin test`.

#### Optional iMessage sending and incoming previews (macOS)

Install the standard `imsg` CLI and sign into Messages.app. Copy
`tools/muse/imessage-skill/SKILL.md` into **the ESP32 agent's workspace**
at `skills/imsg/SKILL.md`, then restart the gateway. The skill is not
automatically loaded from another agent's workspace. Sending requires
computer-control tools; keep recipient/text confirmation even if shell
commands otherwise run without approval. The skill explicitly selects
iMessage and disables SMS fallback. A CLI submission does not prove delivery.
Confirmation and no-retry rules are model instructions, not a hard security
boundary: an agent with unrestricted shell tools can bypass them. Review
the recipient and text carefully, and check an uncertain send result before
authorizing another attempt.

The board's OpenAI voice key and the Mac's OpenClaw credentials are separate.
An accepted board key does not prove that the Mac has a valid model credential.
Fix authentication for the dedicated ESP32 agent without replacing another
agent's credentials. Never paste API keys into chat, commit them, or include
them in shell arguments. If a key has been shared in chat, rotation is advised.

Grant macOS **Privacy & Security > Full Disk Access** to the actual
background service hosts (Homebrew `python3`, `node`, and `imsg` as needed),
not only VS Code or your terminal. Use the file chooser's Command-Shift-G
to select binaries in `/opt/homebrew/bin`. Restart services after changes;
Homebrew upgrades can require renewing permissions. Sending additionally
requires macOS **Automation** permission for the gateway host to control
Messages. Never bypass privacy protections or use injection features.
First verify access under the actual login-service context.

Enable incoming alerts by adding `--imessages` to the bridge's launch command:

```sh
python3 tools/muse/openclaw_bridge.py serve --bind COMPUTER-IP \
  --allow-computer-control --imessages
```

The OpenClaw firmware polls authenticated `POST /v1/notifications` approximately
every five seconds while awake, idle, and connected. It shows the sender's
phone/email and a short text preview over Home or the Muse face, with one chirp
(respecting speaker mute and volume). Tap the popup to expand its available
details in place, scroll vertically for longer text, and tap the collapse hint
to return to the preview. This does not navigate to Settings or change your
remembered screen. Dismiss, Reply and Snooze remain available; explicit Review
actions still open Companion for reply confirmation or timer replacement.
Expanded popups block taps on the underlying screen. On larger touch displays,
actions use 48-pixel-high buttons with separated, padded touch targets.
Incoming message previews dismiss after 15 seconds of visible, collapsed
time; expanding pauses that timeout, and collapsing starts a fresh 15 seconds.
Ordinary alerts pause during voice turns,
menus, images, and settings. They do not wake the sleeping display or keep
Wi-Fi awake on battery; queued alerts appear when the device wakes. Copilot watch
mode below is an explicit exception that keeps Wi-Fi connected. The
Mac must remain awake and reachable. Previews are not automatically read
aloud or used as a voice reply's recipient context.

The bridge watches **all new incoming iMessages**, not SMS, outgoing texts,
reactions, or system events. Initial setup starts at the current database
row, not the historical inbox. Subsequent bridge restarts resume the durable
cursor. Plaintext previews are stored privately in
`~/.openclaw/muse-esp32/messages.sqlite`, with at most 200 queued events.
When full, ingestion pauses without advancing the cursor; messages can
be recovered while they remain in the Mac's Messages database. Acknowledgment
occurs after display/dismissal and retries are idempotent. A board reboot
before acknowledgment can display the oldest unacknowledged alert again.
Attachment-only messages get a placeholder; photos and files are not
downloaded. Sender and preview text are limited to 96 and 256 UTF-8 bytes.
Glyph rendering depends on the board's fonts.

#### Optional gadget companion features (OpenClaw builds)

Hybrid touch displays at least 320x320 start on a **Home** information screen:
local time/date, current weather, the next gadget reminder or active local
timer, and a daily briefing preview. The website's illustrated dark/lavender
design is adapted to live data: a large clock, subdued date, minimal weather
row, and rounded reminder and briefing/Copilot cards. Smaller screens use
shorter previews, with full details available by tapping. Swipe left for
**Muse**, then left again for **Settings**;
swipe right to return. Manually selecting Home or Muse remembers that screen
across reboots. Settings is not saved as the default. Voice temporarily shows
Muse and returns to your selection when idle; manually swiping during a turn
overrides that automatic return. Smaller and non-touch displays and other
firmware providers retain their existing navigation.

Tap a Home card for details in Companion. An absent or previous-day briefing
can be built from its card; this is deterministic and does not call an AI model.
Home refreshes companion status every 10 seconds only while visible, awake,
connected, and the backend is idle. Mac local-time offsets are cached in NVS
without changing global timezone settings; reconnect to update them after a
timezone or daylight-saving change. Before initial clock/timezone sync the
screen explicitly shows that it is waiting, rather than displaying UTC as
local time.

The Mac fetches configured Open-Meteo current conditions and daily high/low
and rain probability about every 15 minutes, independently of status requests.
Briefings reuse that private, persistent cache. Failed weather refreshes retain
old measurements marked **Cached**, and retry no more often than five minutes.
Offline clock/timer display continues; reminders and briefing content indicate
cached/offline state rather than pretending to be live. Missing configuration
or sources are shown explicitly. This feed requires Mac internet access but
does not use OpenAI or require a weather API key.

Add `--companion` to the bridge launch command. Before enabling it with
launchd, ensure its `EnvironmentVariables.PATH` includes the directory containing
Node and the OpenClaw CLI (for example `/opt/homebrew/bin` on Apple Silicon);
launchd does not inherit your interactive shell's search path. After changing
LaunchAgent arguments or environment, bootout/bootstrap it rather than only
kickstarting the old definition. Before enabling it with
computer control, add `process` to **only the ESP32 agent's** `tools.deny`
alongside its existing denials. Keep its full tool profile and other exec
settings. This makes OpenClaw exec foreground-only: native cancellation
otherwise deliberately leaves background commands running. Set this
agent's `tools.exec.timeoutSec` to `86400` for long foreground commands.
Other agents and credentials should not be changed.

Chat uses authenticated `/v1/jobs/start`, `/v1/jobs/status`, and
`/v1/jobs/cancel`. Each device request has an idempotent identifier and a
dedicated `agent:esp32:muse-job:...` session. Short polling replaces the old
90-second chat deadline; Stop calls native `chat.abort` for that exact run.
The gateway still has a 24-hour run ceiling, and provider/tool limits can
end a job earlier. Stop does not undo completed actions. Independent
background launches are prohibited for the ESP32 agent.

Jobs and completion alerts survive bridge restart in private SQLite state.
Interrupted actions are not replayed automatically. Failed or unconfirmed
stops are reported explicitly; check OpenClaw before repeating uncertain
actions. Native requests separate the current user turn from reference-only,
completed conversation history and include the installed companion helper's
exact path and JSON protocol; old commands are not presented as pending work.
Native model cost metadata is an **estimate**, not account billing;
missing steps and potentially truncated histories are marked incomplete,
not zero. API responses at or above the 32,768-byte OpenClaw device limit are explicitly
rejected, never delivered as a clipped successful response. The dashboard separately states
that direct OpenAI voice usage is not metered and shows budgeting rates.
Browser connectivity checks invoke a read-only tool on normal Chrome;
daemon liveness alone is not treated as browser connectivity.

Open **Settings > Companion** for configurable favourite cards, timer
controls, gadget reminders, reply review, connectivity, and briefing settings.
Companion opens a compact menu grouped into **Personal AI**, **Daily tools**,
and **Device**. Conversation, Personal memory, Copilot tasks, Tasks, Personal
routines, Timers, Reminders, Daily briefing,
Calendars & alerts, Replies, Favourite cards, and Connection & costs each have
their own page. Back (or swipe right) returns to the Companion menu, then Settings.
Each section retains its scroll position while Companion stays open, including
live Mac refreshes and returning from text entry. Home reminder/briefing cards
and pending reply/timer-replacement prompts open the relevant section directly.
Favourites currently select from six deterministic actions: five- and
ten-minute timers, a custom timer, adding a reminder, the briefing, and
the dashboard. They do not require a model call.

There is one local countdown timer, with confirmation before replacing it.
It works offline, wakes the sleeping display, and supports Dismiss and
five-minute Snooze. It cannot wake a powered-off board. Timers are anchored
to monotonic time while running; restoration after reboot needs network time.
A timer started without a valid clock cannot be restored across a restart,
and that interruption is reported. Supported voice shortcuts include
`Set a timer for 10 minutes` and `Start a timer for thirty seconds`;
these still use paid OpenAI transcription/speech but skip AI chat.

Mac-backed gadget reminders and scheduled briefings queue while the display
sleeps and are shown when awake. They need an awake, reachable Mac.
Reminders persist and support Dismiss and five-minute Snooze.
Install `tools/muse/companion-skill/SKILL.md` as
`skills/gadget-companion/SKILL.md` in the ESP32 workspace, and copy
`tools/muse/companion_cli.py` to the private bridge state directory.
The authenticated local helper supports reminders and briefing requests;
do not substitute OpenClaw cron or a Mac sleep command for local timers.

### Copilot approvals and questions from VS Code

The hybrid firmware can supervise a **dedicated GitHub Copilot SDK session**
launched in a VS Code terminal. It does **not** attach to an existing Copilot
Chat/Agent Host conversation or approve that panel's prompts. The SDK controller
owns the session's actual `onPermissionRequest` and legacy `onUserInputRequest`
callbacks; a queued chat message saying "approve" is not an authorization.
This uses your authenticated Copilot account, not OpenClaw for coding decisions.
Copilot subscription/usage limits apply. Gadget speech still uses the existing
paid OpenAI transcription and generic response speech.

Requirements: Node.js 22+, an authenticated Copilot CLI/SDK runtime, the
HTTPS Mac bridge running with `--companion`, and the updated hybrid firmware.
The SDK supplies its runtime. Authenticate its CLI if the controller reports
that login is required; never paste GitHub or bridge tokens into chat.
After updating the bridge source, restart its LaunchAgent. No TLS trust reset
or NVS erase is needed. From the **repository root**:

```sh
npm --prefix esp32/tools/muse/copilot ci
npm --prefix esp32/tools/muse/copilot run build
node esp32/tools/muse/copilot/dist/cli.js --cwd .
```

Enter coding tasks in that terminal. `--cwd /absolute/path/to/project` selects
another workspace; `--model` selects a Copilot model and `--python` selects the
Python interpreter used by the existing verified-TLS helper. `/stop` cancels
pending authorizations and aborts the turn; `/quit` closes the controller.
For a VS Code task, run the same Node command with the workspace folder as its
working directory. Name it **Gadget Copilot** and launch it through
**Terminal > Run Task**. Keep the Mac awake and on the same network as the gadget.

Each waiting request puts the project name and question or exact command first
on the gadget card. The full operation, workspace and session remain available
in the expanded scrollable card, with one chime per request.
Copilot cards also appear over Settings. Review it, **hold Talk**, then say
**"approve"** or **"deny"** for a permission. Approval is **once-only**, never
"always allow". For a question, speak the exact offered choice, **"option two"**,
or tap a numbered choice button to send that exact offered label without using
speech APIs. Scroll the card for more choices and the complete request details.
When the question allows freeform answers, **Free text - speak an answer** starts
recording immediately: speak, then tap **Send**; **Cancel** discards the recording
and leaves the question waiting. Recording is limited to 15 seconds; reaching
that limit without Send cancels rather than automatically submitting. Holding
Talk still supports choices or freeform answers when allowed.
Ambiguous permission speech (including an
unqualified "yes") never approves work. Question answers retain their original
choice labels. **Deny** rejects the displayed operation; **Skip** declines a
question without fabricating an answer. Desktop fallback commands are shown
with the exact request ID: `/approve ID`, `/deny ID`, `/answer ID YOUR ANSWER`.
The first valid desktop/device response wins.

**Settings > Companion > Connection & costs > Copilot watch on battery** is on
by default in this deployment, as requested. It keeps Wi-Fi connected while
the screen sleeps, using more battery, but leaves the sleeping audio codecs
off. It does not enable hands-free recording or automatic voice commands.
Disable it for normal battery sleep; plugged-in or already-awake devices can
still receive Copilot alerts. Copilot attention wakes the screen and respects
speaker mute/volume. Calendar quiet hours apply only to calendar heads-ups.
Local timer alarms and confirmed message replies retain priority, and active
recording/speech is not interrupted by a new Copilot alert.

The authorization path is deterministic and request-bound: transcription is
sent directly to the waiting request, **not to the general OpenClaw chat**.
Questions/command details are not automatically sent to OpenAI for read-aloud;
only the user's recorded speech and generic acknowledgment use voice APIs.
Full permission previews must fit 2,047 bytes and be displayable ASCII. Larger,
non-displayable or otherwise unsupported requests explicitly require desktop
review; they cannot be approved from a shortened gadget preview.

Requests expire after ten minutes. The desktop heartbeat is five seconds with
a twenty-second lease. Disconnects, controller/bridge restarts, `/stop`, stale
IDs and invalid responses fail closed; old approvals are never resumed.
"Approval submitted" confirms transport, not completion of the coding work.
Cancelling a voice turn cannot revoke a decision already delivered; stop the
Copilot session if you need to interrupt work that has already been approved.
The controller automatically retries connectivity with a fresh lease and a
fresh SDK session. Previous work is interrupted, not resumed; old SDK events
and authorization callbacks cannot answer or complete a new session's task.
SDK shutdown is bounded to twenty seconds. If graceful shutdown fails, the
controller reports the failure, force-stops its own SDK transport, and exits
nonzero. This cannot undo previously approved operations; review them on the Mac.
A separate private desktop producer credential prevents the device token from
creating work requests. Both use the existing verified local HTTPS channel.
Private `copilot.sqlite` stores request previews and responses; terminal records
older than seven days are pruned on subsequent API activity. SDK/provider
conversation history is separate. Anyone able to operate the unlocked gadget
can submit its decisions; this is not speaker authentication or isolation from
an agent that already has full access to your Mac.

Controller validation: `npm --prefix esp32/tools/muse/copilot test`. Application
TypeScript is strict; transitive SDK declaration checking is skipped for the
SDK's older JSON-RPC iterator declarations with current TypeScript/Node types.

### Copilot task dashboard and connection health

**Settings > Companion > Copilot tasks** shows six recent dedicated-session
tasks: project, reviewed title, working/waiting/stopping state, tool progress,
and the final reply. The Home briefing card temporarily shows an active or
recent task and opens this dashboard. Completion and failure use distinct
chimes; result cards are never voice-approval targets.

**Stop this task** is bound to that exact task and owning SDK session. It
cancels waiting authorizations immediately and queues an SDK abort for the
next desktop heartbeat. `stopping` is not proof of an acknowledged abort;
disconnects are shown as interrupted. Finished means the SDK turn ended,
not that every requested edit, build or test succeeded. Review the result
and terminal. Results remain available for seven days; result notifications
are visible for ten minutes and can be dismissed independently.

Home and Connection & costs distinguish bridge HTTPS contact/authentication
from OpenAI speech failures. When the Mac sleeps or the network is lost,
the gadget continues local timers; Mac tasks, saves and routines require
the bridge. Wake/reconnect does not replay work or approvals.

### Personal routines and local priorities

**Settings > Companion > Personal routines** enables morning/evening summaries,
edits their HH:MM schedules, and builds either summary on demand. New
installations default to disabled, with 08:00 and 20:00 Mac-local schedules.
Scheduled routines run once per local day, catch up only within one hour,
and respect the existing calendar quiet hours (default 22:00–08:00).
Routine/calendar alerts are held during quiet hours; explicit reminders
remain deliverable and dismissible. The Mac must be awake and running the
bridge; this is not a wake-from-sleep scheduler.

**Tasks** provides reviewed local priorities, complete/reopen, pagination,
and double-tap deletion. Up to 100 open/200 total tasks persist on the Mac.
Morning summaries include open priorities; evening summaries include unfinished
priorities, reminders through tomorrow, and tomorrow's selected-calendar
agenda. Calendar/weather failures remain explicit, including partial digests.
These deterministic summaries and task controls do not invoke an AI model.

Meeting preparation lets you edit a note for one upcoming selected-calendar
occurrence. With existing calendar heads-ups enabled, the note is added to
that event's alert and expires at event start. It is not shared with later
occurrences or used to take actions automatically.

### Personal memory and durable conversations

OpenClaw companion mode keeps the last eight completed request/reply pairs in
private Mac SQLite state, with a 24,000-byte serialized reference-context budget.
The gadget and Mac can restart without losing follow-up context. Failed,
cancelled and interrupted tasks are excluded; old jobs without stored requests
are not imported. Each task retains a separate cancellable native session.
Previous turns are reference data, never permission to replay a computer action.
Current local timer state is supplied separately, so timer follow-ups still work.

Say **"Remember that I prefer short answers"** to explicitly save a personal
fact, or use **Settings > Companion > Personal memory > Add a memory**. No automatic learning from
chat, messages, calendars or browsing is performed. Saved facts are sent to the
model as reference context on future OpenClaw turns. Do not store credentials.
There are at most 50 facts of 240 UTF-8 bytes each, shown six per page. Adding an
exact duplicate is idempotent. **Forget** requires two taps on the same item
within 15 seconds.

**Conversation > Start a new conversation** resets recent context but keeps saved memories.
Forgetting a fact also resets recent context to prevent old replies from
reintroducing it. These actions do **not** erase historical job records,
OpenClaw/provider transcripts or backups. Memory/context controls require the
reachable Mac and report success only after confirmation. Standalone OpenAI
builds retain their existing RAM-only conversation behavior.

After a completed OpenClaw turn, **Save...** opens Conversation with its
reviewable source. Choose **Remember this preference**, **Make a reminder**,
or **Save as a task**, edit the suggested user-request text, and explicitly
confirm. Reminder saves also require an interval. There is no automatic
learning or automatic execution. The source must still belong to the current
completed conversation; stale/reset sources are rejected.

Save retries are idempotent. The target and its receipt commit atomically
across the private SQLite stores. **Undo last save** removes only the exact
newly-created item; a deduplicated, pre-existing memory cannot be deleted by
Undo. Undoing a new memory also starts a fresh conversation, just like Forget.
It does not erase provider transcripts or backups. These controls use the
authenticated local bridge and do not need speech/model API calls.

### Proactive calendar alerts

In **Settings > Companion > Calendars & alerts**, enable heads-ups for the currently selected
calendars. The initial lead is 30 minutes; the gadget cycles 5/15/30 minutes.
Quiet hours default to **22:00-08:00 in the Mac's local timezone** and can be
disabled. All-day and cancelled events are excluded. This is a read-only feature:
it creates neither calendar events nor OpenClaw cron jobs and makes no AI calls.
Keep the Mac awake and reachable for delivery.

The next 24 hours are refreshed every two minutes. A missing, unavailable or
five-minute-stale agenda generates no new alerts. Rescheduled/cancelled
occurrences invalidate old queued heads-ups; cached gadget calendar alerts
are rechecked while awake and expire locally at event start, including offline.
The same occurrence is not repeatedly announced across bridge restarts.
Five-minute Snooze is rejected if it would reach or exceed event start.
Quiet hours suppress new calendar alerts only; a still-future event can receive
a delayed heads-up after quiet hours end. Manual reminders and local timer
alarms retain their existing behavior.

When upgrading, reinstall the read-only calendar helper and refresh the installed
companion skill and private CLI copy. The helper now supplies stable calendar and
occurrence identifiers; an older helper is explicitly reported unavailable for
proactive alerts rather than scheduling unreliable duplicates.

Tap **Reply** on an incoming iMessage, hold Talk to dictate, then review the
exact conversation participants and full text in Companion. Edit, cancel,
or explicitly confirm sending. The bridge resolves the source message's
actual chat GUID, never its preview sender. Confirmation sends directly
through `imsg`, without asking a chat model to choose a recipient. Unsupported
display glyphs disable confirmation rather than hiding changes to the text.
Sends are attempted once with no SMS fallback; uncertain sends must be checked
in Messages before authorizing another attempt. Full shell access can still
bypass UI rules, so these are not an isolation boundary against the agent.

The morning briefing is assembled locally from configured weather,
gadget reminders, and enabled Mac event calendars, including recurring
occurrences via EventKit. It is displayed without automatically sending
calendar data to an AI provider. Asking the chat agent for the digest may
put its contents into that model conversation.

Install the read-only helper using the existing Apple developer tools:

```sh
python3 tools/muse/install_calendar.py --request-access
```

Grant **full** calendar access to Muse Calendar Reader; write-only access
cannot read an agenda. Calendar queries launch that authorized app through
LaunchServices with private temporary request/response files; invoking its
executable directly can instead inherit the terminal's privacy identity.
The bridge waits up to 15 seconds for the app's atomic response file rather
than relying on LaunchServices to wait for the short-lived app to exit.
Rebuilding the ad-hoc-signed helper may require granting access again.
Enable all currently readable calendars with
`{"action":"settings","settings":{"calendars_enabled":true,"calendar_ids":"all"}}`
through the local helper. The overall switch and individual calendar switches
are in Companion. New calendars are not silently enabled; refresh and select
them. Calendars absent from EventKit are unavailable to this integration.
The device shows switches for the first 32 calendars; use the helper for
larger selections.
Weather coordinates, briefing `hour`/`minute`, and favourite choices can also
be edited through the helper's `settings` action. Preferences remain private
in `~/.openclaw/muse-esp32/companion.sqlite`, not in the repository.

The daily schedule uses the Mac's local timezone, runs once per local date,
and catches up only within one hour of the scheduled time if the service
was asleep/offline. Scheduled routines respect shared quiet hours.
Manual briefings are also available. Unavailable sources produce an explicitly
partial digest; an interrupted build is not silently retried.

Focused companion regressions (from the repository root):

```sh
python3 -m unittest esp32/tests/test_muse_companion.py \
  esp32/tests/test_openclaw_companion.py esp32/tests/test_openclaw_jobs.py \
  esp32/tests/test_muse_openai.py esp32/tests/test_muse_openclaw.py \
  esp32/tests/test_muse_messages.py esp32/tests/test_muse_settings_ui.py
```

Incoming previews stay local to the Mac and ESP32: the watcher never calls
OpenClaw or an AI provider. Deliberately asking the voice agent to read a
conversation or send a dictated message does send that requested content
through its configured model and may save it in transcripts. Keep the
device and local queue private. Watcher, permission, queue, and HTTP failures
are reported in bridge/device logs, without message bodies or sender IDs.
Removing `--imessages` and restarting stops incoming delivery; deleting
the private queue deliberately resets the baseline at next setup. Do not
delete it merely to hide a permission error.

The USB console also accepts `>openai.key=KEY`, `>openai.test`,
`>openai.clear`, and the existing Wi-Fi setup commands. Key values are never
echoed in logs or status. `tools/muse/chat.py --port PORT "message"` can test
a typed turn without paying for transcription or speech.

## Boards

The last seven run the full on-screen UI: an animated avatar, push-to-talk and
settings. Audio and image support vary by board, so check the feature table in
[`devices/`](devices). The others show status on a light, a ring or a simple
status screen.

| Board | What you get | Build it |
|---|---|---|
| ESP32-C5 DevKitC-1 | Status light and button | `idf.py build` |
| ideaspark ESP32 with 1.9" display | Status on screen, images | `tools/board.sh ideaspark build` |
| Seeed SenseCAP Indicator | Status on a 4" screen, images | `tools/board.sh sensecap-indicator build` |
| Seeed reTerminal E1001 | Status on a 7.5" e-paper, black and white images | `tools/board.sh reterminal-e1001 build` |
| Home Assistant Voice Preview Edition | Status on the LED ring, push-to-talk, volume dial | `tools/board.sh home-assistant-voice build` |
| Waveshare ESP32-S3-Touch-AMOLED-1.75C | UI, push-to-talk, settings, images | see [`AGENTS.md`](AGENTS.md#boards-with-the-full-ui-by-hand) |
| Waveshare ESP32-S3-Touch-AMOLED-1.75 | UI, push-to-talk, settings, images | see [`AGENTS.md`](AGENTS.md#boards-with-the-full-ui-by-hand) |
| Espressif ESP32-S3-BOX-3 | UI, touch, push-to-talk, settings, images | [BOX-3 setup](devices/esp32-s3-box-3.md) |
| AIPI Lite | UI, push-to-talk, two-button menu, images | see [`AGENTS.md`](AGENTS.md#boards-with-the-full-ui-by-hand) |
| Waveshare ESP32-C6-Touch-AMOLED-1.8 | UI, push-to-talk with text replies | see [`AGENTS.md`](AGENTS.md#boards-with-the-full-ui-by-hand) |
| Seeed SenseCAP Watcher | UI, push-to-talk, settings, images | see [`AGENTS.md`](AGENTS.md#boards-with-the-full-ui-by-hand) |
| M5Stack Cardputer ADV (experimental) | UI, GO/Space push-to-talk with text replies, Esc/Enter/arrow menu controls | `tools/muse/board.sh build cardputer-adv` |
| M5Stack StickS3 | UI, push-to-talk, two-button menu, images | see [`AGENTS.md`](AGENTS.md#boards-with-the-full-ui-by-hand) |
| M5Stack StopWatch | UI, push-to-talk, settings, images | see [`AGENTS.md`](AGENTS.md#boards-with-the-full-ui-by-hand) |
| M5Stack StickC Plus2 | UI, push-to-talk, two-button menu, images | see [`AGENTS.md`](AGENTS.md#boards-with-the-full-ui-by-hand) |

See [`devices/`](devices) for each board's hardware, features, and where to
buy one.

`tools/board.sh BOARD [build|flash|monitor|flash-monitor] [PORT]` builds each
board in its own `build-<board>` directory with the right chip and settings.
Boards that support images can show pictures Muse sends them:
`tools/image_for_display.py` prepares a picture for the screen size.

Boards without PSRAM, like the classic ESP32 and the ESP32-C6, run without the
home-network tunnel, which needs more memory than they have. Muse can still
reach the device and control it.

## Hack and extend it

Every board's settings live in a small `devices/sdkconfig.<board>` file loaded
on top of `sdkconfig.defaults`. To add a board, copy the closest one and change
the chip, button pin, status light, and flash size.
[`devices/README.md`](devices/README.md#add-a-board) covers the details, or ask
Muse Code to do it for you.

To put your own avatar on a board's screen, plug in the board and run
`python3 tools/muse/avatar.py`. It asks your Muse to redraw its avatar as the
board's pixel avatar, checks the result, then builds and flashes it. Your avatar
stays out of git. See [`tools/muse/AVATAR_RECIPE.md`](tools/muse/AVATAR_RECIPE.md)
for how it works and for boards that need the manual steps.

To work on the UI without a board, use the
[`simulator/`](simulator/README.md) desktop preview. It runs the production UI
and avatar renderer in a 412 x 412 SenseCAP Watcher window, supports mouse and
keyboard input, and can render scripted screenshots without a display server.

Replies from Muse are text: push-to-talk sends your voice note, Muse
transcribes it and answers in writing, and boards with a screen show the
answer as captions (the Voice PE's replies show up in the Muse app). Two
things you can change:

- **Shorter answers.** Ask for them in the message itself, such as "Answer in
  one sentence."
- **Spoken answers.** Send each reply's text to a text-to-speech API of your
  choice and play the audio it returns. On boards with PSRAM, `start_tts` in
  [`components/muse/muse_chat_session.cpp`](components/muse/muse_chat_session.cpp)
  is the spot: it has the reply text, and the MP3 decoder, speaker and volume
  are already wired up there.

A few things worth knowing:

- Your SDK token ships inside the firmware, so treat it as an identifier
  rather than a password. If it leaks, revoke it on gadgets.muse.ai, generate
  a new one, and rebuild.
- **We strongly recommend enabling NVS encryption** if your board supports it.
  NVS stores Wi-Fi credentials and device tokens in flash; without encryption,
  anyone with physical access to the board can read them. Enable it with
  `CONFIG_HOMEHUB_NVS_ENCRYPTION` in `idf.py menuconfig` (under "ESP32 Device
  SDK"). The encryption key is derived from an HMAC key in eFuse, which is
  generated automatically on first boot if the eFuse block is available.
- Builds are version `999.0.0`. Over-the-air updates are off by default; the
  boards that run the full on-screen UI turn them on. Change it with
  `CONFIG_HOMEHUB_OTA_ENABLED` in `idf.py menuconfig`, and set a version with
  `idf.py -DPROJECT_VER=1.0.0 build`.
- Each build keeps its generated settings in its build directory
  (`build/sdkconfig`). If you change `sdkconfig.defaults` or a board file,
  delete the build directory so the change takes effect.
- Builds are signed with the included development key and never turn on
  Secure Boot, so you can reflash your board as often as you like.

## Tests

The tests run on your computer, with no board attached:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
```

Run `idf.py build` once first so the downloaded components are in place.

## Community

Meet other hackers who are building and customizing Muse gadgets in our
community [Discord](https://discord.gg/3bhjCkZdd6). Get inspired, support each
other, and share what you make.

## License

The ESP32 Device SDK is licensed under the Apache License, Version 2.0, found
in [`LICENSE`](../LICENSE), except for these third-party files, which keep
their upstream licenses:

- [`components/minimp3/include/minimp3.h`](components/minimp3), the MP3
  decoder, is CC0-1.0. See [`components/minimp3/LICENSE`](components/minimp3/LICENSE).
- [`main/pixel_font.c`](main/pixel_font.c), the Adafruit GFX font, is
  BSD-2-Clause, as its header says.

ESP-IDF components fetched at build time (into `managed_components/`) are
under their own licenses.
