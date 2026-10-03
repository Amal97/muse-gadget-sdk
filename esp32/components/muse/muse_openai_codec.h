// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stddef.h>
#include <stdint.h>

void muse_openai_wav_header(uint8_t out[44], uint32_t frames);

typedef struct {
    uint8_t bytes[6];
    size_t used;
} muse_openai_resampler_t;

/* One 24 kHz PCM byte at a time; every three samples produce two at 16 kHz. */
size_t muse_openai_resample_byte(muse_openai_resampler_t *state, uint8_t byte, int16_t out[2]);
