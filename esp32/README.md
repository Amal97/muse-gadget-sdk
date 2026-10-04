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
   firewall only on trusted networks. Reserve the computer's LAN address
   in your router, or update the bridge bind and board URL when it changes.
5. With the existing OpenAI key and Wi-Fi saved on the board, provision
   the separate bridge token privately over USB:

   ```sh
   python tools/muse/openclaw_setup.py --port PORT \
     --url https://COMPUTER-IP:8765/v1/chat/completions --test
   ```

   `--test` generates a short, billed model reply through the actual ESP32
   connection. **Test API key** on the screen still tests OpenAI only.
   USB status reports `provider: "openai"` for the voice pipeline and
   `chat_provider: "openclaw"` for conversations.

Your computer must remain awake and reachable on the same network. There
is no automatic fallback to OpenAI chat when OpenClaw fails: failures are
displayed explicitly. **Settings > OpenAI > Use direct OpenAI chat** or
`openclaw_setup.py --port PORT --disable` explicitly removes the bridge
settings and returns to the original standalone behavior.

The OpenAI key and bridge token remain unencrypted in device NVS. Audio
goes to OpenAI; conversation text goes through the computer to OpenClaw's
configured provider. OpenClaw may save transcripts/session files on the
computer. The firmware sends its bounded history with each turn and the
bridge creates an independent OpenClaw session, so **New conversation**
clears active device context, not previously saved computer logs. Existing
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

To revoke computer control, restore the ESP32 agent's `tools.deny: ["*"]`,
remove `--allow-computer-control` from the bridge service, and restart it.
Restore that agent's previous exec-approval policy as well. Use the
on-screen direct-OpenAI option to disconnect the board from OpenClaw entirely.

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
2. Copy `tools/muse/normal-chrome-skill/SKILL.md` into the **ESP32 agent's**
   workspace at `skills/normal-chrome/SKILL.md`. Add an agent instruction to
   require `python3 "$HOME/.openclaw/muse-esp32/normal_chrome.py"` with JSON
   parameters for every browser action and to read the skill for actual
   command names, such as `new_page` and `evaluate_script`, rather than
   executing placeholders like `TOOL`. Instruct it not to substitute
   AppleScript, `osascript`, `open`, or direct Chrome launch commands if the
   helper fails. These are model instructions, not permission boundaries
   under unrestricted exec access. Add `"browser"` to **only that agent's**
   existing `tools.deny` list, preserving other denied tools, to prevent native
   isolated-profile fallback. Keep its full/exec tool access and the main
   agent's/global browser settings unchanged. Restart the gateway.
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
Tool parameters are supplied as a JSON object, not the native CLI's positional
arguments. Actions use the pinned package's existing-daemon client directly,
so losing the daemon cannot trigger the CLI's default isolated-browser launch.

Test with `python3 tools/muse/normal_chrome.py status`. A running daemon
does not by itself prove Chrome consent or connectivity: also perform a safe
blank-tab action. `python3 tools/muse/normal_chrome.py stop` disconnects the
adapter without closing normal Chrome. To revoke access, disable remote
debugging in Chrome and stop the adapter. Restore the ESP32 agent's former
browser policy and clear the firmware opt-in if returning to the isolated
setup. Host coverage: `python3 -m unittest tests/test_normal_chrome.py
tests/test_muse_openai.py`.

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
phone/email and a short text preview over the Muse face, with one chirp
(respecting speaker mute and volume). Tap the card to dismiss, or let it
dismiss after 15 seconds of visible time. Alerts pause during voice turns,
menus, images, and settings. They do not wake the sleeping display or keep
Wi-Fi awake on battery; queued alerts appear when the device wakes. The
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
