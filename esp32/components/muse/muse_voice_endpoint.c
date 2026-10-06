// SPDX-License-Identifier: Apache-2.0
#include "muse_voice_endpoint.h"

muse_endpoint_result_t muse_voice_endpoint_feed(muse_voice_endpoint_t *state, bool speech)
{
    state->elapsed_ms += 20;
    if (speech) {
        state->speech_ms += 20;
        state->silence_ms = 0;
        if (state->speech_ms >= 200) state->heard = true;
    } else {
        state->speech_ms = 0;
        state->silence_ms += 20;
    }
    if (state->heard && state->silence_ms >= 900) return MUSE_ENDPOINT_SEND;
    if (!state->heard && state->elapsed_ms >= 5000) return MUSE_ENDPOINT_EMPTY;
    if (state->elapsed_ms >= 15000) return state->heard ? MUSE_ENDPOINT_SEND : MUSE_ENDPOINT_EMPTY;
    return MUSE_ENDPOINT_LISTEN;
}
