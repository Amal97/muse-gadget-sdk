// SPDX-License-Identifier: Apache-2.0
#include <assert.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>
#include <ctype.h>

#include "host_compat.h"
#include "cJSON.h"
#include "esp_http_client.h"
#include "muse_openai.h"
#include "muse_openai_codec.h"
#include "muse_chat_priv.h"
#include "muse_settings.h"
#include "muse_state.h"
#include "muse_wifi.h"
#include "muse_audio.h"
#include "muse_companion.h"

#define ESP_ERR_NO_MEM 2
#define ESP_LOGI(tag, ...) ((void)(tag))
#define ESP_LOGW(tag, ...) ((void)(tag))
#define ESP_LOGE(tag, ...) ((void)(tag))
#define ESP_ERROR_CHECK(err) assert((err) == ESP_OK)
#define MALLOC_CAP_SPIRAM 1
#define MALLOC_CAP_8BIT 2
#define CONFIG_MUSE_OPENAI_CHAT_MODEL "gpt-4o-mini"
#define CONFIG_MUSE_OPENAI_TRANSCRIPTION_MODEL "gpt-4o-mini-transcribe"
#define CONFIG_MUSE_OPENAI_SPEECH_MODEL "gpt-4o-mini-tts"
#define CONFIG_MUSE_OPENAI_VOICE "coral"
#define CONFIG_MUSE_OPENCLAW 1
#define pdTRUE 1
#define pdPASS 1
#define portMAX_DELAY 0
#define pdMS_TO_TICKS(ms) (ms)
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(lock) ((void)(lock))
#define portEXIT_CRITICAL(lock) ((void)(lock))
typedef int portMUX_TYPE;
typedef int TickType_t;
typedef int SemaphoreHandle_t;
typedef struct { unsigned count, size, limit; unsigned char data[8][128]; } fake_queue_t;
typedef fake_queue_t *QueueHandle_t;
static QueueHandle_t feeder_queue;
static void (*job_feeder)(void);
static jmp_buf worker_exit;

