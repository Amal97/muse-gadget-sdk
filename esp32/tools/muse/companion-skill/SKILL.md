---
name: gadget-companion
description: Required for ESP32/Muse gadget reminder requests and morning briefings. Saves reminders to the device notification queue, not OpenClaw cron.
---

# Gadget companion

Use the local helper rather than OpenClaw cron for gadget reminders:

```sh
python3 "$HOME/.openclaw/muse-esp32/companion_cli.py" '{"action":"reminder","title":"Stretch","after_seconds":600}'
python3 "$HOME/.openclaw/muse-esp32/companion_cli.py" '{"action":"reminders"}'
python3 "$HOME/.openclaw/muse-esp32/companion_cli.py" '{"action":"briefing"}'
python3 "$HOME/.openclaw/muse-esp32/companion_cli.py" '{"action":"briefing_status"}'
```

For a specific reminder time, pass `due` as a Unix timestamp instead of
`after_seconds`. Resolve the time using the Mac's current local timezone; confirm
ambiguous times with the user. Only report creation when the helper returns the
saved reminder.

A briefing initially reports `building`. Poll `briefing_status` until `ready`,
`partial`, `failed`, or `interrupted`; explicitly report unavailable sources.
Do not read disabled calendars or unrelated message drafts. Scheduled briefings
are assembled locally without an AI request.

Countdown timers run on the gadget, not the Mac. Use its Timers quick action;
do not substitute a Mac background sleep or claim a local timer was started.

ESP32 tasks use foreground execution so the gadget can stop them. Do not detach
commands, use `nohup`, or start independently running background jobs. Completed
computer actions cannot be undone by Stop.
