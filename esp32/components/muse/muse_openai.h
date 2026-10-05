// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stdint.h>

/* Drops the in-memory conversation and cancels the current request. */
void muse_openai_clear_history(void);

typedef struct {
    char id[33];
    char sender[97];
    char preview[257];
    char kind[17];
    char body[2048];
    int64_t expires_at;
    bool respondable;
    bool allow_freeform;
    uint8_t choice_count;
    char choices[12][241];
} muse_notification_t;

bool muse_openai_notification(muse_notification_t *out);
void muse_openai_notification_dismiss(void);
void muse_openai_copilot_focus(const char *id);
bool muse_openai_copilot_record_begin(const char *id);
bool muse_openai_job_active(void);
