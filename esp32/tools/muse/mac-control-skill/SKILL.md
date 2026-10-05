---
name: mac-control
description: Control Mac applications, output volume, mute, Bluetooth and Wi-Fi through the structured mac_control tool.
metadata: {"clawdbot":{"os":["darwin"]}}
---

# Mac voice control

Use the structured `mac_control` tool, not generated shell commands, AppleScript,
sudo, or GUI scripting. This controls the Mac, not the ESP32's speaker or settings.
Use `normal_chrome` for browsing and website input.

## Commands

Call the tool with `command` and the listed fields:

| Request | Tool arguments |
|---|---|
| Available controls | `{"command":"capabilities"}` |
| Current Mac sound | `{"command":"audio_status"}` |
| Set Mac volume to 30% | `{"command":"volume_set","volume":30}` |
| Mute/unmute the Mac | `{"command":"mute_set","muted":true}` / `false` |
| Check an app | `{"command":"app_status","app":"Calculator"}` |
| Open an installed app | `{"command":"app_open","app":"Calculator"}` |
| Bluetooth status | `{"command":"bluetooth_status"}` |
| Turn Bluetooth on/off | `{"command":"bluetooth_set","enabled":true}` / `false` |
| Wi-Fi status | `{"command":"wifi_status"}` |
| Turn Wi-Fi on/off | `{"command":"wifi_set","enabled":true}` / `false` |
| Inspect a pending confirmation | `{"command":"pending"}` |

Use the user's actual application name. Never substitute Chrome navigation for
opening an application, or invent an app's running state. Volume must be a whole
percentage from 0 to 100. Status and action results describe power/running state,
not internet connectivity, window readiness, or successful playback.

## Disconnection confirmation

Turning Bluetooth or Wi-Fi off can disconnect input devices, the gadget, or the
internet. An off request returns `confirmation_required` without changing power.
Explain its warning and ask the user to make a separate voice confirmation,
for example "Confirm turning Bluetooth off." Then STOP this request.

Do not call `confirm` in the initiating request. A model-generated assertion of
consent is not a user confirmation. A new gadget job is required. After the user
explicitly confirms, call `pending`, verify that its target matches the user,
then call `confirm` with the exact stored `confirmationId` and `target`.
Do not make the user speak or copy the ID.

Confirmations expire after three minutes, survive bridge/gateway restarts, and
are single-use. Cancelling needs no additional confirmation: immediately call
`pending`, then `cancel` with its `confirmationId` when the user asks to cancel.
Never ask the user to confirm cancellation. Do not confirm
a different pending action, automatically replay expired/failed actions, or
claim that cancellation undoes an already completed change.

Mac Wi-Fi off may prevent the final response from reaching the ESP32. Warn before
confirmation, and explain that restoring Mac connectivity may require local
access. Voice is not speaker authentication.

## Results and boundaries

Report success only from a verified result. Native app and sound actions read
back their resulting state. Radio power is also read back after changes.
If macOS denies an action or a native tool times out, report the actual error
and leave its outcome unconfirmed. Do not bypass privacy prompts, elevate
permissions, disable protections, or blindly repeat uncertain actions.

Bluetooth power changes require the installed `blueutil` utility and may need
macOS Bluetooth permission for the actual gateway/tool host. Missing/denied
access is a setup issue, not a reason to invent success or use GUI fallbacks.

This tool does not provide arbitrary scripts, file deletion, app quitting,
shutdown/restart, password entry, or unrestricted UI automation. The existing
agent's full exec access is separate and can bypass tool-level instructions;
these confirmations are not an OS security sandbox or identity check.