static SemaphoreHandle_t xSemaphoreCreateMutex(void) { return 1; }
static void xSemaphoreTake(int lock, int wait) { (void)lock; (void)wait; }
static void xSemaphoreGive(int lock) { (void)lock; }
static QueueHandle_t xQueueCreate(unsigned limit, unsigned size)
{
    assert(limit <= 8 && size <= 128);
    QueueHandle_t queue = calloc(1, sizeof(*queue));
    assert(queue);
    queue->size = size;
    queue->limit = limit;
    if (limit == 1) feeder_queue = queue;
    return queue;
}
static void xQueueReset(QueueHandle_t queue) { queue->count = 0; }
static int xQueueSend(QueueHandle_t queue, const void *value, int wait)
{
    (void)wait;
    if (queue->count == queue->limit) return 0;
    memcpy(queue->data[queue->count++], value, queue->size);
    return 1;
}
static int xQueueReceive(QueueHandle_t queue, void *value, int wait)
{
    (void)wait;
    if (!queue->count && queue == feeder_queue && job_feeder) job_feeder();
    if (!queue->count) {
        if (queue == feeder_queue) longjmp(worker_exit, 1);
        return 0;
    }
    memcpy(value, queue->data[0], queue->size);
    queue->count--;
    memmove(queue->data[0], queue->data[1], queue->count * sizeof(queue->data[0]));
    return 1;
}
static int64_t now;
static void (*audio_consumer)(void);
static int64_t esp_timer_get_time(void) { return now; }
static void vTaskDelay(int ticks)
{
    now += (int64_t)ticks * 1000;
    if (audio_consumer) audio_consumer();
}
static int xTaskCreate(void (*fn)(void *), const char *name, unsigned stack, void *arg, unsigned priority, void *out)
{
    (void)fn; (void)name; (void)stack; (void)arg; (void)priority; (void)out;
    return pdPASS;
}
static void *heap_caps_malloc(size_t size, int caps) { (void)caps; return malloc(size); }
static void esp_fill_random(void *out, size_t size)
{
    for (size_t i = 0; i < size; i++) ((uint8_t *)out)[i] = (uint8_t)i;
}
static void mbedtls_platform_zeroize(void *ptr, size_t size) { memset(ptr, 0, size); }
static bool wifi_on = true, speaker_on = true;
static bool asleep;
bool muse_state_asleep(void) { return asleep; }
bool muse_state_on_battery(void) { return true; }
void muse_state_set_asleep(bool value) { asleep = value; }
void muse_state_set_caption(const char *format, ...) { (void)format; }
void muse_companion_start(void) {}
static muse_timer_state_t timer_phase;
static unsigned timer_seconds, timer_pending;
muse_timer_state_t muse_timer_status(unsigned *seconds)
{
    if (seconds) *seconds = 0;
    return timer_phase;
}
bool muse_timer_dismiss(void) { return true; }
bool muse_timer_start(unsigned seconds) { timer_seconds = seconds; return true; }
unsigned muse_timer_pending(void) { return timer_pending; }
bool muse_wifi_connected(void) { return wifi_on; }
bool muse_settings_speaker_on(void) { return speaker_on; }
bool muse_settings_copilot_watch(void) { return false; }
size_t muse_settings_openai_key_len(void) { return 8; }
void muse_settings_openai_key(char *out) { strcpy(out, "test-key"); }
static bool use_openclaw, bridge_token_set = true, bridge_changed;
bool muse_settings_openclaw_enabled(void) { return use_openclaw; }
bool muse_settings_openclaw_token_set(void) { return bridge_token_set; }
void muse_settings_openclaw(char *url, char *token)
{
    if (url) strcpy(url, bridge_changed ? "https://192.168.1.73:8765/v1/chat/completions" :
                   "https://192.168.1.72:8765/v1/chat/completions");
    if (token) strcpy(token, bridge_token_set ? (bridge_changed ? "changed-key" : "bridge-key") : "");
}
muse_mode_t muse_state_mode(float *seconds) { (void)seconds; return MUSE_MODE_IDLE; }
static unsigned console_errors, console_done, console_sent;
void muse_hatch_console(const char *type, const char *text, const char *fields, ...)
{
    (void)text; (void)fields;
    console_errors += !strcmp(type, "error");
    console_done += !strcmp(type, "done");
    console_sent += !strcmp(type, "sent");
}
bool muse_hatch_caption_at(const char *text, size_t at, char *out, size_t cap)
{
    strlcpy(out, text + at, cap);
    return text[0] != '\0';
}
typedef struct { const char *server; } esp_sntp_config_t;
#define ESP_NETIF_SNTP_DEFAULT_CONFIG(server_name) { server_name }
static int esp_netif_sntp_init(const esp_sntp_config_t *config) { (void)config; return 0; }

