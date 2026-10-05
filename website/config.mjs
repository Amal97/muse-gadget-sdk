// SPDX-License-Identifier: Apache-2.0
export const repository = "https://github.com/Amal97/muse-gadget-sdk";
export const branch = "feature/open-claw-with-muse";

export const groups = [
    { title: "Discover", pages: [
        ["what-the-project-does", "Features", "Voice, personal routines, and coding supervision."],
        ["choose-a-mode", "Choose your setup", "Compare standalone OpenAI, OpenClaw, and original Muse."],
        ["hardware-and-compatibility", "Hardware & compatibility", "The right board, host, and supported environment."],
        ["how-it-works", "How it works", "Understand the device, speech APIs, bridge, and SDK."],
    ] },
    { title: "Get set up", pages: [
        ["requirements", "Before you start", "Hardware, accounts, tools, and network requirements."],
        ["download-and-install-the-toolchain", "Download & toolchain", "Clone the feature branch and install ESP-IDF."],
        ["install-standalone-openai-firmware", "Standalone OpenAI", "Build, flash, configure Wi-Fi, and try your first turn."],
        ["add-the-openclaw-companion", "Connect OpenClaw", "Set up the agent, private TLS bridge, and hybrid firmware."],
        ["configure-your-personal-companion", "Personalize your companion", "Weather, calendars, summaries, and meeting prep."],
    ] },
    { title: "Connect more", pages: [
        ["enable-computer-control", "Computer control", "Explicit opt-in, foreground work, and permission boundaries."],
        ["optional-mac-integrations", "Messages & Chrome", "Optional Mac integrations and their privacy permissions."],
        ["set-up-github-copilot", "GitHub Copilot", "A dedicated SDK session, voice decisions, and touch choices."],
        ["start-the-bridge-at-login", "Start at login", "Create and maintain your own Mac login service."],
    ] },
    { title: "Everyday use", pages: [
        ["daily-use", "Using your gadget", "Navigate, save memories, manage tasks, and set timers."],
        ["offline-behavior-and-limitations", "Offline & limitations", "What still works when the Mac or network is unavailable."],
        ["privacy-permissions-and-costs", "Privacy & costs", "Understand credentials, data flow, trust, and billing."],
        ["updates-backups-and-removal", "Updates & backups", "Update safely, preserve settings, or remove integrations."],
        ["troubleshooting", "Troubleshooting", "Find the right check without erasing settings or bypassing TLS."],
    ] },
    { title: "Reference", pages: [
        ["development-and-testing", "Development & testing", "Source layout, regressions, simulator, and extension guides."],
        ["original-muse-and-linux-modes", "Original Muse & Linux", "The original SDKs have their own installation paths."],
        ["license-and-attribution", "License & attribution", "Upstream attribution, licenses, and asset restrictions."],
    ] },
];
