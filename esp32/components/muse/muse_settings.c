/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "muse_settings.h"

#include <string.h>
#include <stdlib.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "nvs.h"
#include "nvs_flash.h"

#include "muse_link.h"

static const char *TAG = "muse_settings";

#define NS "muse"
#define DEFAULT_HOST "hatch.metaaivm.com"

static struct {
    uint8_t volume;
    bool speaker_on;
    uint8_t mic_gain;
    uint8_t brightness;
    uint16_t sleep_s;
    bool wifi_on;
    bool ble_on;
    bool home_face;
    int32_t home_offset;
    char ssid[MUSE_SSID_MAX + 1];
    char pass[MUSE_PASS_MAX + 1];
    char host[MUSE_HOST_MAX + 1];
    char vm[MUSE_VM_MAX + 1];
    char token[MUSE_TOKEN_MAX + 1];
    char openai_key[MUSE_TOKEN_MAX + 1];
    char openclaw_url[MUSE_OPENCLAW_URL_MAX + 1];
    char openclaw_token[MUSE_TOKEN_MAX + 1];
} s = {
    .volume = 70,
    .speaker_on = true,
    .mic_gain = 30,
    .brightness = 100,
    .sleep_s = 120,
    .wifi_on = true,
    .host = DEFAULT_HOST,
    .home_offset = INT32_MAX,
};

static SemaphoreHandle_t s_lock;
static nvs_handle_t s_nvs;
static muse_setting_cb_t s_listener;

#define LOCKED(body) do { xSemaphoreTake(s_lock, portMAX_DELAY); body; xSemaphoreGive(s_lock); } while (0)

static int clampi(int v, int lo, int hi)
{
    return v < lo ? lo : (v > hi ? hi : v);
}

/* Missing keys leave the default in place. */
static void load_str(const char *key, char *out, size_t len)
{
    size_t n = len;
    nvs_get_str(s_nvs, key, out, &n);
}

static void load_u8(const char *key, uint8_t *out)
{
    nvs_get_u8(s_nvs, key, out);
}

static void save_u8(const char *key, uint8_t v)
{
    nvs_set_u8(s_nvs, key, v);
    nvs_commit(s_nvs);
}

static void save_str(const char *key, const char *v)
{
    nvs_set_str(s_nvs, key, v);
    nvs_commit(s_nvs);
}

static void notify(muse_setting_t what)
{
    if (s_listener) {
        s_listener(what);
    }
}

esp_err_t muse_settings_init(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "NVS layout changed, erasing");
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    if (err != ESP_OK) {
        return err;
    }
    s_lock = xSemaphoreCreateMutex();
    err = nvs_open(NS, NVS_READWRITE, &s_nvs);
    if (err != ESP_OK) {
        return err;
    }

    uint8_t b;
    load_u8("volume", &s.volume);
    if (nvs_get_u8(s_nvs, "speaker", &b) == ESP_OK) {
        s.speaker_on = b;
    }
    load_u8("mic_gain", &s.mic_gain);
    load_u8("bright", &s.brightness);
    nvs_get_u16(s_nvs, "sleep_s", &s.sleep_s);
    if (nvs_get_u8(s_nvs, "wifi_on", &b) == ESP_OK) {
        s.wifi_on = b;
    }
    if (nvs_get_u8(s_nvs, "ble_on", &b) == ESP_OK) {
        s.ble_on = b;
    }
    load_str("ssid", s.ssid, sizeof(s.ssid));
    load_str("pass", s.pass, sizeof(s.pass));
    load_str("host", s.host, sizeof(s.host));
    load_str("vm", s.vm, sizeof(s.vm));
    load_str("token", s.token, sizeof(s.token));
    load_str("openai_key", s.openai_key, sizeof(s.openai_key));
    load_str("oc_url", s.openclaw_url, sizeof(s.openclaw_url));
    load_str("oc_token", s.openclaw_token, sizeof(s.openclaw_token));
    esp_err_t home_err = nvs_get_u8(s_nvs, "home_face", &b);
    if (home_err == ESP_OK && b <= 1) s.home_face = b;
    else if (home_err != ESP_ERR_NVS_NOT_FOUND) ESP_LOGW(TAG, "Invalid saved home preference");
    int32_t offset;
    home_err = nvs_get_i32(s_nvs, "home_offset", &offset);
    if (home_err == ESP_OK && offset >= -50400 && offset <= 50400 && offset % 60 == 0) s.home_offset = offset;
    else if (home_err != ESP_ERR_NVS_NOT_FOUND) ESP_LOGW(TAG, "Invalid saved home timezone");

    s.volume = clampi(s.volume, 0, 100);
    s.mic_gain = clampi(s.mic_gain, 0, MUSE_MIC_GAIN_MAX);
    s.brightness = clampi(s.brightness, 10, 100);
    ESP_LOGI(TAG, "vol %d%s, mic %d dB, bright %d, sleep %ds, wifi %s (%s), ble %s, muse %s",
             s.volume, s.speaker_on ? "" : " (speaker off)", s.mic_gain, s.brightness, s.sleep_s, s.wifi_on ? "on" : "off",
             "network saved by Link", s.ble_on ? "on" : "off", s.token[0] ? "token set" : "no token");
    return ESP_OK;
}