typedef struct {
    const uint8_t *body;
    size_t size;
    int status;
    const char *type;
} fixture_t;
struct fake_esp_http_client {
    esp_http_client_config_t config;
    fixture_t fixture;
    size_t read_at, written, expected;
    char upload[65536];
};
static fixture_t fixtures[8];
static struct fake_esp_http_client clients[8];
static unsigned fixture_count, client_count, closed_count, read_count;
static bool cancel_on_read, change_on_cancel, incomplete, fail_open;
static char paths[8][160];
const char openai_test_root[] asm("_binary_openai_root_pem_start") = "public test root";
const char openclaw_test_root[] asm("_binary_openclaw_root_start") = "public bridge root";
const char *esp_err_to_name(int error) { (void)error; return "test"; }
esp_http_client_handle_t esp_http_client_init(const esp_http_client_config_t *config)
{
    assert(client_count < fixture_count);
    struct fake_esp_http_client *client = &clients[client_count];
    memset(client, 0, sizeof(*client));
    client->config = *config;
    client->fixture = fixtures[client_count];
    assert(config->disable_auto_redirect);
    if (!strncmp(config->url, "https://api.openai.com/v1/", 26)) {
        assert(config->cert_pem == openai_test_root && !config->common_name);
    } else {
        assert(!strcmp(config->url, "https://192.168.1.72:8765/v1/chat/completions")
               || !strcmp(config->url, "https://192.168.1.72:8765/v1/notifications")
               || !strcmp(config->url, "https://192.168.1.72:8765/v1/companion")
               || strstr(config->url, "https://192.168.1.72:8765/v1/jobs/") == config->url);
        assert(config->cert_pem == openclaw_test_root);
        assert(!strcmp(config->common_name, "muse-openclaw.local"));
    }
    strlcpy(paths[client_count], config->url, sizeof(paths[0]));
    client_count++;
    return client;
}
int esp_http_client_set_header(esp_http_client_handle_t client, const char *key, const char *value)
{
    if (!strcmp(key, "Authorization")) {
        assert(!strcmp(value, client->config.cert_pem == openclaw_test_root ?
                       "Bearer bridge-key" : "Bearer test-key"));
    }
    return 0;
}
static int esp_http_client_open(esp_http_client_handle_t client, int size)
{
    client->expected = (size_t)size;
    return fail_open ? -1 : 0;
}
static int esp_http_client_get_and_clear_last_tls_error(esp_http_client_handle_t client, int *error, int *flags)
{
    (void)client;
    *error = *flags = 0;
    return 0;
}
static int esp_http_client_write(esp_http_client_handle_t client, const char *data, int size)
{
    int n = size > 9 ? 9 : size;
    assert(client->written + (size_t)n < sizeof(client->upload));
    memcpy(client->upload + client->written, data, (size_t)n);
    client->written += (size_t)n;
    client->upload[client->written] = '\0';
    return n;
}
static int64_t esp_http_client_fetch_headers(esp_http_client_handle_t client)
{
    assert(client->written == client->expected);
    esp_http_client_event_t event = {
        .event_id = HTTP_EVENT_ON_HEADER, .header_key = "Content-Type",
        .header_value = client->fixture.type, .user_data = client->config.user_data,
    };
    client->config.event_handler(&event);
    return (int64_t)client->fixture.size;
}
int esp_http_client_get_status_code(esp_http_client_handle_t client) { return client->fixture.status; }
static int esp_http_client_read(esp_http_client_handle_t client, char *out, int cap)
{
    read_count++;
    if (cancel_on_read) {
        cancel_on_read = false;
        muse_hatch_turn_cancel();
        if (change_on_cancel) bridge_changed = true;
    }
    size_t n = client->fixture.size - client->read_at;
    size_t fragment = read_count % 7 + 1;
    if (n > fragment) n = fragment;
    if (n > (size_t)cap) n = (size_t)cap;
    memcpy(out, client->fixture.body + client->read_at, n);
    client->read_at += n;
    return (int)n;
}
static bool esp_http_client_is_complete_data_received(esp_http_client_handle_t client)
{
    return !incomplete && client->read_at == client->fixture.size;
}
static int esp_http_client_close(esp_http_client_handle_t client) { (void)client; return 0; }
int esp_http_client_cleanup(esp_http_client_handle_t client) { (void)client; closed_count++; return 0; }

/* Production source is inserted here by test_muse_openai.py. */
#define worker worker_task
/* OPENAI_IMPLEMENTATION */
#undef worker

static void worker(void *arg)
{
    if (!setjmp(worker_exit)) worker_task(arg);
}

static const char transcript[] = "{\"text\":\"Hello there\"}";
static const char answer[] = "{\"choices\":[{\"message\":{\"content\":\"Hello friend.\"},\"finish_reason\":\"stop\"}]}";
static const char job_running[] =
    "{\"id\":\"000102030405060708090a0b0c0d0e0f\",\"status\":\"running\",\"elapsed_seconds\":123}";
static const char job_completed[] =
    "{\"id\":\"000102030405060708090a0b0c0d0e0f\",\"status\":\"completed\",\"reply\":\"Hello friend.\"}";
static const uint8_t speech[] = { 0, 0, 100, 0, 200, 0, 44, 1, 144, 1, 244, 1 };

