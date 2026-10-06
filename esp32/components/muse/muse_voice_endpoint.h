// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    unsigned elapsed_ms;
    unsigned speech_ms;
    unsigned silence_ms;
    bool heard;
} muse_voice_endpoint_t;

typedef enum {
    MUSE_ENDPOINT_LISTEN,
    MUSE_ENDPOINT_SEND,
    MUSE_ENDPOINT_EMPTY,
} muse_endpoint_result_t;

/* Called once per 20 ms microphone frame after a local wake-word detection. */
muse_endpoint_result_t muse_voice_endpoint_feed(muse_voice_endpoint_t *state, bool speech);