void muse_settings_set_listener(muse_setting_cb_t cb)
{
    s_listener = cb;
}

int muse_settings_volume(void) { return s.volume; }
bool muse_settings_speaker_on(void) { return s.speaker_on; }
int muse_settings_mic_gain(void) { return s.mic_gain; }
int muse_settings_brightness(void) { return s.brightness; }
int muse_settings_sleep_s(void) { return s.sleep_s; }
bool muse_settings_wifi_on(void) { return s.wifi_on; }
bool muse_settings_ble_on(void) { return s.ble_on; }

bool muse_settings_home_face(void) { return s.home_face; }
int32_t muse_settings_home_offset(void) { return s.home_offset; }

esp_err_t muse_settings_set_home_face(bool face)
{
    esp_err_t err = ESP_OK;
    LOCKED({
        if (s.home_face != face) {
            err = nvs_set_u8(s_nvs, "home_face", face);
            if (err == ESP_OK) err = nvs_commit(s_nvs);
            if (err == ESP_OK) s.home_face = face;
        }
    });
    if (err != ESP_OK) ESP_LOGE(TAG, "Home preference save failed: %s", esp_err_to_name(err));
    return err;
}

esp_err_t muse_settings_set_home_offset(int32_t seconds)
{
    if (seconds < -50400 || seconds > 50400 || seconds % 60) {
        ESP_LOGE(TAG, "Invalid home timezone offset");
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = ESP_OK;
    LOCKED({
        if (s.home_offset != seconds) {
            err = nvs_set_i32(s_nvs, "home_offset", seconds);
            if (err == ESP_OK) err = nvs_commit(s_nvs);
            if (err == ESP_OK) s.home_offset = seconds;
        }
    });
    if (err != ESP_OK) ESP_LOGE(TAG, "Home timezone save failed: %s", esp_err_to_name(err));
    return err;
}

/* Home Link owns the saved networks (this is the first); the local copy is only a fallback. */
void muse_settings_wifi(char ssid[MUSE_SSID_MAX + 1], char pass[MUSE_PASS_MAX + 1])
{
    if (muse_link_wifi_get(ssid, pass)) {
        return;
    }
    LOCKED({
        strlcpy(ssid, s.ssid, MUSE_SSID_MAX + 1);
        if (pass) {
            strlcpy(pass, s.pass, MUSE_PASS_MAX + 1);
        }
    });
}

void muse_settings_hatch_host(char out[MUSE_HOST_MAX + 1])
{
    LOCKED(strlcpy(out, s.host, MUSE_HOST_MAX + 1));
}

void muse_settings_hatch_vm(char out[MUSE_VM_MAX + 1])
{
    LOCKED(strlcpy(out, s.vm, MUSE_VM_MAX + 1));
}

void muse_settings_hatch_token(char out[MUSE_TOKEN_MAX + 1])
{
    LOCKED(strlcpy(out, s.token, MUSE_TOKEN_MAX + 1));
}

size_t muse_settings_hatch_token_len(void)
{
    size_t n;
    LOCKED(n = strlen(s.token));
    return n;
}

void muse_settings_openai_key(char out[MUSE_TOKEN_MAX + 1])
{
    LOCKED(strlcpy(out, s.openai_key, MUSE_TOKEN_MAX + 1));
}

size_t muse_settings_openai_key_len(void)
{
    size_t n;
    LOCKED(n = strlen(s.openai_key));
    return n;
}

esp_err_t muse_settings_set_openai_key(const char *key)
{
    if (!key || strlen(key) > MUSE_TOKEN_MAX) {
        ESP_LOGW(TAG, "OpenAI key rejected: invalid length");
        return ESP_ERR_INVALID_SIZE;
    }
    for (const unsigned char *p = (const unsigned char *)key; *p; p++) {
        if (*p <= 0x20 || *p >= 0x7f) {
            ESP_LOGW(TAG, "OpenAI key rejected: invalid character");
            return ESP_ERR_INVALID_ARG;
        }
    }
    esp_err_t err;
    LOCKED({
        err = nvs_set_str(s_nvs, "openai_key", key);
        if (err == ESP_OK) err = nvs_commit(s_nvs);
        if (err == ESP_OK) strlcpy(s.openai_key, key, sizeof(s.openai_key));
    });
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "OpenAI key save failed: %s", esp_err_to_name(err));
    } else {
        notify(MUSE_SETTING_OPENAI);
    }
    return err;
}