static void fixture(const void *body, size_t size, int status, const char *type)
{
    fixtures[fixture_count++] = (fixture_t){ body, size, status, type };
}
static void good_fixtures(void)
{
    fixture(transcript, strlen(transcript), 200, "application/json");
    if (use_openclaw) {
        fixture(job_running, strlen(job_running), 200, "application/json");
        fixture(job_completed, strlen(job_completed), 200, "application/json");
    } else {
        fixture(answer, strlen(answer), 200, "application/json");
    }
    fixture(speech, sizeof(speech), 200, "audio/pcm");
}
static void reset_transport(void)
{
    fixture_count = client_count = closed_count = read_count = 0;
    incomplete = fail_open = cancel_on_read = false;
    xQueueReset(s_events);
}
static void record_note(void)
{
    int16_t pcm[12] = {1, 2, 3, 4};
    assert(muse_hatch_ready());
    muse_hatch_turn_begin();
    muse_hatch_turn_audio(pcm, 12);
    muse_hatch_turn_end();
}
static void pipeline(void)
{
    good_fixtures();
    record_note();
    worker(NULL);
    assert(client_count == 3 && closed_count == 3 && !atomic_load(&s_busy));
    assert(strstr(paths[0], "/audio/transcriptions"));
    char *wav = strstr(clients[0].upload, "RIFF");
    assert(wav && !memcmp(wav + 8, "WAVE", 4));
    assert((uint8_t)wav[4] == 60 && (uint8_t)wav[40] == 24);
    assert(clients[0].written == clients[0].expected);
    cJSON *body = cJSON_Parse(clients[1].upload);
    assert(body);
    assert(!strcmp(cJSON_GetObjectItem(body, "model")->valuestring, "gpt-4o-mini"));
    assert(cJSON_GetObjectItem(body, "max_completion_tokens")->valueint == 240);
    assert(cJSON_GetArraySize(cJSON_GetObjectItem(body, "messages")) == 2);
    cJSON_Delete(body);
    body = cJSON_Parse(clients[2].upload);
    assert(!strcmp(cJSON_GetObjectItem(body, "response_format")->valuestring, "pcm"));
    cJSON_Delete(body);
    char text[96];
    assert(muse_hatch_turn_event(text, sizeof(text)) == MUSE_HATCH_EV_HEARD);
    assert(!strcmp(text, "Hello there"));
    assert(muse_hatch_turn_event(text, sizeof(text)) == MUSE_HATCH_EV_REPLY);
    assert(!strcmp(text, "Hello friend."));
    assert(muse_hatch_turn_event(text, sizeof(text)) == MUSE_HATCH_EV_DONE);
    int16_t pcm[8];
    assert(muse_hatch_turn_read(pcm, 8, 0) == 4);
    assert(pcm[0] == 0 && pcm[1] == 150 && pcm[2] == 300 && pcm[3] == 450);
    muse_hatch_status_t status;
    muse_hatch_status(&status);
    assert(status.state == MUSE_HATCH_REACHABLE);
}
static void failures(void)
{
    const int codes[] = {401, 403, 404, 429, 500};
    const char *messages[] = {"KEY INVALID", "ACCESS DENIED", "MODEL NOT AVAILABLE", "QUOTA OR RATE LIMIT", "HTTP 500"};
    for (size_t i = 0; i < 5; i++) {
        reset_transport();
        fixture("{}", 2, codes[i], "application/json");
        record_note();
        worker(NULL);
        char text[96];
        assert(muse_hatch_turn_event(text, sizeof(text)) == MUSE_HATCH_EV_ERROR);
        assert(strstr(text, messages[i]));
        assert(client_count == 1 && closed_count == 1 && muse_hatch_ready());
    }
    reset_transport();
    good_fixtures();
    record_note();
    worker(NULL);
    assert(client_count == 3); /* A failed turn doesn't poison the next one. */
}
static void invalid_responses(void)
{
    const char *invalid[] = {"not json", "{\"text\":\"\"}", "{\"other\":1}"};
    for (size_t i = 0; i < 3; i++) {
        reset_transport();
        fixture(invalid[i], strlen(invalid[i]), 200, "application/json");
        record_note();
        worker(NULL);
        char text[96];
        assert(muse_hatch_turn_event(text, sizeof(text)) == MUSE_HATCH_EV_ERROR);
        assert(!strcmp(text, "NO VALID SPEECH TRANSCRIPT"));
        assert(client_count == 1);
    }
    reset_transport();
    good_fixtures();
    fixtures[2].type = "application/json";
    record_note();
    worker(NULL);
    char text[96];
    assert(muse_hatch_turn_event(text, sizeof(text)) == MUSE_HATCH_EV_HEARD);
    assert(muse_hatch_turn_event(text, sizeof(text)) == MUSE_HATCH_EV_REPLY);
    assert(muse_hatch_turn_event(text, sizeof(text)) == MUSE_HATCH_EV_ERROR);
    assert(!strcmp(text, "OPENAI SPEECH FORMAT INVALID") && s_audio_n == 0);
}
static void cancellation(void)
{
    good_fixtures();
    record_note();
    cancel_on_read = true;
    worker(NULL);
    char text[96];
    assert(muse_hatch_turn_event(text, sizeof(text)) == MUSE_HATCH_EV_NONE);
    assert(!atomic_load(&s_busy) && s_audio_n == 0 && client_count == 1 && closed_count == 1);
    reset_transport();
    good_fixtures();
    record_note();
    worker(NULL);
    assert(client_count == 3);
    muse_hatch_turn_cancel();
    assert(s_audio_n == 0 && !s_reply[0]);
}
static void consume_silence(void)
{
    int16_t pcm[160];
    size_t n = muse_hatch_turn_read(pcm, 160, 0);
    for (size_t i = 0; i < n; i++) assert(pcm[i] == 0);
}
static void typed_and_muted(void)
{
    fixture(answer, strlen(answer), 200, "application/json");
    muse_hatch_text_turn(strdup("Hello"));
    worker(NULL);
    assert(client_count == 1 && console_sent == 1 && console_done == 1 && console_errors == 0);
    reset_transport();
    good_fixtures();
    speaker_on = false;
    audio_consumer = consume_silence;
    record_note();
    worker(NULL);
    assert(client_count == 2 && s_audio_n > 0); /* Caption pacing, but no paid TTS. */
    while (s_audio_n) consume_silence();
    assert(s_audio_n == 0);
}
static void incomplete_download(void)
{
    good_fixtures();
    incomplete = true;
    record_note();
    worker(NULL);
    char text[96];
    assert(muse_hatch_turn_event(text, sizeof(text)) == MUSE_HATCH_EV_ERROR);
    assert(!strcmp(text, "OPENAI RESPONSE INCOMPLETE") && client_count == 1);
}
static uint32_t le32(const uint8_t *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}
static void codec_boundaries(void)
{
    uint8_t wav[44];
    muse_openai_wav_header(wav, 16000 * 15);
    assert(le32(wav + 4) == 480036 && le32(wav + 40) == 480000);
    assert(le32(wav + 24) == 16000 && le32(wav + 28) == 32000);
    muse_openai_wav_header(wav, 0);
    assert(le32(wav + 4) == 36 && le32(wav + 40) == 0);
    muse_openai_resampler_t state = {0};
    int16_t out[2];
    const uint8_t extremes[] = {0, 128, 0, 128, 0, 128, 255, 127, 255, 127, 255, 127};
    for (size_t i = 0; i < sizeof(extremes); i++) {
        size_t frames = muse_openai_resample_byte(&state, extremes[i], out);
        assert(frames == (i % 6 == 5 ? 2 : 0));
        if (frames) assert(out[0] == (i < 6 ? -32768 : 32767) && out[1] == out[0]);
    }
    assert(state.used == 0);
    muse_hatch_turn_begin();
    int16_t *pcm = calloc(RECORD_FRAMES + 1, sizeof(int16_t));
    assert(pcm);
    assert(muse_hatch_turn_audio_wait(pcm, RECORD_FRAMES + 1, 0) == RECORD_FRAMES);
    muse_hatch_turn_audio(pcm, 1);
    char text[96];
    assert(muse_hatch_turn_event(text, sizeof(text)) == MUSE_HATCH_EV_ERROR);
    assert(!strcmp(text, "RECORDING BUFFER FULL"));
    muse_hatch_turn_cancel();
    assert(muse_hatch_ready() && !s_record && client_count == 0);
    free(pcm);
}
static int next_turn;
static void feed_next_turn(void)
{
    if (next_turn == 7) {
        job_feeder = NULL;
        return;
    }
    if (next_turn == 6) muse_openai_clear_history();
    char text[32];
    snprintf(text, sizeof(text), "Turn %d", next_turn++);
    muse_hatch_text_turn(strdup(text));
}
static void bounded_history(void)
{
    for (int i = 0; i < 7; i++) fixture(answer, strlen(answer), 200, "application/json");
    job_feeder = feed_next_turn;
    feed_next_turn();
    worker(NULL);
    assert(client_count == 7 && console_sent == 7 && console_done == 7);
    for (int i = 0; i < 7; i++) {
        cJSON *body = cJSON_Parse(clients[i].upload);
        int previous = i < 4 ? i : 4;
        if (i == 6) previous = 0;
        assert(cJSON_GetArraySize(cJSON_GetObjectItem(body, "messages")) == 2 + previous * 2);
        cJSON_Delete(body);
    }
}
static void clear_active_voice(void)
{
    good_fixtures();
    record_note();
    muse_openai_clear_history();
    char text[96];
    assert(muse_hatch_turn_event(text, sizeof(text)) == MUSE_HATCH_EV_ERROR);
    assert(!strcmp(text, "CONVERSATION CLEARED"));
    worker(NULL);
    assert(client_count == 0 && muse_hatch_ready());
    muse_hatch_status_t status;
    muse_hatch_status(&status);
    assert(status.state == MUSE_HATCH_UNTESTED);
    reset_transport();
    good_fixtures();
    record_note();
    worker(NULL);
    assert(client_count == 3);
}
static void hybrid_pipeline(void)
{
    use_openclaw = true;
    good_fixtures();
    record_note();
    worker(NULL);
    assert(client_count == 4 && closed_count == 4);
    assert(clients[0].config.cert_pem == openai_test_root);
    assert(clients[1].config.cert_pem == openclaw_test_root);
    assert(clients[2].config.cert_pem == openclaw_test_root);
    assert(clients[3].config.cert_pem == openai_test_root);
    assert(strstr(paths[1], "/jobs/start") && strstr(paths[2], "/jobs/status"));
    cJSON *body = cJSON_Parse(clients[1].upload);
    assert(!strcmp(cJSON_GetObjectItem(body, "id")->valuestring, "000102030405060708090a0b0c0d0e0f"));
    const char *prompt = cJSON_GetObjectItem(
        cJSON_GetArrayItem(cJSON_GetObjectItem(body, "messages"), 0), "content")->valuestring;
    assert(strlen(prompt) < 2047);
    assert(strstr(prompt, "Use available tools") && strstr(prompt, "tool result confirms success"));
    assert(strstr(prompt, "confirm the exact") && strstr(prompt, "named --to and --text")
           && strstr(prompt, "--no-sms-fallback") && strstr(prompt, "retry a failed send"));
#if CONFIG_MUSE_OPENCLAW_NORMAL_CHROME
    assert(strstr(prompt, "read the normal-chrome skill") && strstr(prompt, "Never use the isolated"));
    assert(strstr(prompt, "python3 \"$HOME/.openclaw/muse-esp32/normal_chrome.py\"")
           && strstr(prompt, "JSON tool parameters") && strstr(prompt, "Do not use AppleScript")
           && strstr(prompt, "instead of trying another method"));
    assert(strstr(prompt, "Use new_page with url") && strstr(prompt, "evaluate_script with")
           && strstr(prompt, "with the read tool") && strstr(prompt, "() => document.title"));
    assert(!strstr(prompt, "For browser actions, use the openclaw profile"));
#else
    assert(strstr(prompt, "For browser actions, use the openclaw profile"));
#endif
    assert(!strstr(prompt, "Do not claim to control devices"));
    cJSON_Delete(body);
    muse_hatch_turn_cancel();
    reset_transport();
    fixture(answer, strlen(answer), 401, "application/json");
    muse_hatch_text_turn(strdup("Hello"));
    worker(NULL);
    muse_hatch_status_t status;
    muse_hatch_status(&status);
    assert(status.state == MUSE_HATCH_UNREACHABLE);
    assert(!strcmp(status.detail, "OPENCLAW BRIDGE TOKEN INVALID"));
    assert(client_count == 1 && console_errors == 1 && console_done == 0);
    reset_transport();
    bridge_token_set = false;
    assert(!muse_hatch_ready());
    muse_hatch_status(&status);
    assert(status.state == MUSE_HATCH_NOT_SET);
    bridge_token_set = true;
    use_openclaw = false;
    muse_hatch_config_changed();
    good_fixtures();
    record_note();
    worker(NULL);
    assert(client_count == 3 && clients[1].config.cert_pem == openai_test_root);
}

