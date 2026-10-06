// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

/* First mapping needs an internal stack; subsequent model operations belong to the voice task. */
esp_err_t muse_wakeword_init(void);
bool muse_wakeword_ready(void);
bool muse_wakeword_feed(const int16_t *pcm, size_t frames);
bool muse_wakeword_speech(int16_t *pcm);
void muse_wakeword_reset(void);
