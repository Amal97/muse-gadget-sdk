// SPDX-License-Identifier: Apache-2.0
#include "muse_wakeword.h"
#include "sdkconfig.h"

#if CONFIG_MUSE_WAKEWORD
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_wn_iface.h"
#include "esp_wn_models.h"
#include "esp_vad.h"
#include "model_path.h"
#include "muse_audio.h"

static const char *TAG = "muse_wakeword";
static srmodel_list_t *s_models;
static const esp_wn_iface_t *s_iface;
static model_iface_data_t *s_model;
static vad_handle_t s_vad;
static int16_t *s_frame;
static size_t s_size, s_fill;
static atomic_bool s_ready;

static void drop_processing(void)
{
    atomic_store(&s_ready, false);
    if (s_vad) vad_destroy(s_vad);
    if (s_model) s_iface->destroy(s_model);
    free(s_frame);
    s_vad = NULL;
    s_model = NULL;
    s_frame = NULL;
    s_size = s_fill = 0;
}

esp_err_t muse_wakeword_init(void)
{
    if (muse_wakeword_ready()) return ESP_OK;
    bool cached = s_models != NULL;
    if (!cached) s_models = esp_srmodel_init("model");
    const char *name = s_models ? esp_srmodel_filter(s_models, "wn9", "hiesp") : NULL;
    if (!name || strcmp(name, "wn9_hiesp") || !(s_iface = esp_wn_handle_from_name(name))) {
        ESP_LOGE(TAG, "Hi ESP model unavailable; flash the model partition");
        goto failed;
    }
    s_model = s_iface->create(name, DET_MODE_95);
    if (!s_model) {
        ESP_LOGE(TAG, "Hi ESP model allocation failed");
        goto failed;
    }
    int size = s_iface->get_samp_chunksize(s_model);
    if (size <= 0 || size > MUSE_AUDIO_RATE ||
        s_iface->get_channel_num(s_model) != 1 ||
        s_iface->get_samp_rate(s_model) != MUSE_AUDIO_RATE) {
        ESP_LOGE(TAG, "Wake model requires unsupported audio geometry");
        goto failed;
    }
    s_size = (size_t)size;
    s_frame = heap_caps_malloc(s_size * sizeof(int16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    s_vad = vad_create(VAD_MODE_3);
    if (!s_frame || !s_vad) {
        ESP_LOGE(TAG, "Wake-word/VAD buffer allocation failed");
        goto failed;
    }
    atomic_store(&s_ready, true);
    ESP_LOGI(TAG, "Hi ESP ready: %u samples, %d Hz; idle audio stays local",
             (unsigned)s_size, MUSE_AUDIO_RATE);
    return ESP_OK;
failed:
    drop_processing();
    /* A voice-task reset retains the mapping: unmapping also needs an internal stack. */
    if (!cached && s_models) {
        esp_srmodel_deinit(s_models);
        s_models = NULL;
    }
    return ESP_FAIL;
}

bool muse_wakeword_ready(void) { return atomic_load(&s_ready); }

void muse_wakeword_reset(void)
{
    s_fill = 0;
    if (muse_wakeword_ready()) {
        /* ESP-SR 2.5.5's wn9_hiesp clean crashes; recreate state using cached coefficients. */
        drop_processing();
        if (muse_wakeword_init() != ESP_OK) {
            ESP_LOGE(TAG, "wake-word reset failed; use Talk and restart to restore detection");
        }
    }
}

bool muse_wakeword_feed(const int16_t *pcm, size_t frames)
{
    if (!muse_wakeword_ready()) return false;
    while (frames) {
        size_t count = frames < s_size - s_fill ? frames : s_size - s_fill;
        memcpy(s_frame + s_fill, pcm, count * sizeof(int16_t));
        s_fill += count;
        pcm += count;
        frames -= count;
        if (s_fill == s_size) {
            s_fill = 0;
            int result = s_iface->detect(s_model, s_frame);
            if (result == WAKENET_DETECTED) return true;
        }
    }
    return false;
}

bool muse_wakeword_speech(int16_t *pcm)
{
    return muse_wakeword_ready() && vad_process(s_vad, pcm, MUSE_AUDIO_RATE, 20) == VAD_SPEECH;
}
#else
esp_err_t muse_wakeword_init(void) { return ESP_ERR_NOT_SUPPORTED; }
bool muse_wakeword_ready(void) { return false; }
bool muse_wakeword_feed(const int16_t *pcm, size_t frames) { (void)pcm; (void)frames; return false; }
bool muse_wakeword_speech(int16_t *pcm) { (void)pcm; return false; }
void muse_wakeword_reset(void) {}
#endif