static void incoming_notifications(void)
{
    use_openclaw = true;
    const char message[] = "{\"notification\":{\"id\":\"0123456789abcdef0123456789abcdef\","
                           "\"sender\":\"Test sender\",\"preview\":\"Test preview\"}}";
    fixture(message, strlen(message), 200, "application/json");
    poll_notifications();
    muse_notification_t notification;
    assert(muse_openai_notification(&notification));
    assert(!strcmp(notification.sender, "Test sender"));
    assert(!strcmp(notification.preview, "Test preview"));
    assert(!strcmp(paths[0], "https://192.168.1.72:8765/v1/notifications"));
    assert(clients[0].config.timeout_ms == 2000);
    assert(clients[0].config.cert_pem == openclaw_test_root);
    assert(!strcmp(clients[0].upload, "{\"ack\":\"\"}"));
    fixture(message, strlen(message), 200, "application/json");
    poll_notifications();
    assert(client_count == 2);
    muse_openai_notification_dismiss();
    assert(!muse_openai_notification(&notification));
    asleep = true;
    poll_notifications();
    assert(client_count == 2);
    asleep = false;
    atomic_store(&s_busy, true);
    poll_notifications();
    assert(client_count == 2);
    atomic_store(&s_busy, false);
    fixture("{}", 2, 503, "application/json");
    poll_notifications();
    assert(s_notification_ack[0] && client_count == 3);
    const char empty[] = "{\"notification\":null}";
    fixture(empty, strlen(empty), 200, "application/json");
    poll_notifications();
    assert(!s_notification_ack[0] && !muse_openai_notification(&notification));
    assert(strstr(clients[3].upload, "0123456789abcdef0123456789abcdef"));
    const char invalid[] = "{\"notification\":{\"id\":\"bad\",\"sender\":\"x\",\"preview\":\"y\"}}";
    fixture(invalid, strlen(invalid), 200, "application/json");
    poll_notifications();
    assert(!muse_openai_notification(&notification));
    fixture(message, strlen(message), 200, "application/json");
    poll_notifications();
    assert(muse_openai_notification(&notification));
    muse_hatch_config_changed();
    assert(!muse_openai_notification(&notification) && !s_notification_ack[0]);
    use_openclaw = false;
    poll_notifications();
    assert(client_count == 6);
}
static void local_timer_shortcut(void)
{
    use_openclaw = true;
    const char timer[] = "{\"text\":\"Set a timer for 5 minutes\"}";
    fixture(timer, strlen(timer), 200, "application/json");
    fixture(speech, sizeof(speech), 200, "audio/pcm");
    record_note();
    worker(NULL);
    assert(timer_seconds == 300 && client_count == 2);
    assert(strstr(paths[0], "/audio/transcriptions") && strstr(paths[1], "/audio/speech"));
    timer_pending = 600;
    timer_phase = MUSE_TIMER_RINGING;
    muse_notification_t notification;
    assert(muse_openai_notification(&notification));
    assert(!strcmp(notification.kind, "timer"));
}

