// SPDX-License-Identifier: Apache-2.0
#include "muse_openai.h"
#include "muse_openai_codec.h"
#include "muse_chat_priv.h"

#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>

#include "cJSON.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_netif_sntp.h"
#include "esp_timer.h"
#include "esp_random.h"
#include <ctype.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mbedtls/platform_util.h"
#include "sdkconfig.h"

#include "muse_audio.h"
#include "muse_settings.h"
#include "muse_state.h"
#include "muse_wifi.h"
#include "muse_companion.h"

#define RECORD_FRAMES (16000 * 16)
#define AUDIO_FRAMES 16384
#define TEXT_CAP 2048
#define JSON_CAP 16384
#define HISTORY_PAIRS 4
#define REQUEST_US (90LL * 1000000)

static const char *TAG = "muse_openai";
extern const char openai_root_pem[] asm("_binary_openai_root_pem_start");
#if CONFIG_MUSE_OPENCLAW
extern const char openclaw_root_pem[] asm("_binary_openclaw_root_start");
#endif
static SemaphoreHandle_t s_data;
static QueueHandle_t s_jobs, s_events;
static int16_t *s_record, *s_audio;
static size_t s_record_n, s_audio_head, s_audio_n;
static char s_reply[TEXT_CAP];
static atomic_uint s_generation;
static atomic_bool s_busy, s_clear_history;
static portMUX_TYPE s_status_lock = portMUX_INITIALIZER_UNLOCKED;
static muse_hatch_state_t s_status = MUSE_HATCH_UNTESTED;
static char s_detail[48];
#if CONFIG_MUSE_OPENCLAW
static muse_notification_t s_notification;
static char s_notification_ack[33];
static char s_mac_job[33];
static char *s_companion_json;
static unsigned s_companion_version;
static char s_reply_arm[33], s_record_reply[33];
static muse_notification_t s_draft_notice;
#endif

typedef enum { JOB_VOICE, JOB_TEXT, JOB_TEST, JOB_NOTIFICATION, JOB_COMPANION } job_kind_t;
typedef struct {
    job_kind_t kind;
    unsigned generation;
    int16_t *pcm;
    size_t frames;
    char *text;
    char reply_target[33];
    const char *bridge_url, *bridge_token;
} job_t;

typedef struct {
    unsigned generation;
    muse_hatch_ev_t type;
    char text[96];
} event_t;

typedef struct {
    const void *data;
    size_t size;
} part_t;

static bool current(unsigned generation)
{
    return generation == atomic_load(&s_generation);
}

static void report(muse_hatch_state_t state, const char *detail)
{
    portENTER_CRITICAL(&s_status_lock);
    s_status = state;
    strlcpy(s_detail, detail, sizeof(s_detail));
    portEXIT_CRITICAL(&s_status_lock);
}

static void emit(const job_t *job, muse_hatch_ev_t type, const char *text)
{
    if (!current(job->generation)) return;
    if (job->kind == JOB_TEXT) {
        switch (type) {
        case MUSE_HATCH_EV_REPLY:
            muse_hatch_console("text", text, "\"msg\":0");
            muse_hatch_console("message_done", NULL, "\"msg\":0,\"bytes\":%u", (unsigned)strlen(text));
            break;
        case MUSE_HATCH_EV_DONE:
            muse_hatch_console("done", NULL, "\"messages\":1,\"complete\":true");
            break;
        case MUSE_HATCH_EV_ERROR:
            muse_hatch_console("error", text, NULL);
            break;
        default:
            break;
        }
        return;
    }
    event_t event = { .generation = job->generation, .type = type };
    strlcpy(event.text, text ? text : "", sizeof(event.text));
    if (xQueueSend(s_events, &event, 0) != pdTRUE) {
        ESP_LOGE(TAG, "voice event queue full");
    }
}

static bool openclaw_chat(void)
{
#if CONFIG_MUSE_OPENCLAW
    return muse_settings_openclaw_enabled();
#else
    return false;
#endif
}

static void request_error(bool openclaw, const char *detail, char *why, size_t cap)
{
    snprintf(why, cap, "%s %s", openclaw ? "OPENCLAW" : "OPENAI", detail);
}

static void http_error(int status, bool openclaw, char *why, size_t cap)
{
    if (openclaw) {
        if (status == 401) request_error(true, "BRIDGE TOKEN INVALID", why, cap);
        else snprintf(why, cap, "OPENCLAW HTTP %d", status);
        return;
    }
    switch (status) {
    case 401: strlcpy(why, "OPENAI KEY INVALID", cap); break;
    case 403: strlcpy(why, "OPENAI ACCESS DENIED", cap); break;
    case 404: strlcpy(why, "OPENAI MODEL NOT AVAILABLE", cap); break;
    case 429: strlcpy(why, "OPENAI QUOTA OR RATE LIMIT", cap); break;
    default: snprintf(why, cap, "OPENAI HTTP %d", status); break;
    }
}

