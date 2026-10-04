// SPDX-License-Identifier: Apache-2.0
#include "muse_companion.h"
#include <stdint.h>
#include <time.h>
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "nvs.h"
#include "muse_state.h"

static const char *TAG = "companion";
static SemaphoreHandle_t s_lock;
static muse_timer_state_t s_phase;
static int64_t s_deadline, s_epoch;
static unsigned s_pending;

static bool save_timer(muse_timer_state_t phase, int64_t epoch)
{
    nvs_handle_t handle;
    esp_err_t err = nvs_open("companion", NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "timer storage open failed: %s", esp_err_to_name(err));
        muse_state_set_caption("TIMER STORAGE FAILED");
        return false;
    }
    err = nvs_set_u8(handle, "timer_phase", (uint8_t)phase);
    if (err == ESP_OK) err = nvs_set_i64(handle, "timer_epoch", epoch);
    if (err == ESP_OK) err = nvs_commit(handle);
    nvs_close(handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "timer persistence failed: %s", esp_err_to_name(err));
        muse_state_set_caption("TIMER NOT SAVED");
    }
    return err == ESP_OK;
}

static bool set_timer(unsigned seconds, unsigned mode)
{
    if (!s_lock || !seconds || seconds > 7 * 86400) {
        ESP_LOGW(TAG, "invalid timer duration");
        muse_state_set_caption("TIMER MUST BE 1 SECOND TO 7 DAYS");
        return false;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if ((mode == 1 && (!s_pending || seconds != s_pending)) ||
        (mode == 2 && s_phase != MUSE_TIMER_RINGING)) {
        xSemaphoreGive(s_lock);
        ESP_LOGW(TAG, "timer confirmation is stale or timer is not ringing");
        muse_state_set_caption("TIMER CHANGED; REVIEW IT AGAIN");
        return false;
    }
    if (!mode && s_phase != MUSE_TIMER_OFF) {
        s_pending = seconds;
        xSemaphoreGive(s_lock);
        muse_state_set_caption("TIMER ALREADY ACTIVE; CONFIRM REPLACEMENT IN COMPANION");
        return false;
    }
    time_t now = time(NULL);
    int64_t epoch = now >= 1700000000 ? (int64_t)now + seconds : 0;
    bool ok = save_timer(MUSE_TIMER_RUNNING, epoch);
    if (ok) {
        s_deadline = esp_timer_get_time() + (int64_t)seconds * 1000000;
        s_epoch = epoch;
        s_phase = MUSE_TIMER_RUNNING;
        s_pending = 0;
    }
    xSemaphoreGive(s_lock);
    if (ok) muse_state_set_caption("LOCAL TIMER SET: %u SECONDS", seconds);
    return ok;
}

bool muse_timer_start(unsigned seconds) { return set_timer(seconds, 0); }

unsigned muse_timer_pending(void)
{
    if (!s_lock) return 0;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    unsigned pending = s_pending;
    xSemaphoreGive(s_lock);
    return pending;
}

void muse_timer_cancel_replace(void)
{
    if (!s_lock) return;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_pending = 0;
    xSemaphoreGive(s_lock);
}

bool muse_timer_confirm_replace(unsigned seconds)
{
    return set_timer(seconds, 1);
}

bool muse_timer_snooze(unsigned seconds)
{
    return set_timer(seconds, 2);
}

bool muse_timer_dismiss(void)
{
    if (!s_lock) return false;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    bool ok = save_timer(MUSE_TIMER_OFF, 0);
    if (ok) {
        s_phase = MUSE_TIMER_OFF;
        s_pending = 0;
    }
    xSemaphoreGive(s_lock);
    return ok;
}

muse_timer_state_t muse_timer_status(unsigned *seconds)
{
    if (!s_lock) return MUSE_TIMER_OFF;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    muse_timer_state_t phase = s_phase;
    int64_t remaining = s_deadline - esp_timer_get_time();
    if (seconds) *seconds = phase == MUSE_TIMER_RUNNING && remaining > 0 ?
        (unsigned)((remaining + 999999) / 1000000) : 0;
    xSemaphoreGive(s_lock);
    return phase;
}

static void timer_tick(void)
{
    bool wake = false;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    time_t now = time(NULL);
    if (s_phase == MUSE_TIMER_WAIT_CLOCK && now >= 1700000000) {
        s_deadline = esp_timer_get_time() + (s_epoch - (int64_t)now) * 1000000;
        s_phase = MUSE_TIMER_RUNNING;
    }
    if (s_phase == MUSE_TIMER_RUNNING && !s_epoch && now >= 1700000000) {
        int64_t remaining = s_deadline - esp_timer_get_time();
        s_epoch = (int64_t)now + (remaining > 0 ? (remaining + 999999) / 1000000 : 0);
        if (!save_timer(MUSE_TIMER_RUNNING, s_epoch)) ESP_LOGE(TAG, "timer clock anchor not persisted");
    }
    if (s_phase == MUSE_TIMER_RUNNING && esp_timer_get_time() >= s_deadline) {
        s_phase = MUSE_TIMER_RINGING;
        if (!save_timer(MUSE_TIMER_RINGING, s_epoch)) ESP_LOGE(TAG, "alarm state not persisted");
    }
    wake = s_phase == MUSE_TIMER_RINGING;
    xSemaphoreGive(s_lock);
    if (wake && muse_state_asleep()) muse_state_set_asleep(false);
}

static void timer_worker(void *arg)
{
    (void)arg;
    for (;;) {
        timer_tick();
        vTaskDelay(pdMS_TO_TICKS(250));
    }
}

void muse_companion_start(void)
{
    s_lock = xSemaphoreCreateMutex();
    ESP_ERROR_CHECK(s_lock ? ESP_OK : ESP_ERR_NO_MEM);
    nvs_handle_t handle;
    esp_err_t err = nvs_open("companion", NVS_READWRITE, &handle);
    if (err == ESP_OK) {
        uint8_t phase = MUSE_TIMER_OFF;
        err = nvs_get_u8(handle, "timer_phase", &phase);
        if (err != ESP_OK && err != ESP_ERR_NVS_NOT_FOUND) ESP_LOGE(TAG, "timer state read failed");
        if (err == ESP_OK && phase != MUSE_TIMER_OFF) {
            err = nvs_get_i64(handle, "timer_epoch", &s_epoch);
            if (err == ESP_OK && phase == MUSE_TIMER_RINGING) s_phase = MUSE_TIMER_RINGING;
            else if (err == ESP_OK && phase == MUSE_TIMER_RUNNING && s_epoch > 0) {
                s_phase = MUSE_TIMER_WAIT_CLOCK;
            } else {
                ESP_LOGW(TAG, "timer interrupted: invalid or unavailable persisted clock");
                muse_state_set_caption("TIMER INTERRUPTED BY RESTART; CHECK CLOCK");
            }
        }
        nvs_close(handle);
    } else {
        ESP_LOGE(TAG, "timer storage unavailable");
        muse_state_set_caption("TIMER STORAGE UNAVAILABLE");
    }
    ESP_ERROR_CHECK(xTaskCreate(timer_worker, "local_timer", 3072, NULL, 3, NULL) ==
                    pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
}