static void dictated_reply(bool valid)
{
    use_openclaw = true;
    const char id[] = "0123456789abcdef0123456789abcdef";
    strlcpy(s_notification.id, id, sizeof(s_notification.id));
    strlcpy(s_notification.kind, "imessage", sizeof(s_notification.kind));
    assert(muse_openai_reply_begin(id));
    const char draft[] = "{\"draft\":{\"id\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\","
        "\"state\":\"unconfirmed\",\"recipient\":\"Test recipient: +15555550100\","
        "\"text\":\"Hello there\",\"preview\":\"Hello there\",\"detail\":\"Not sent\"}}";
    fixture(transcript, strlen(transcript), 200, "application/json");
    fixture(valid ? draft : "{}", valid ? strlen(draft) : 2, 200, "application/json");
    record_note();
    worker(NULL);
    assert(client_count == 2 && strstr(paths[1], "/companion"));
    cJSON *body = cJSON_Parse(clients[1].upload);
    assert(!strcmp(cJSON_GetObjectItem(body, "action")->valuestring, "reply_prepare"));
    assert(!strcmp(cJSON_GetObjectItem(body, "notification")->valuestring, id));
    assert(!strcmp(cJSON_GetObjectItem(body, "text")->valuestring, "Hello there"));
    cJSON_Delete(body);
    if (valid) {
        assert(!strcmp(s_notification_ack, id) && !s_notification.id[0]);
        assert(s_draft_notice.id[0]);
    } else {
        assert(!s_notification_ack[0] && !strcmp(s_notification.id, id));
        muse_hatch_status_t status;
        muse_hatch_status(&status);
        assert(!strcmp(status.detail, "COMPANION RESPONSE INVALID"));
    }
}

