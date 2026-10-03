// SPDX-License-Identifier: Apache-2.0
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "host_compat.h"
#include "muse_settings.h"

#define ESP_ERR_INVALID_ARG 1
#define ESP_LOGW(tag, ...) ((void)(tag))
#define ESP_LOGE(tag, ...) do { (void)(tag); if (0) fprintf(stderr, __VA_ARGS__); } while (0)
#define LOCKED(body) do { body; } while (0)

static const char *TAG = "test";
static int s_nvs, set_error, commit_error, saves, notifications;
static struct {
    char openclaw_url[MUSE_OPENCLAW_URL_MAX + 1];
    char openclaw_token[MUSE_TOKEN_MAX + 1];
} s;

static int nvs_set_str(int handle, const char *name, const char *value)
{
    (void)handle; (void)name; (void)value;
    saves++;
    return set_error;
}
static int nvs_commit(int handle) { (void)handle; return commit_error; }
static const char *esp_err_to_name(int error) { (void)error; return "test"; }
static void notify(muse_setting_t setting)
{
    assert(setting == MUSE_SETTING_OPENCLAW);
    notifications++;
}

/* OPENCLAW_SETTINGS_IMPLEMENTATION */

int main(void)
{
    const char *valid[] = {
        "https://192.168.1.72:8765/v1/chat/completions",
        "https://computer.local/v1/chat/completions",
        "https://host:65535/v1/chat/completions", "",
    };
    for (size_t i = 0; i < sizeof(valid) / sizeof(valid[0]); i++) {
        assert(muse_settings_set_openclaw_url(valid[i]) == ESP_OK);
        assert(!strcmp(s.openclaw_url, valid[i]));
        assert(muse_settings_openclaw_enabled() == (valid[i][0] != '\0'));
    }
    const char *invalid[] = {
        NULL, "http://host/v1/chat/completions", "https://user@host/v1/chat/completions",
        "https:///v1/chat/completions", "https://host:0/v1/chat/completions",
        "https://host:65536/v1/chat/completions", "https://host:bad/v1/chat/completions",
        "https://host:/v1/chat/completions", "https://host/v1/chat/completions?secret=bad",
        "https://host/v1/chat/completions#fragment", "https://host/v1/chat/completions\n",
        "https://host/not-chat", "https://host /v1/chat/completions",
    };
    int before = saves;
    for (size_t i = 0; i < sizeof(invalid) / sizeof(invalid[0]); i++) {
        assert(muse_settings_set_openclaw_url(invalid[i]) == ESP_ERR_INVALID_ARG);
    }
    char long_url[MUSE_OPENCLAW_URL_MAX + 2];
    memset(long_url, 'x', sizeof(long_url) - 1);
    long_url[sizeof(long_url) - 1] = '\0';
    assert(muse_settings_set_openclaw_url(long_url) == ESP_ERR_INVALID_ARG);
    assert(saves == before);
    assert(muse_settings_set_openclaw_token("bridge-secret") == ESP_OK);
    assert(muse_settings_openclaw_token_set());
    const char *bad_tokens[] = {NULL, "bad token", "bad\nline", "bad\rline", "bad\tline", "\x7f"};
    before = saves;
    for (size_t i = 0; i < sizeof(bad_tokens) / sizeof(bad_tokens[0]); i++) {
        assert(muse_settings_set_openclaw_token(bad_tokens[i]) == ESP_ERR_INVALID_ARG);
    }
    char long_token[MUSE_TOKEN_MAX + 2];
    memset(long_token, 'x', sizeof(long_token) - 1);
    long_token[sizeof(long_token) - 1] = '\0';
    assert(muse_settings_set_openclaw_token(long_token) == ESP_ERR_INVALID_ARG);
    assert(saves == before);
    before = notifications;
    set_error = 2;
    assert(muse_settings_set_openclaw_url(valid[0]) == 2 && !s.openclaw_url[0]);
    assert(notifications == before);
    set_error = 0;
    commit_error = 3;
    assert(muse_settings_set_openclaw_token("replacement") == 3);
    assert(!strcmp(s.openclaw_token, "bridge-secret") && notifications == before);
    commit_error = 0;
    assert(muse_settings_set_openclaw_url(valid[0]) == ESP_OK);
    char url[MUSE_OPENCLAW_URL_MAX + 1], token[MUSE_TOKEN_MAX + 1];
    muse_settings_openclaw(url, token);
    assert(!strcmp(url, valid[0]) && !strcmp(token, "bridge-secret"));
    assert(muse_settings_set_openclaw_token("") == ESP_OK);
    assert(!muse_settings_openclaw_token_set());
    return 0;
}