static bool push_audio(unsigned generation, const int16_t *pcm, size_t frames)
{
    int64_t deadline = esp_timer_get_time() + REQUEST_US;
    while (frames && current(generation)) {
        xSemaphoreTake(s_data, portMAX_DELAY);
        size_t n = current(generation) ? AUDIO_FRAMES - s_audio_n : 0;
        if (n > frames) n = frames;
        for (size_t i = 0; i < n; i++) {
            s_audio[(s_audio_head + s_audio_n + i) % AUDIO_FRAMES] = pcm[i];
        }
        s_audio_n += n;
        xSemaphoreGive(s_data);
        pcm += n;
        frames -= n;
        if (!n) {
            if (esp_timer_get_time() >= deadline) return false;
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }
    return frames == 0;
}

static esp_err_t on_http_event(esp_http_client_event_t *event)
{
    if (event->event_id == HTTP_EVENT_ON_HEADER && !strcasecmp(event->header_key, "Content-Type")) {
        strlcpy(event->user_data, event->header_value, 64);
    }
    return ESP_OK;
}

/* Redirects are disabled so credentials never leave their intended endpoint. */
static bool request(const job_t *job, const char *path, esp_http_client_method_t method,
                    const char *content_type, const part_t *parts, size_t part_count,
                    char *json, size_t json_cap, bool speech, bool openclaw, char *why, size_t why_cap)
{
    bool ok = false;
    char url[MUSE_OPENCLAW_URL_MAX + 1];
    snprintf(url, sizeof(url), "https://api.openai.com/v1/%s", path);
    char *auth = malloc(MUSE_TOKEN_MAX + 8);
    if (!auth) {
        strlcpy(why, "NO MEMORY FOR HTTPS", why_cap);
        return false;
    }
    memcpy(auth, "Bearer ", 7);
    muse_settings_openai_key(auth + 7);
    const char *certificate = openai_root_pem, *common_name = NULL;
#if CONFIG_MUSE_OPENCLAW
    if (openclaw) {
        if (job->bridge_url && job->bridge_token) {
            strlcpy(url, job->bridge_url, sizeof(url));
            strlcpy(auth + 7, job->bridge_token, MUSE_TOKEN_MAX + 1);
        } else muse_settings_openclaw(url, auth + 7);
        if (strcmp(path, "chat/completions")) {
            char *endpoint = strstr(url, "/v1/chat/completions");
            if (endpoint) snprintf(endpoint, sizeof(url) - (size_t)(endpoint - url), "/v1/%s", path);
        }
        certificate = openclaw_root_pem;
        common_name = "muse-openclaw.local";
        if (!url[0] || !auth[7]) {
            mbedtls_platform_zeroize(auth, MUSE_TOKEN_MAX + 8);
            free(auth);
            request_error(true, "BRIDGE NOT CONFIGURED", why, why_cap);
            return false;
        }
    }
#endif
    char response_type[64] = "";
    esp_http_client_config_t config = {
        .url = url,
        .method = method,
        .cert_pem = certificate,
        .common_name = common_name,
        .timeout_ms = openclaw && strcmp(path, "chat/completions") ? 2000 : 30000,
        .buffer_size = 2048,
        .buffer_size_tx = 2048,
        .disable_auto_redirect = true,
        .event_handler = on_http_event,
        .user_data = response_type,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    esp_err_t err = client ? esp_http_client_set_header(client, "Authorization", auth) : ESP_ERR_NO_MEM;
    mbedtls_platform_zeroize(auth, MUSE_TOKEN_MAX + 8);
    free(auth);
    if (err == ESP_OK && content_type) err = esp_http_client_set_header(client, "Content-Type", content_type);
    size_t total = 0;
    for (size_t i = 0; i < part_count; i++) total += parts[i].size;
    int64_t deadline = esp_timer_get_time() +
        (openclaw && strcmp(path, "chat/completions") ? 5000000 : REQUEST_US);
    if (err == ESP_OK && current(job->generation)) err = esp_http_client_open(client, (int)total);
    if (err != ESP_OK || !current(job->generation)) {
        request_error(openclaw, "HTTPS CONNECT FAILED", why, why_cap);
        if (client && current(job->generation)) {
            int tls_error = 0, flags = 0;
            esp_http_client_get_and_clear_last_tls_error(client, &tls_error, &flags);
            ESP_LOGW(TAG, "TLS error %d, certificate flags 0x%x, UTC %lld",
                     tls_error, flags, (long long)time(NULL));
            if (flags) request_error(openclaw, "CERTIFICATE CHECK FAILED", why, why_cap);
        }
        goto cleanup;
    }
    for (size_t i = 0; i < part_count; i++) {
        const char *data = parts[i].data;
        size_t remaining = parts[i].size;
        while (remaining && current(job->generation)) {
            size_t chunk = remaining > 4096 ? 4096 : remaining;
            int n = esp_http_client_write(client, data, (int)chunk);
            if (n <= 0 || esp_timer_get_time() >= deadline) {
                request_error(openclaw, "UPLOAD FAILED", why, why_cap);
                goto cleanup;
            }
            remaining -= (size_t)n;
            data += n;
        }
        if (!current(job->generation)) goto cleanup;
    }
    if (esp_http_client_fetch_headers(client) < 0) {
        request_error(openclaw, "RESPONSE TIMEOUT", why, why_cap);
        goto cleanup;
    }
    int status = esp_http_client_get_status_code(client);
    if (status < 200 || status >= 300) {
        http_error(status, openclaw, why, why_cap);
        goto cleanup;
    }
    if (speech && strncasecmp(response_type, "audio/pcm", 9)
        && strncasecmp(response_type, "application/octet-stream", 24)) {
        strlcpy(why, "OPENAI SPEECH FORMAT INVALID", why_cap);
        goto cleanup;
    }
    size_t used = 0, audio_bytes = 0;
    muse_openai_resampler_t resampler = {0};
    uint8_t buf[1024];
    while (current(job->generation)) {
        int n = esp_http_client_read(client, (char *)buf, sizeof(buf));
        if (n < 0 || esp_timer_get_time() >= deadline) {
            request_error(openclaw, "DOWNLOAD FAILED", why, why_cap);
            goto cleanup;
        }
        if (!n) {
            if (!esp_http_client_is_complete_data_received(client)) {
                request_error(openclaw, "RESPONSE INCOMPLETE", why, why_cap);
                goto cleanup;
            }
            break;
        }
        if (speech) {
            audio_bytes += (size_t)n;
            if (audio_bytes > 24000 * 2 * 60) {
                strlcpy(why, "OPENAI SPEECH TOO LONG", why_cap);
                goto cleanup;
            }
            int16_t pcm[342];
            size_t frames = 0;
            for (int i = 0; i < n; i++) {
                frames += muse_openai_resample_byte(&resampler, buf[i], pcm + frames);
            }
            if (!push_audio(job->generation, pcm, frames)) {
                strlcpy(why, "OPENAI AUDIO PLAYBACK STALLED", why_cap);
                goto cleanup;
            }
        } else {
            if (used + (size_t)n >= json_cap) {
                request_error(openclaw, "RESPONSE TOO LARGE", why, why_cap);
                goto cleanup;
            }
            memcpy(json + used, buf, (size_t)n);
            used += (size_t)n;
        }
    }
    if (speech && (!audio_bytes || (audio_bytes & 1))) {
        strlcpy(why, "OPENAI SPEECH INVALID", why_cap);
        goto cleanup;
    }
    if (!speech) json[used] = '\0';
    ok = current(job->generation);
cleanup:
    if (client) {
        esp_http_client_close(client);
        esp_http_client_cleanup(client);
    }
    if (!ok && current(job->generation)) {
        ESP_LOGW(TAG, "%s (%s)", why, esp_err_to_name(err));
    }
    return ok;
}

static char *json_request(const job_t *job, const char *path, cJSON *body, bool speech,
                          bool openclaw, char *why, size_t cap)
{
    char *encoded = body ? cJSON_PrintUnformatted(body) : NULL;
    char *response = heap_caps_malloc(JSON_CAP, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!encoded || !response) {
        free(encoded);
        free(response);
        strlcpy(why, "NO MEMORY FOR OPENAI REQUEST", cap);
        return NULL;
    }
    part_t part = { encoded, strlen(encoded) };
    bool ok = request(job, path, HTTP_METHOD_POST, "application/json", &part, 1,
                      response, JSON_CAP, speech, openclaw, why, cap);
    free(encoded);
    if (!ok) {
        free(response);
        return NULL;
    }
    return response;
}

static char *transcribe(const job_t *job, char *why, size_t cap)
{
    static const char tail[] = "\r\n--muse-openai-voice--\r\n";
    char head[512];
    int n = snprintf(head, sizeof(head),
                     "--muse-openai-voice\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n%s\r\n"
                     "--muse-openai-voice\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\njson\r\n"
                     "--muse-openai-voice\r\nContent-Disposition: form-data; name=\"file\"; filename=\"speech.wav\"\r\n"
                     "Content-Type: audio/wav\r\n\r\n", CONFIG_MUSE_OPENAI_TRANSCRIPTION_MODEL);
    if (n < 0 || (size_t)n >= sizeof(head)) {
        strlcpy(why, "TRANSCRIPTION MODEL NAME TOO LONG", cap);
        return NULL;
    }
    uint8_t wav[44];
    muse_openai_wav_header(wav, (uint32_t)job->frames);
    const part_t parts[] = {
        { head, (size_t)n }, { wav, sizeof(wav) },
        { job->pcm, job->frames * sizeof(int16_t) }, { tail, sizeof(tail) - 1 },
    };
    char *response = heap_caps_malloc(JSON_CAP, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!response) {
        strlcpy(why, "NO MEMORY FOR TRANSCRIPTION", cap);
        return NULL;
    }
    bool ok = request(job, "audio/transcriptions", HTTP_METHOD_POST,
                      "multipart/form-data; boundary=muse-openai-voice", parts, 4,
                      response, JSON_CAP, false, false, why, cap);
    cJSON *root = ok ? cJSON_Parse(response) : NULL;
    cJSON *text = cJSON_GetObjectItemCaseSensitive(root, "text");
    char *result = NULL;
    if (ok) {
        if (cJSON_IsString(text) && text->valuestring[0] && strlen(text->valuestring) < TEXT_CAP) {
            result = strdup(text->valuestring);
            if (!result) strlcpy(why, "NO MEMORY FOR TRANSCRIPT", cap);
        } else {
            strlcpy(why, "NO VALID SPEECH TRANSCRIPT", cap);
        }
    }
    cJSON_Delete(root);
    free(response);
    return result;
}

static bool add_message(cJSON *messages, const char *role, const char *text)
{
    cJSON *message = cJSON_CreateObject();
    if (!message || !cJSON_AddStringToObject(message, "role", role)
        || !cJSON_AddStringToObject(message, "content", text)
        || !cJSON_AddItemToArray(messages, message)) {
        cJSON_Delete(message);
        return false;
    }
    return true;
}

#if CONFIG_MUSE_OPENCLAW
static int local_timer(const char *text, char *reply, size_t cap)
{
    if (strlen(text) >= 128) return 0;
    char lower[128];
    size_t length = strlen(text);
    for (size_t i = 0; i <= length; i++) lower[i] = (char)tolower((unsigned char)text[i]);
    while (length && (lower[length - 1] == '.' || lower[length - 1] == '?' || lower[length - 1] == ' ')) {
        lower[--length] = '\0';
    }
    char quantity[16], unit[16];
    int used = 0;
    const char *const patterns[] = {
        "set a timer for %15s %15s %n", "start a timer for %15s %15s %n",
        "set timer for %15s %15s %n", "start timer for %15s %15s %n"
    };
    bool matched = false;
    for (size_t i = 0; i < sizeof(patterns) / sizeof(patterns[0]); i++) {
        used = 0;
        if (sscanf(lower, patterns[i], quantity, unit, &used) == 2 && used > 0 && !lower[used]) {
            matched = true;
            break;
        }
    }
    if (!matched) return 0;
    unsigned long amount = 0;
    if (strspn(quantity, "0123456789") == strlen(quantity)) amount = strtoul(quantity, NULL, 10);
    else {
        static const char *const words[] = { "one", "two", "three", "four", "five", "six", "seven",
                                            "eight", "nine", "ten", "fifteen", "twenty", "thirty", "sixty" };
        static const unsigned values[] = { 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20, 30, 60 };
        for (size_t i = 0; i < sizeof(values) / sizeof(values[0]); i++) {
            if (!strcmp(quantity, words[i])) amount = values[i];
        }
    }
    unsigned multiplier = (!strcmp(unit, "seconds") || !strcmp(unit, "second")) ? 1 :
        (!strcmp(unit, "minutes") || !strcmp(unit, "minute")) ? 60 :
        (!strcmp(unit, "hours") || !strcmp(unit, "hour")) ? 3600 : 0;
    if (!amount || !multiplier || amount > 7 * 86400 / multiplier) return -1;
    unsigned seconds = (unsigned)amount * multiplier;
    if (muse_timer_start(seconds)) snprintf(reply, cap, "Local timer set for %lu %s.", amount, unit);
    else if (muse_timer_pending() == seconds) strlcpy(reply,
        "A timer is already active. Open Companion to confirm replacing it.", cap);
    else return -1;
    return 1;
}

static bool companion_result(const job_t *job, cJSON *body, char *why, size_t cap)
{
    char *response = json_request(job, "companion", body, false, true, why, cap);
    if (!response) return false;
    cJSON *root = cJSON_Parse(response);
    cJSON *request_action = cJSON_GetObjectItemCaseSensitive(body, "action");
    bool preparing = cJSON_IsString(request_action) && !strcmp(request_action->valuestring, "reply_prepare");
    cJSON *draft_result = cJSON_GetObjectItemCaseSensitive(root, "draft");
    cJSON *draft_id = cJSON_GetObjectItemCaseSensitive(draft_result, "id");
    cJSON *draft_state = cJSON_GetObjectItemCaseSensitive(draft_result, "state");
    cJSON *draft_text = cJSON_GetObjectItemCaseSensitive(draft_result, "text");
    cJSON *recipient = cJSON_GetObjectItemCaseSensitive(draft_result, "recipient");
    bool valid_draft = cJSON_IsString(draft_id) && strlen(draft_id->valuestring) == 32
        && cJSON_IsString(draft_state) && !strcmp(draft_state->valuestring, "unconfirmed")
        && cJSON_IsString(draft_text) && draft_text->valuestring[0]
        && strlen(draft_text->valuestring) < TEXT_CAP
        && cJSON_IsString(recipient) && recipient->valuestring[0]
        && strlen(recipient->valuestring) < 1024;
    if (!cJSON_IsObject(root) || cJSON_GetObjectItemCaseSensitive(root, "error")
        || (preparing && !valid_draft)) {
        cJSON_Delete(root);
        free(response);
        strlcpy(why, "COMPANION RESPONSE INVALID", cap);
        return false;
    }
    xSemaphoreTake(s_data, portMAX_DELAY);
    cJSON *merged = s_companion_json[0] ? cJSON_Parse(s_companion_json) : cJSON_CreateObject();
    bool ok = cJSON_IsObject(merged) && current(job->generation);
    for (cJSON *field = root->child; ok && field; field = field->next) {
        cJSON *copy = cJSON_Duplicate(field, true);
        cJSON_DeleteItemFromObjectCaseSensitive(merged, field->string);
        if (!copy || !cJSON_AddItemToObject(merged, field->string, copy)) {
            cJSON_Delete(copy);
            ok = false;
        }
    }
    char *encoded = ok ? cJSON_PrintUnformatted(merged) : NULL;
    if (!encoded || strlen(encoded) >= JSON_CAP) ok = false;
    if (ok) {
        strlcpy(s_companion_json, encoded, JSON_CAP);
        s_companion_version++;
        cJSON *action = cJSON_GetObjectItemCaseSensitive(body, "action");
        cJSON *notification = cJSON_GetObjectItemCaseSensitive(body, "notification");
        cJSON *snoozed = cJSON_GetObjectItemCaseSensitive(body, "id");
        const char *ack = cJSON_IsString(action) && !strcmp(action->valuestring, "reply_prepare")
            && cJSON_IsString(notification) ? notification->valuestring :
            cJSON_IsString(action) && !strcmp(action->valuestring, "snooze")
            && cJSON_IsString(snoozed) ? snoozed->valuestring : NULL;
        if (ack && !strcmp(s_notification.id, ack)) {
            strlcpy(s_notification_ack, ack, sizeof(s_notification_ack));
            memset(&s_notification, 0, sizeof(s_notification));
        }
        cJSON *draft = cJSON_GetObjectItemCaseSensitive(merged, "draft");
        cJSON *state = cJSON_GetObjectItemCaseSensitive(draft, "state");
        cJSON *id = cJSON_GetObjectItemCaseSensitive(draft, "id");
        cJSON *text = cJSON_GetObjectItemCaseSensitive(draft, "preview");
        memset(&s_draft_notice, 0, sizeof(s_draft_notice));
        if (cJSON_IsString(state) && !strcmp(state->valuestring, "unconfirmed")
            && cJSON_IsString(id) && strlen(id->valuestring) == 32
            && cJSON_IsString(text) && strlen(text->valuestring) < sizeof(s_draft_notice.preview)) {
            strlcpy(s_draft_notice.id, id->valuestring, sizeof(s_draft_notice.id));
            strlcpy(s_draft_notice.kind, "reply", sizeof(s_draft_notice.kind));
            strlcpy(s_draft_notice.sender, "Reply draft - NOT SENT", sizeof(s_draft_notice.sender));
            strlcpy(s_draft_notice.preview, text->valuestring, sizeof(s_draft_notice.preview));
        }
    }
    xSemaphoreGive(s_data);
    free(encoded);
    cJSON_Delete(merged);
    cJSON_Delete(root);
    free(response);
    if (!ok) strlcpy(why, "COMPANION STATE TOO LARGE OR INVALID", cap);
    return ok;
}

bool muse_openai_companion_snapshot(char *out, size_t cap, unsigned *version)
{
    if (!s_data || !s_companion_json) return false;
    xSemaphoreTake(s_data, portMAX_DELAY);
    bool changed = *version != s_companion_version && s_companion_json[0];
    if (changed && strlen(s_companion_json) < cap) {
        strlcpy(out, s_companion_json, cap);
        *version = s_companion_version;
    } else {
        changed = false;
    }
    xSemaphoreGive(s_data);
    return changed;
}

bool muse_openai_companion_command(const char *json)
{
    if (!json || strlen(json) >= JSON_CAP || !openclaw_chat() || !s_jobs) {
        ESP_LOGW(TAG, "invalid or unavailable companion command");
        muse_state_set_caption("COMPANION COMMAND INVALID OR NOT CONFIGURED");
        return false;
    }
    bool expected = false;
    if (!atomic_compare_exchange_strong(&s_busy, &expected, true)) return false;
    job_t job = { .kind = JOB_COMPANION, .generation = atomic_load(&s_generation),
                  .text = strdup(json) };
    if (!job.text || xQueueSend(s_jobs, &job, 0) != pdTRUE) {
        free(job.text);
        atomic_store(&s_busy, false);
        ESP_LOGE(TAG, "companion command could not be queued");
        muse_state_set_caption("COMPANION COMMAND NOT QUEUED");
        return false;
    }
    return true;
}

bool muse_openai_companion_busy(void)
{
    return atomic_load(&s_busy);
}

bool muse_openai_reply_begin(const char *notification_id)
{
    if (!s_data || !notification_id || strlen(notification_id) != 32 ||
        strspn(notification_id, "0123456789abcdef") != 32 || atomic_load(&s_busy)) {
        ESP_LOGW(TAG, "reply context is invalid or busy");
        muse_state_set_caption("REPLY NOT READY - TRY AGAIN");
        return false;
    }
    xSemaphoreTake(s_data, portMAX_DELAY);
    bool valid = !strcmp(s_notification.id, notification_id)
        && !strcmp(s_notification.kind, "imessage");
    if (valid) strlcpy(s_reply_arm, notification_id, sizeof(s_reply_arm));
    xSemaphoreGive(s_data);
    if (valid) muse_state_set_caption("HOLD TALK TO DICTATE A REPLY; REVIEW BEFORE SENDING");
    else muse_state_set_caption("MESSAGE PREVIEW NO LONGER AVAILABLE");
    return valid;
}

bool muse_openai_job_active(void)
{
    if (!s_data) return false;
    xSemaphoreTake(s_data, portMAX_DELAY);
    bool active = s_mac_job[0] != '\0';
    xSemaphoreGive(s_data);
    return active;
}

static char *mac_complete(const job_t *job, cJSON *messages, char *why, size_t cap)
{
    char url[MUSE_OPENCLAW_URL_MAX + 1];
    char *token = malloc(MUSE_TOKEN_MAX + 1);
    if (!token) {
        strlcpy(why, "NO MEMORY FOR MAC JOB CREDENTIALS", cap);
        return NULL;
    }
    muse_settings_openclaw(url, token);
    job_t scoped = *job;
    scoped.bridge_url = url;
    scoped.bridge_token = token;
    uint8_t random[16];
    char id[33];
    esp_fill_random(random, sizeof(random));
    for (size_t i = 0; i < sizeof(random); i++) snprintf(id + i * 2, 3, "%02x", random[i]);
    cJSON *body = cJSON_CreateObject();
    cJSON *copy = cJSON_Duplicate(messages, true);
    if (!body || !copy || !cJSON_AddStringToObject(body, "id", id)
        || !cJSON_AddItemToObject(body, "messages", copy)) {
        cJSON_Delete(copy);
        cJSON_Delete(body);
        mbedtls_platform_zeroize(token, MUSE_TOKEN_MAX + 1);
        free(token);
        strlcpy(why, "NO MEMORY FOR MAC JOB", cap);
        return NULL;
    }
    xSemaphoreTake(s_data, portMAX_DELAY);
    strlcpy(s_mac_job, id, sizeof(s_mac_job));
    xSemaphoreGive(s_data);
    char *response = json_request(&scoped, "jobs/start", body, false, true, why, cap);
    cJSON_DeleteItemFromObjectCaseSensitive(body, "messages");
    bool stopping = false, announced = false, uncertain = false;
    char *reply = NULL;
    if (!response && current(job->generation) &&
        (strstr(why, "HTTP ") || strstr(why, "TOKEN INVALID"))) goto finished;
    for (;;) {
        if (!current(job->generation)) stopping = true;
        if (response) {
            cJSON *root = cJSON_Parse(response);
            cJSON *status = cJSON_GetObjectItemCaseSensitive(root, "status");
            cJSON *returned_id = cJSON_GetObjectItemCaseSensitive(root, "id");
            bool valid = cJSON_IsString(status) && cJSON_IsString(returned_id)
                && !strcmp(returned_id->valuestring, id);
            if (!valid) {
                uncertain = true;
                strlcpy(why, "OPENCLAW JOB RESPONSE INVALID", cap);
                cJSON_Delete(root);
                free(response);
                break;
            }
            const char *state = status->valuestring;
            if (!strcmp(state, "completed") || !strcmp(state, "cancelled")
                || !strcmp(state, "failed") || !strcmp(state, "interrupted")) {
                cJSON *text = cJSON_GetObjectItemCaseSensitive(root, "reply");
                cJSON *detail = cJSON_GetObjectItemCaseSensitive(root, "detail");
                if (!stopping && !strcmp(state, "completed") && cJSON_IsString(text)
                    && text->valuestring[0] && strlen(text->valuestring) < TEXT_CAP) {
                    reply = strdup(text->valuestring);
                    if (!reply) strlcpy(why, "NO MEMORY FOR MAC REPLY", cap);
                } else {
                    strlcpy(why, cJSON_IsString(detail) ? detail->valuestring :
                            "OPENCLAW JOB ENDED WITHOUT A REPLY", cap);
                    if (stopping) report(!strcmp(state, "cancelled") ? MUSE_HATCH_REACHABLE :
                                         MUSE_HATCH_UNREACHABLE, why);
                }
                cJSON_Delete(root);
                free(response);
                break;
            }
            if (strcmp(state, "starting") && strcmp(state, "running") && strcmp(state, "stopping")) {
                uncertain = true;
                strlcpy(why, "OPENCLAW JOB STATE INVALID", cap);
                cJSON_Delete(root);
                free(response);
                break;
            }
            cJSON *elapsed = cJSON_GetObjectItemCaseSensitive(root, "elapsed_seconds");
            char detail[48];
            snprintf(detail, sizeof(detail), "%s %.0fs", stopping ? "Stopping Mac job" : "Mac working",
                     cJSON_IsNumber(elapsed) ? elapsed->valuedouble : 0);
            report(MUSE_HATCH_TESTING, detail);
            if (!stopping && current(job->generation) && job->kind == JOB_VOICE) {
                muse_state_set_caption("%s", detail);
            }
            cJSON_Delete(root);
            free(response);
        } else {
            report(MUSE_HATCH_TESTING, stopping ? "Mac offline; stop pending" : "Mac offline; checking job status");
        }
        if (stopping && !announced) {
            job_t notice = { .kind = JOB_VOICE, .generation = atomic_load(&s_generation) };
            emit(&notice, MUSE_HATCH_EV_ERROR, "STOP REQUESTED - WAITING FOR MAC");
            announced = true;
        }
        vTaskDelay(pdMS_TO_TICKS(1000));
        job_t control = scoped;
        control.generation = atomic_load(&s_generation);
        response = json_request(&control, stopping ? "jobs/cancel" : "jobs/status",
                                body, false, true, why, cap);
        if (!response && (strstr(why, "HTTP ") || strstr(why, "TOKEN INVALID"))) {
            uncertain = true;
            break;
        }
    }
finished:
    if (uncertain) {
        strlcpy(why, "MAC JOB STATUS UNKNOWN - STOP UNCONFIRMED; CHECK DASHBOARD", cap);
        report(MUSE_HATCH_UNREACHABLE, why);
    }
    cJSON_Delete(body);
    mbedtls_platform_zeroize(token, MUSE_TOKEN_MAX + 1);
    free(token);
    xSemaphoreTake(s_data, portMAX_DELAY);
    s_mac_job[0] = '\0';
    xSemaphoreGive(s_data);
    return reply;
}
#endif

static char *complete(const job_t *job, const char *text, cJSON *history, char *why, size_t cap)
{
    bool openclaw = openclaw_chat();
    cJSON *body = cJSON_CreateObject();
    cJSON *messages = body ? cJSON_AddArrayToObject(body, "messages") : NULL;
    bool ok = messages && cJSON_AddStringToObject(body, "model", openclaw ? "openclaw:esp32" : CONFIG_MUSE_OPENAI_CHAT_MODEL)
              && cJSON_AddNumberToObject(body, "max_completion_tokens", 240)
              && add_message(messages, "system",
                             openclaw ?
                             "You are a friendly voice companion connected to OpenClaw. "
                             "Use available tools when asked to perform an action. Only claim an action "
                             "succeeded when its tool result confirms success. "
#if CONFIG_MUSE_OPENCLAW_NORMAL_CHROME
                             "For browser actions, read the normal-chrome skill and use its local helper "
                             "through exec to control normal signed-in Chrome. All browser commands must "
                             "use python3 \"$HOME/.openclaw/muse-esp32/normal_chrome.py\" with JSON tool "
                             "parameters. Use new_page with url to open a tab, and evaluate_script with "
                             "the returned pageId and function to inspect or change its DOM. The function "
                             "must be callable JavaScript such as () => document.title, not a bare "
                             "assignment. Read skills/normal-chrome/SKILL.md with the read tool before "
                             "browser actions, including its JSON-escaped string examples. "
                             "TOOL and OPEN_TAB are not command names. Do not use AppleScript, "
                             "osascript, open, or other browser "
                             "launchers for browser actions. If the helper fails, report the error "
                             "instead of trying another method. Never use the isolated "
                             "openclaw browser profile or fall back to another browser. "
#else
                             "For browser actions, use the openclaw profile. "
#endif
                             "Before sending a message, confirm the exact "
                             "recipient and text. Never infer a reply recipient from a display preview. "
                             "For iMessage, read the imsg skill and use imsg send with named --to and --text "
                             "arguments, --service imessage --no-sms-fallback --json. Never use positional "
                             "send arguments or automatically retry a failed send. "
                             "Reply in plain text without emoji, "
                             "in at most three short sentences." :
                             "You are a friendly standalone voice companion. Reply in plain text, "
                             "in at most three short sentences. Do not claim to control devices.");
    for (int i = 0; ok && i < cJSON_GetArraySize(history); i++) {
        cJSON *item = cJSON_Duplicate(cJSON_GetArrayItem(history, i), true);
        if (!item || !cJSON_AddItemToArray(messages, item)) {
            cJSON_Delete(item);
            ok = false;
        }
    }
    ok = ok && add_message(messages, "user", text);
#if CONFIG_MUSE_OPENCLAW
    if (ok && openclaw) {
        char *reply = mac_complete(job, messages, why, cap);
        cJSON_Delete(body);
        return reply;
    }
#endif
    char *response = ok ? json_request(job, "chat/completions", body, false, openclaw, why, cap) : NULL;
    cJSON_Delete(body);
    if (!ok) strlcpy(why, "NO MEMORY FOR CHAT", cap);
    if (!response) return NULL;
    cJSON *root = cJSON_Parse(response);
    cJSON *choice = cJSON_GetArrayItem(cJSON_GetObjectItemCaseSensitive(root, "choices"), 0);
    cJSON *message = cJSON_GetObjectItemCaseSensitive(choice, "message");
    cJSON *content = cJSON_GetObjectItemCaseSensitive(message, "content");
    cJSON *finish = cJSON_GetObjectItemCaseSensitive(choice, "finish_reason");
    char *result = NULL;
    if (!cJSON_IsString(content) || !content->valuestring[0] || strlen(content->valuestring) >= TEXT_CAP) {
        request_error(openclaw, "REPLY INVALID OR TOO LONG", why, cap);
    } else if (cJSON_IsString(finish) && !strcmp(finish->valuestring, "length")) {
        request_error(openclaw, "REPLY HIT TOKEN LIMIT", why, cap);
    } else {
        result = strdup(content->valuestring);
        if (!result) strlcpy(why, "NO MEMORY FOR REPLY", cap);
    }
    cJSON_Delete(root);
    free(response);
    return result;
}

static bool speak(const job_t *job, const char *text, char *why, size_t cap)
{
    cJSON *body = cJSON_CreateObject();
    bool ok = body && cJSON_AddStringToObject(body, "model", CONFIG_MUSE_OPENAI_SPEECH_MODEL)
              && cJSON_AddStringToObject(body, "voice", CONFIG_MUSE_OPENAI_VOICE)
              && cJSON_AddStringToObject(body, "input", text)
              && cJSON_AddStringToObject(body, "response_format", "pcm");
    char *response = ok ? json_request(job, "audio/speech", body, true, false, why, cap) : NULL;
    cJSON_Delete(body);
    if (!ok) strlcpy(why, "NO MEMORY FOR SPEECH", cap);
    ok = response != NULL;
    free(response);
    return ok;
}

static bool show_muted_reply(const job_t *job, const char *text)
{
    static const int16_t silence[MUSE_AUDIO_CHUNK];
    size_t frames = (strlen(text) + 28) * 16000 / 14;
    while (frames && current(job->generation)) {
        size_t n = frames < MUSE_AUDIO_CHUNK ? frames : MUSE_AUDIO_CHUNK;
        if (!push_audio(job->generation, silence, n)) return false;
        frames -= n;
    }
    return current(job->generation);
}

#if CONFIG_MUSE_OPENCLAW
bool muse_openai_notification(muse_notification_t *out)
{
    if (!s_data) return false;
    unsigned replacement = muse_timer_pending();
    muse_timer_state_t timer_state = muse_timer_status(NULL);
    if (replacement && timer_state != MUSE_TIMER_RINGING) {
        memset(out, 0, sizeof(*out));
        strlcpy(out->id, "00000000000000000000000000000002", sizeof(out->id));
        strlcpy(out->kind, "timer_replace", sizeof(out->kind));
        strlcpy(out->sender, "Timer already active", sizeof(out->sender));
        snprintf(out->preview, sizeof(out->preview), "Review replacement with %u seconds. Existing timer is unchanged.",
                 replacement);
        return true;
    }
    if (timer_state == MUSE_TIMER_RINGING) {
        memset(out, 0, sizeof(*out));
        strlcpy(out->id, "00000000000000000000000000000001", sizeof(out->id));
        strlcpy(out->kind, "timer", sizeof(out->kind));
        strlcpy(out->sender, "Local timer", sizeof(out->sender));
        strlcpy(out->preview, "Time is up. Dismiss or snooze for 5 minutes.", sizeof(out->preview));
        return true;
    }
    xSemaphoreTake(s_data, portMAX_DELAY);
    bool draft = s_draft_notice.id[0] != '\0' &&
        (!s_notification.id[0] || !strcmp(s_notification.kind, "imessage"));
    bool available = (draft || s_notification.id[0]) && openclaw_chat();
    if (available) {
        *out = draft ? s_draft_notice : s_notification;
        if (!draft && s_reply_arm[0]) strlcpy(out->kind, "reply_wait", sizeof(out->kind));
    }
    xSemaphoreGive(s_data);
    return available;
}

void muse_openai_notification_dismiss(void)
{
    if (muse_timer_status(NULL) == MUSE_TIMER_RINGING) {
        muse_timer_dismiss();
        return;
    }
    if (!s_data) return;
    xSemaphoreTake(s_data, portMAX_DELAY);
    if (s_notification.id[0]) {
        s_reply_arm[0] = '\0';
        strlcpy(s_notification_ack, s_notification.id, sizeof(s_notification_ack));
        memset(&s_notification, 0, sizeof(s_notification));
    }
    xSemaphoreGive(s_data);
}

static void poll_notifications(void)
{
    if (!openclaw_chat() || !muse_settings_openclaw_token_set() || !muse_wifi_connected()
        || muse_state_asleep() || muse_state_mode(NULL) != MUSE_MODE_IDLE
        || atomic_load(&s_busy) || time(NULL) < 1700000000) return;
    char acknowledgment[33];
    xSemaphoreTake(s_data, portMAX_DELAY);
    bool pending = s_notification.id[0] &&
        !(s_draft_notice.id[0] && !strcmp(s_notification.kind, "imessage"));
    strlcpy(acknowledgment, s_notification_ack, sizeof(acknowledgment));
    xSemaphoreGive(s_data);
    if (pending) return;
    job_t job = { .kind = JOB_NOTIFICATION, .generation = atomic_load(&s_generation) };
    cJSON *body = cJSON_CreateObject();
    char why[96] = "OPENCLAW NOTIFICATION FAILED";
    char *response = body && cJSON_AddStringToObject(body, "ack", acknowledgment) ?
        json_request(&job, "notifications", body, false, true, why, sizeof(why)) : NULL;
    cJSON_Delete(body);
    if (!response) {
        if (current(job.generation)) ESP_LOGW(TAG, "incoming iMessages: %s", why);
        return;
    }
    cJSON *root = cJSON_Parse(response);
    cJSON *notification = cJSON_GetObjectItemCaseSensitive(root, "notification");
    muse_notification_t next = {0};
    bool valid = cJSON_IsNull(notification);
    if (cJSON_IsObject(notification)) {
        cJSON *id = cJSON_GetObjectItemCaseSensitive(notification, "id");
        cJSON *sender = cJSON_GetObjectItemCaseSensitive(notification, "sender");
        cJSON *text = cJSON_GetObjectItemCaseSensitive(notification, "preview");
        cJSON *kind = cJSON_GetObjectItemCaseSensitive(notification, "kind");
        cJSON *full = cJSON_GetObjectItemCaseSensitive(notification, "body");
        valid = cJSON_IsString(id) && strlen(id->valuestring) == 32
            && strspn(id->valuestring, "0123456789abcdef") == 32
            && cJSON_IsString(sender) && sender->valuestring[0]
            && strlen(sender->valuestring) < sizeof(next.sender)
            && cJSON_IsString(text) && text->valuestring[0]
            && strlen(text->valuestring) < sizeof(next.preview)
            && (!kind || (cJSON_IsString(kind) &&
                (!strcmp(kind->valuestring, "imessage") || !strcmp(kind->valuestring, "reminder")
                 || !strcmp(kind->valuestring, "briefing") || !strcmp(kind->valuestring, "job"))))
            && (!full || (cJSON_IsString(full) && strlen(full->valuestring) < sizeof(next.body)));
        if (valid) {
            strlcpy(next.id, id->valuestring, sizeof(next.id));
            strlcpy(next.sender, sender->valuestring, sizeof(next.sender));
            strlcpy(next.preview, text->valuestring, sizeof(next.preview));
            strlcpy(next.kind, kind ? kind->valuestring : "imessage", sizeof(next.kind));
            strlcpy(next.body, full ? full->valuestring : text->valuestring, sizeof(next.body));
        }
    }
    cJSON_Delete(root);
    free(response);
    if (!valid) {
        ESP_LOGW(TAG, "incoming iMessages: invalid notification response");
        return;
    }
    xSemaphoreTake(s_data, portMAX_DELAY);
    if (current(job.generation)) {
        s_notification = next;
        s_notification_ack[0] = '\0';
        if (next.id[0]) ESP_LOGI(TAG, "incoming iMessage queued for display");
    }
    xSemaphoreGive(s_data);
}
#endif

static void worker(void *arg)
{
    (void)arg;
    cJSON *history = cJSON_CreateArray();
    job_t job;
#if CONFIG_MUSE_OPENCLAW
    const TickType_t wait = pdMS_TO_TICKS(5000);
#else
    const TickType_t wait = portMAX_DELAY;
#endif
    for (;;) {
        if (xQueueReceive(s_jobs, &job, wait) != pdTRUE) {
#if CONFIG_MUSE_OPENCLAW
            poll_notifications();
#endif
            continue;
        }
        char why[96] = "OPENAI REQUEST FAILED";
        char *text = NULL, *reply = NULL;
        if (atomic_exchange(&s_clear_history, false)) {
            cJSON_Delete(history);
            history = cJSON_CreateArray();
        }
        if (!current(job.generation)) goto finished;
        if (job.kind == JOB_TEXT) {
            muse_hatch_console("sent", NULL, "\"bytes\":%u", (unsigned)strlen(job.text));
        }
        report(MUSE_HATCH_TESTING, openclaw_chat() && job.kind != JOB_TEST ?
               "OpenClaw + OpenAI voice..." : "Contacting OpenAI...");
        if (!muse_wifi_connected()) {
            strlcpy(why, "NO WI-FI", sizeof(why));
            goto failed;
        }
        if (time(NULL) < 1700000000) {
            ESP_LOGI(TAG, "waiting for network time for TLS");
            for (int i = 0; i < 100 && time(NULL) < 1700000000 && current(job.generation); i++) {
                vTaskDelay(pdMS_TO_TICKS(100));
            }
            if (time(NULL) < 1700000000) {
                strlcpy(why, "NETWORK TIME NOT READY - RETRY", sizeof(why));
                goto failed;
            }
        }
        if (!current(job.generation)) goto finished;
#if CONFIG_MUSE_OPENCLAW
        if (job.kind == JOB_COMPANION) {
            cJSON *body = cJSON_Parse(job.text);
            bool ok = cJSON_IsObject(body) && companion_result(&job, body, why, sizeof(why));
            cJSON_Delete(body);
            if (!ok) goto failed;
            report(MUSE_HATCH_REACHABLE, "Companion updated");
            goto finished;
        }
#endif
        if (job.kind == JOB_TEST) {
            char path[128];
            char *response = heap_caps_malloc(JSON_CAP, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
            if (!response) {
                strlcpy(why, "NO MEMORY FOR API TEST", sizeof(why));
                goto failed;
            }
            snprintf(path, sizeof(path), "models/%s", CONFIG_MUSE_OPENAI_CHAT_MODEL);
            bool accepted = request(&job, path, HTTP_METHOD_GET, NULL, NULL, 0, response,
                                    JSON_CAP, false, false, why, sizeof(why));
            free(response);
            if (!accepted) goto failed;
            report(MUSE_HATCH_REACHABLE, "API key accepted");
            ESP_LOGI(TAG, "OpenAI API key test passed");
            goto finished;
        }
        if (!history) {
            strlcpy(why, "NO MEMORY FOR CONVERSATION", sizeof(why));
            goto failed;
        }
        text = job.kind == JOB_VOICE ? transcribe(&job, why, sizeof(why)) : strdup(job.text);
        if (!text) goto failed;
        emit(&job, MUSE_HATCH_EV_HEARD, text);
#if CONFIG_MUSE_OPENCLAW
        if (job.reply_target[0]) {
            cJSON *body = cJSON_CreateObject();
            bool ok = body && cJSON_AddStringToObject(body, "action", "reply_prepare")
                && cJSON_AddStringToObject(body, "notification", job.reply_target)
                && cJSON_AddStringToObject(body, "text", text)
                && companion_result(&job, body, why, sizeof(why));
            cJSON_Delete(body);
            if (!ok) goto failed;
            xSemaphoreTake(s_data, portMAX_DELAY);
            strlcpy(s_reply, "REPLY NOT SENT. TAP THE DRAFT TO REVIEW AND CONFIRM.", sizeof(s_reply));
            xSemaphoreGive(s_data);
            emit(&job, MUSE_HATCH_EV_REPLY, "REPLY NOT SENT - REVIEW DRAFT");
            emit(&job, MUSE_HATCH_EV_DONE, "");
            report(MUSE_HATCH_REACHABLE, "Reply draft ready; not sent");
            goto finished;
        }
#endif
#if CONFIG_MUSE_OPENCLAW
        char timer_reply[128];
        int timer = openclaw_chat() ? local_timer(text, timer_reply, sizeof(timer_reply)) : 0;
        if (timer < 0) {
            strlcpy(why, "TIMER DURATION INVALID OR STORAGE FAILED", sizeof(why));
            goto failed;
        }
        reply = timer ? strdup(timer_reply) : complete(&job, text, history, why, sizeof(why));
#else
        reply = complete(&job, text, history, why, sizeof(why));
#endif
        if (!reply) goto failed;
        if (!current(job.generation)) goto finished;
        if (!add_message(history, "user", text) || !add_message(history, "assistant", reply)) {
            atomic_store(&s_clear_history, true);
            strlcpy(why, "NO MEMORY FOR CONVERSATION", sizeof(why));
            goto failed;
        }
        while (cJSON_GetArraySize(history) > HISTORY_PAIRS * 2) {
            cJSON_DeleteItemFromArray(history, 0);
            cJSON_DeleteItemFromArray(history, 0);
        }
        xSemaphoreTake(s_data, portMAX_DELAY);
        if (current(job.generation)) strlcpy(s_reply, reply, sizeof(s_reply));
        xSemaphoreGive(s_data);
        emit(&job, MUSE_HATCH_EV_REPLY, reply);
        if (job.kind == JOB_VOICE) {
            bool speech_on = muse_settings_speaker_on();
            if (speech_on && !speak(&job, reply, why, sizeof(why))) goto failed;
            if (!speech_on && !show_muted_reply(&job, reply)) {
                strlcpy(why, "CAPTION PLAYBACK STALLED", sizeof(why));
                goto failed;
            }
        }
        if (current(job.generation)) report(MUSE_HATCH_REACHABLE, "Ready to talk");
        emit(&job, MUSE_HATCH_EV_DONE, "");
        goto finished;
failed:
        if (current(job.generation)) {
            ESP_LOGW(TAG, "%s", why);
            report(MUSE_HATCH_UNREACHABLE, why);
            if (job.kind == JOB_COMPANION) muse_state_set_caption("%s", why);
            else if (job.kind != JOB_TEST) emit(&job, MUSE_HATCH_EV_ERROR, why);
        }
finished:
        free(text);
        free(reply);
        free(job.pcm);
        free(job.text);
        atomic_store(&s_busy, false);
    }
}

void muse_hatch_start(void)
{
    s_data = xSemaphoreCreateMutex();
    s_jobs = xQueueCreate(1, sizeof(job_t));
    s_events = xQueueCreate(8, sizeof(event_t));
    s_audio = heap_caps_malloc(AUDIO_FRAMES * sizeof(int16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
#if CONFIG_MUSE_OPENCLAW
    s_companion_json = heap_caps_malloc(JSON_CAP, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    ESP_ERROR_CHECK(s_companion_json ? ESP_OK : ESP_ERR_NO_MEM);
    strlcpy(s_companion_json, "{}", JSON_CAP);
    muse_companion_start();
#endif
    ESP_ERROR_CHECK(s_data && s_jobs && s_events && s_audio ? ESP_OK : ESP_ERR_NO_MEM);
    esp_sntp_config_t time_config = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
    ESP_ERROR_CHECK(esp_netif_sntp_init(&time_config));
    ESP_ERROR_CHECK(xTaskCreate(worker, "openai", 12288, NULL, 4, NULL) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_LOGI(TAG, "standalone provider ready; key %s", muse_settings_openai_key_len() ? "set" : "not set");
}

void muse_hatch_status(muse_hatch_status_t *out)
{
    portENTER_CRITICAL(&s_status_lock);
    out->state = s_status;
    strlcpy(out->detail, s_detail, sizeof(out->detail));
    portEXIT_CRITICAL(&s_status_lock);
    if (!muse_settings_openai_key_len()) {
        out->state = MUSE_HATCH_NOT_SET;
#if CONFIG_MUSE_OPENCLAW
        strlcpy(out->detail, "Set voice key in Settings > OpenClaw", sizeof(out->detail));
#else
        strlcpy(out->detail, "Set API key in Settings > OpenAI", sizeof(out->detail));
#endif
    } else if (!muse_wifi_connected()) {
        out->state = MUSE_HATCH_OFFLINE;
        strlcpy(out->detail, "Set up Wi-Fi in Settings", sizeof(out->detail));
    } else if (openclaw_chat() && !muse_settings_openclaw_token_set()) {
        out->state = MUSE_HATCH_NOT_SET;
        strlcpy(out->detail, "Set OpenClaw bridge token over USB", sizeof(out->detail));
    }
}

const char *muse_hatch_state_name(muse_hatch_state_t state)
{
    switch (state) {
    case MUSE_HATCH_NOT_SET: return "API key not set";
    case MUSE_HATCH_OFFLINE: return "Offline";
    case MUSE_HATCH_UNTESTED: return "Ready";
    case MUSE_HATCH_TESTING: return "Working";
    case MUSE_HATCH_REACHABLE: return "Connected";
    case MUSE_HATCH_UNREACHABLE: return "Request failed";
    }
    return "Unknown";
}

bool muse_hatch_ready(void)
{
    return s_jobs && muse_settings_openai_key_len() && muse_wifi_connected() &&
           (!openclaw_chat() || muse_settings_openclaw_token_set()) && !atomic_load(&s_busy);
}

static bool begin_job(void)
{
    bool expected = false;
    if (!s_jobs || !atomic_compare_exchange_strong(&s_busy, &expected, true)) {
        ESP_LOGW(TAG, "OpenAI request still busy");
        return false;
    }
    atomic_fetch_add(&s_generation, 1);
    xSemaphoreTake(s_data, portMAX_DELAY);
    s_audio_head = s_audio_n = 0;
    s_reply[0] = '\0';
    xSemaphoreGive(s_data);
    xQueueReset(s_events);
    return true;
}

void muse_hatch_test(void)
{
    if (!muse_hatch_ready()) {
        ESP_LOGW(TAG, "API test requires Wi-Fi, API key and an idle request");
        report(MUSE_HATCH_UNREACHABLE, "Set Wi-Fi/key, then retry when idle");
        return;
    }
    if (!begin_job()) return;
    report(MUSE_HATCH_TESTING, "Testing API key...");
    job_t job = { .kind = JOB_TEST, .generation = atomic_load(&s_generation) };
    if (xQueueSend(s_jobs, &job, 0) != pdTRUE) {
        atomic_store(&s_busy, false);
        report(MUSE_HATCH_UNREACHABLE, "API request queue full");
        ESP_LOGE(TAG, "API request queue full");
    }
}

void muse_hatch_turn_begin(void)
{
    if (!begin_job()) return;
    xSemaphoreTake(s_data, portMAX_DELAY);
    free(s_record);
    s_record = heap_caps_malloc(RECORD_FRAMES * sizeof(int16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    s_record_n = 0;
#if CONFIG_MUSE_OPENCLAW
    strlcpy(s_record_reply, s_reply_arm, sizeof(s_record_reply));
    s_reply_arm[0] = '\0';
#endif
    xSemaphoreGive(s_data);
    if (!s_record) {
        job_t job = { .generation = atomic_load(&s_generation) };
        emit(&job, MUSE_HATCH_EV_ERROR, "NO MEMORY FOR RECORDING");
        atomic_store(&s_busy, false);
        ESP_LOGE(TAG, "no memory for recording");
    }
}

size_t muse_hatch_turn_audio_wait(const int16_t *pcm, size_t frames, int wait_ms)
{
    (void)wait_ms;
    xSemaphoreTake(s_data, portMAX_DELAY);
    size_t n = s_record ? RECORD_FRAMES - s_record_n : 0;
    if (n > frames) n = frames;
    if (n) memcpy(s_record + s_record_n, pcm, n * sizeof(int16_t));
    s_record_n += n;
    xSemaphoreGive(s_data);
    return n;
}

void muse_hatch_turn_audio(const int16_t *pcm, size_t frames)
{
    if (muse_hatch_turn_audio_wait(pcm, frames, 0) != frames) {
        job_t job = { .generation = atomic_load(&s_generation) };
        emit(&job, MUSE_HATCH_EV_ERROR, "RECORDING BUFFER FULL");
        ESP_LOGE(TAG, "recording buffer full");
    }
}

void muse_hatch_turn_end(void)
{
    job_t job = { .kind = JOB_VOICE, .generation = atomic_load(&s_generation) };
    xSemaphoreTake(s_data, portMAX_DELAY);
    job.pcm = s_record;
    job.frames = s_record_n;
    s_record = NULL;
    s_record_n = 0;
#if CONFIG_MUSE_OPENCLAW
    strlcpy(job.reply_target, s_record_reply, sizeof(job.reply_target));
    s_record_reply[0] = '\0';
#endif
    xSemaphoreGive(s_data);
    if (!job.pcm || !job.frames || xQueueSend(s_jobs, &job, 0) != pdTRUE) {
        free(job.pcm);
        atomic_store(&s_busy, false);
        emit(&job, MUSE_HATCH_EV_ERROR, "VOICE REQUEST NOT QUEUED");
        ESP_LOGE(TAG, "voice request not queued");
    }
}

void muse_hatch_turn_cancel(void)
{
    atomic_fetch_add(&s_generation, 1);
    if (!s_data) return;
    xSemaphoreTake(s_data, portMAX_DELAY);
    bool recording = s_record != NULL;
    free(s_record);
    s_record = NULL;
    s_record_n = s_audio_head = s_audio_n = 0;
    s_reply[0] = '\0';
#if CONFIG_MUSE_OPENCLAW
    s_reply_arm[0] = s_record_reply[0] = '\0';
#endif
    xSemaphoreGive(s_data);
    if (recording) atomic_store(&s_busy, false);
}

void muse_openai_clear_history(void)
{
    bool active = atomic_load(&s_busy);
    atomic_store(&s_clear_history, true);
    muse_hatch_turn_cancel();
    report(MUSE_HATCH_UNTESTED, "Ready to talk");
    if (active && s_events) {
        job_t job = { .kind = JOB_VOICE, .generation = atomic_load(&s_generation) };
        emit(&job, MUSE_HATCH_EV_ERROR, "CONVERSATION CLEARED");
    }
    ESP_LOGI(TAG, "conversation cleared");
}

void muse_hatch_config_changed(void)
{
    muse_openai_clear_history();
#if CONFIG_MUSE_OPENCLAW
    if (s_data) {
        xSemaphoreTake(s_data, portMAX_DELAY);
        memset(&s_notification, 0, sizeof(s_notification));
        memset(&s_draft_notice, 0, sizeof(s_draft_notice));
        strlcpy(s_companion_json, "{}", JSON_CAP);
        s_companion_version++;
        s_notification_ack[0] = '\0';
        xSemaphoreGive(s_data);
    }
#endif
    report(MUSE_HATCH_UNTESTED, "Ready to talk");
}

void muse_hatch_set_resting(bool resting)
{
    (void)resting;
}

muse_hatch_ev_t muse_hatch_turn_event(char *text, size_t cap)
{
    event_t event;
    while (s_events && xQueueReceive(s_events, &event, 0) == pdTRUE) {
        if (current(event.generation)) {
            strlcpy(text, event.text, cap);
            return event.type;
        }
    }
    return MUSE_HATCH_EV_NONE;
}

bool muse_hatch_turn_caption(size_t played, char *out, size_t cap)
{
    if (!s_data) return false;
    xSemaphoreTake(s_data, portMAX_DELAY);
    size_t len = strlen(s_reply);
    /* Approximate reading pace: PCM playback has no word timestamps. */
    size_t at = played * 14 / 16000;
    if (at >= len && len) at = len - 1;
    bool result = len && muse_hatch_caption_at(s_reply, at, out, cap);
    xSemaphoreGive(s_data);
    return result;
}

size_t muse_hatch_turn_read(int16_t *pcm, size_t frames, int wait_ms)
{
    if (!s_data) return 0;
    int64_t deadline = esp_timer_get_time() + (int64_t)wait_ms * 1000;
    do {
        xSemaphoreTake(s_data, portMAX_DELAY);
        size_t n = frames < s_audio_n ? frames : s_audio_n;
        for (size_t i = 0; i < n; i++) pcm[i] = s_audio[(s_audio_head + i) % AUDIO_FRAMES];
        s_audio_head = (s_audio_head + n) % AUDIO_FRAMES;
        s_audio_n -= n;
        xSemaphoreGive(s_data);
        if (n) return n;
        if (esp_timer_get_time() >= deadline) break;
        vTaskDelay(pdMS_TO_TICKS(5));
    } while (true);
    return 0;
}

size_t muse_hatch_mp3_selftest(int16_t **pcm)
{
    *pcm = NULL;
    ESP_LOGW(TAG, "MP3 self-test is not available in OpenAI PCM mode");
    return 0;
}

void muse_hatch_text_turn(char *text)
{
    muse_mode_t mode = muse_state_mode(NULL);
    if (!text || !text[0] || strlen(text) >= TEXT_CAP) {
        muse_hatch_console("error", "TEXT MUST BE 1-2047 BYTES", NULL);
        free(text);
        return;
    }
    if (mode == MUSE_MODE_LISTENING || mode == MUSE_MODE_THINKING || mode == MUSE_MODE_SPEAKING
        || !muse_hatch_ready() || !begin_job()) {
        muse_hatch_console("error", "OPENAI BUSY OR WI-FI/KEY NOT SET", NULL);
        free(text);
        return;
    }
    job_t job = { .kind = JOB_TEXT, .generation = atomic_load(&s_generation), .text = text };
    if (xQueueSend(s_jobs, &job, 0) != pdTRUE) {
        atomic_store(&s_busy, false);
        free(text);
        muse_hatch_console("error", "OPENAI REQUEST QUEUE FULL", NULL);
        return;
    }
}

void muse_hatch_text_cancel(void)
{
    muse_hatch_turn_cancel();
}
