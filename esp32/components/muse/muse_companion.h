// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <stdbool.h>
#include <stddef.h>
#define MUSE_COMPANION_SNAPSHOT_CAP 32768

typedef enum { MUSE_TIMER_OFF, MUSE_TIMER_RUNNING, MUSE_TIMER_RINGING, MUSE_TIMER_WAIT_CLOCK } muse_timer_state_t;
void muse_companion_start(void);
bool muse_timer_start(unsigned seconds);
bool muse_timer_dismiss(void);
unsigned muse_timer_pending(void);
bool muse_timer_confirm_replace(unsigned seconds);
void muse_timer_cancel_replace(void);
bool muse_timer_snooze(unsigned seconds);
muse_timer_state_t muse_timer_status(unsigned *seconds);

bool muse_openai_companion_command(const char *json);
bool muse_openai_companion_snapshot(char *out, size_t cap, unsigned *version);
bool muse_openai_reply_begin(const char *notification_id);
bool muse_openai_companion_busy(void);
