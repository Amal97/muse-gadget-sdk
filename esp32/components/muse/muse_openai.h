// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>

/* Drops the in-memory conversation and cancels the current request. */
void muse_openai_clear_history(void);

typedef struct {
    char id[33];
    char sender[97];
    char preview[257];
} muse_notification_t;

bool muse_openai_notification(muse_notification_t *out);
void muse_openai_notification_dismiss(void);