static void scoped_stop_after_settings_change(void)
{
    use_openclaw = true;
    const char stopped[] = "{\"id\":\"000102030405060708090a0b0c0d0e0f\","
        "\"status\":\"cancelled\",\"detail\":\"Native run stopped\"}";
    fixture(job_running, strlen(job_running), 200, "application/json");
    fixture(stopped, strlen(stopped), 200, "application/json");
    cancel_on_read = change_on_cancel = true;
    muse_hatch_text_turn(strdup("Harmless task"));
    worker(NULL);
    assert(bridge_changed && client_count == 2 && strstr(paths[1], "/jobs/cancel"));
    assert(!muse_openai_job_active() && !atomic_load(&s_busy));
}

static void snooze_ack_only_after_success(void)
{
    use_openclaw = true;
    const char id[] = "0123456789abcdef0123456789abcdef";
    strlcpy(s_notification.id, id, sizeof(s_notification.id));
    strlcpy(s_notification.kind, "reminder", sizeof(s_notification.kind));
    const char request[] = "{\"action\":\"snooze\",\"id\":\"0123456789abcdef0123456789abcdef\",\"seconds\":300}";
    fixture("{}", 2, 503, "application/json");
    assert(muse_openai_companion_command(request));
    worker(NULL);
    assert(!strcmp(s_notification.id, id) && !s_notification_ack[0]);
    reset_transport();
    fixture("{\"settings\":{}}", 15, 200, "application/json");
    assert(muse_openai_companion_command(request));
    worker(NULL);
    assert(!s_notification.id[0] && !strcmp(s_notification_ack, id));
}

int main(int argc, char **argv)
{
    assert(argc == 2);
    muse_hatch_start();
    switch (atoi(argv[1])) {
    case 0: pipeline(); break;
    case 1: failures(); break;
    case 2: invalid_responses(); break;
    case 3: cancellation(); break;
    case 4: typed_and_muted(); break;
    case 5: incomplete_download(); break;
    case 6: codec_boundaries(); break;
    case 7: bounded_history(); break;
    case 8: clear_active_voice(); break;
    case 9: hybrid_pipeline(); break;
    case 10: incoming_notifications(); break;
    case 11: local_timer_shortcut(); break;
    case 12: dictated_reply(true); break;
    case 13: dictated_reply(false); break;
    case 14: scoped_stop_after_settings_change(); break;
    case 15: snooze_ack_only_after_success(); break;
    default: return 2;
    }
    muse_hatch_turn_cancel();
    free(s_audio);
    free(s_jobs);
    free(s_events);
    return 0;
}