void muse_settings_openclaw(char url[MUSE_OPENCLAW_URL_MAX + 1], char token[MUSE_TOKEN_MAX + 1])
{
    LOCKED({
        if (url) strlcpy(url, s.openclaw_url, MUSE_OPENCLAW_URL_MAX + 1);
        if (token) strlcpy(token, s.openclaw_token, MUSE_TOKEN_MAX + 1);
    });
}

bool muse_settings_openclaw_enabled(void)
{
    bool enabled;
    LOCKED(enabled = s.openclaw_url[0] != '\0');
    return enabled;
}

bool muse_settings_openclaw_token_set(void)
{
    bool set;
    LOCKED(set = s.openclaw_token[0] != '\0');
    return set;
}

static esp_err_t save_openclaw(const char *name, const char *value, char *out, size_t size)
{
    esp_err_t err;
    LOCKED({
        err = nvs_set_str(s_nvs, name, value);
        if (err == ESP_OK) err = nvs_commit(s_nvs);
        if (err == ESP_OK) strlcpy(out, value, size);
    });
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "OpenClaw setting save failed: %s", esp_err_to_name(err));
    } else {
        notify(MUSE_SETTING_OPENCLAW);
    }
    return err;
}

esp_err_t muse_settings_set_openclaw_url(const char *url)
{
    static const char suffix[] = "/v1/chat/completions";
    bool valid = url && strlen(url) <= MUSE_OPENCLAW_URL_MAX;
    if (valid && url[0]) {
        valid = !strncmp(url, "https://", 8);
        const char *host = valid ? url + 8 : "";
        size_t n = strspn(host, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-");
        const char *end = host + n;
        valid = valid && n > 0;
        if (valid && *end == ':') {
            const char *port = ++end;
            while (*end >= '0' && *end <= '9') end++;
            long number = strtol(port, NULL, 10);
            valid = end > port && number >= 1 && number <= 65535;
        }
        valid = valid && !strcmp(end, suffix);
    }
    if (!valid) {
        ESP_LOGW(TAG, "OpenClaw URL rejected: expected an HTTPS chat endpoint");
        return ESP_ERR_INVALID_ARG;
    }
    return save_openclaw("oc_url", url, s.openclaw_url, sizeof(s.openclaw_url));
}

esp_err_t muse_settings_set_openclaw_token(const char *token)
{
    bool valid = token && strlen(token) <= MUSE_TOKEN_MAX;
    if (valid) {
        for (const unsigned char *p = (const unsigned char *)token; *p; p++) {
            if (*p <= 0x20 || *p >= 0x7f) valid = false;
        }
    }
    if (!valid) {
        ESP_LOGW(TAG, "OpenClaw token rejected: invalid length or character");
        return ESP_ERR_INVALID_ARG;
    }
    return save_openclaw("oc_token", token, s.openclaw_token, sizeof(s.openclaw_token));
}

void muse_settings_set_volume(int pct)
{
    s.volume = clampi(pct, 0, 100);
    save_u8("volume", s.volume);
    notify(MUSE_SETTING_VOLUME);
}

void muse_settings_set_speaker_on(bool on)
{
    s.speaker_on = on;
    save_u8("speaker", on);
    notify(MUSE_SETTING_SPEAKER);
}

void muse_settings_set_mic_gain(int db)
{
    s.mic_gain = clampi(db, 0, MUSE_MIC_GAIN_MAX);
    save_u8("mic_gain", s.mic_gain);
    notify(MUSE_SETTING_MIC_GAIN);
}

void muse_settings_set_brightness(int pct)
{
    s.brightness = clampi(pct, 10, 100);
    save_u8("bright", s.brightness);
    notify(MUSE_SETTING_BRIGHTNESS);
}

void muse_settings_set_sleep_s(int secs)
{
    s.sleep_s = clampi(secs, 0, 3600);
    nvs_set_u16(s_nvs, "sleep_s", s.sleep_s);
    nvs_commit(s_nvs);
    notify(MUSE_SETTING_SLEEP);
}

void muse_settings_set_wifi_on(bool on)
{
    s.wifi_on = on;
    save_u8("wifi_on", on);
    notify(MUSE_SETTING_WIFI);
}

void muse_settings_set_ble_on(bool on)
{
    s.ble_on = on;
    save_u8("ble_on", on);
    notify(MUSE_SETTING_BLE);
}

void muse_settings_set_wifi(const char *ssid, const char *pass)
{
    if (muse_link_wifi_set(ssid ? ssid : "", ssid && ssid[0] && pass ? pass : "")) {
        ESP_LOGI(TAG, "wifi network: %s", ssid && ssid[0] ? ssid : "(all forgotten)");
        notify(MUSE_SETTING_WIFI);
        return;
    }
    LOCKED({
        strlcpy(s.ssid, ssid ? ssid : "", sizeof(s.ssid));
        strlcpy(s.pass, s.ssid[0] && pass ? pass : "", sizeof(s.pass));
        save_str("ssid", s.ssid);
        save_str("pass", s.pass);
    });
    ESP_LOGI(TAG, "wifi network: %s", s.ssid[0] ? s.ssid : "(forgotten)");
    notify(MUSE_SETTING_WIFI);
}

void muse_settings_set_hatch_host(const char *host)
{
    LOCKED({
        strlcpy(s.host, host && host[0] ? host : DEFAULT_HOST, sizeof(s.host));
        save_str("host", s.host);
    });
    notify(MUSE_SETTING_HATCH);
}

void muse_settings_set_hatch_vm(const char *vm)
{
    LOCKED({
        strlcpy(s.vm, vm ? vm : "", sizeof(s.vm));
        save_str("vm", s.vm);
    });
    notify(MUSE_SETTING_HATCH);
}

esp_err_t muse_settings_set_hatch_token(const char *token, bool append)
{
    esp_err_t err = ESP_OK;
    LOCKED({
        size_t have = append ? strlen(s.token) : 0;
        size_t add = strlen(token ? token : "");
        if (have + add > MUSE_TOKEN_MAX) {
            err = ESP_ERR_INVALID_SIZE;
        } else {
            memcpy(s.token + have, token, add);
            s.token[have + add] = '\0';
            save_str("token", s.token);
        }
    });
    if (err == ESP_OK) {
        notify(MUSE_SETTING_HATCH);
    }
    return err;
}
