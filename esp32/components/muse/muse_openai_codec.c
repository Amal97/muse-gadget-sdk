// SPDX-License-Identifier: Apache-2.0
#include "muse_openai_codec.h"

#include <string.h>

static void put_le(uint8_t *out, uint32_t value, size_t bytes)
{
    for (size_t i = 0; i < bytes; i++) out[i] = (uint8_t)(value >> (8 * i));
}

void muse_openai_wav_header(uint8_t out[44], uint32_t frames)
{
    memcpy(out, "RIFF", 4);
    put_le(out + 4, 36 + frames * 2, 4);
    memcpy(out + 8, "WAVEfmt ", 8);
    put_le(out + 16, 16, 4);
    put_le(out + 20, 1, 2);
    put_le(out + 22, 1, 2);
    put_le(out + 24, 16000, 4);
    put_le(out + 28, 32000, 4);
    put_le(out + 32, 2, 2);
    put_le(out + 34, 16, 2);
    memcpy(out + 36, "data", 4);
    put_le(out + 40, frames * 2, 4);
}

size_t muse_openai_resample_byte(muse_openai_resampler_t *state, uint8_t byte, int16_t out[2])
{
    state->bytes[state->used++] = byte;
    if (state->used < 6) return 0;
    int16_t a = (int16_t)((uint16_t)state->bytes[0] | (uint16_t)state->bytes[1] << 8);
    int16_t b = (int16_t)((uint16_t)state->bytes[2] | (uint16_t)state->bytes[3] << 8);
    int16_t c = (int16_t)((uint16_t)state->bytes[4] | (uint16_t)state->bytes[5] << 8);
    out[0] = a;
    out[1] = (int16_t)(((int32_t)b + c) / 2);
    state->used = 0;
    return 2;
}
