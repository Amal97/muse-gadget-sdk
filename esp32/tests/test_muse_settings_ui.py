# SPDX-License-Identifier: Apache-2.0
"""Compile the production settings builders for each supported backend."""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_cardputer_navigation import function

ROOT = Path(__file__).resolve().parents[1]


class SettingsUITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        directory = Path(cls.tmp.name)
        settings = (ROOT / "components/muse/muse_settings_ui.c").read_text()
        source = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define ESP_OK 0
#define COLOR_ACCENT 1
#define COLOR_DANGER 2
#define COLOR_TEXT 3
#define COLOR_OK 4
#define COLOR_WARN 5
#define COLOR_DIM 6
#define LV_OBJ_FLAG_HIDDEN 1
#define LV_SYMBOL_HOME "home"
#define LV_SYMBOL_WIFI "wifi"
#define LV_SYMBOL_BLUETOOTH "bluetooth"
#define LV_SYMBOL_VOLUME_MAX "volume"
#define LV_SYMBOL_EYE_CLOSE "sleep"
#define LV_SYMBOL_BATTERY_FULL "battery"
#define LV_SYMBOL_POWER "power"
typedef struct { int unused; } lv_obj_t;
typedef struct { int unused; } lv_event_t;
typedef struct { int unused; } page_t;
static page_t WIFI, HATCH, BLE, SOUND, SLEEP, BATTERY, POWER;
static lv_obj_t object;
static lv_obj_t *s_home, *s_hatch, *s_home_wifi, *s_home_hatch, *s_home_ble;
static lv_obj_t *s_home_sound, *s_home_sleep, *s_home_battery, *s_about;
static lv_obj_t *s_hatch_status, *s_hatch_token;
#if !CONFIG_MUSE_OPENAI
static lv_obj_t *s_hatch_host, *s_hatch_vm, *s_link_status, *s_link_reset_lbl;
static int64_t s_link_reset_armed_us;
#endif
#if CONFIG_MUSE_OPENCLAW
static lv_obj_t *s_openclaw_url, *s_openclaw_token;
static int save_count;
static char saved_url[128] = "https://existing.invalid/v1/chat/completions";
static char caption[96];
static bool fail_save;
#endif
static const char *titles[8], *labels[32], *notes[16];
static int title_count, label_count, note_count;
static lv_obj_t *page(lv_obj_t *tile, const char *title, bool back, lv_obj_t **list) {
    (void)tile; (void)back; *list = &object; titles[title_count++] = title; return &object;
}
static lv_obj_t *row(lv_obj_t *list, const char *icon, const char *text, lv_obj_t **out,
                     void (*callback)(lv_event_t *), void *arg) {
    (void)list; (void)icon; (void)callback; (void)arg;
    if (out) *out = &object;
    labels[label_count++] = text; return &object;
}
static lv_obj_t *button(lv_obj_t *list, const char *text, int color,
                        void (*callback)(lv_event_t *), lv_obj_t **out) {
    return row(list, NULL, text, out, callback, (void *)(intptr_t)color);
}
static lv_obj_t *note(lv_obj_t *list, const char *text) {
    (void)list; notes[note_count++] = text; return &object;
}
void lv_obj_add_flag(lv_obj_t *obj, int flag) { (void)obj; (void)flag; }
void on_nav(lv_event_t *e) { (void)e; }
void on_hatch_token(lv_event_t *e) { (void)e; }
void on_hatch_test(lv_event_t *e) { (void)e; }
void on_openai_clear(lv_event_t *e) { (void)e; }
void on_openai_forget(lv_event_t *e) { (void)e; }
void on_openclaw_url(lv_event_t *e) { (void)e; }
void on_openclaw_token(lv_event_t *e) { (void)e; }
void on_link_reset(lv_event_t *e) { (void)e; }
void on_hatch_host(lv_event_t *e) { (void)e; }
void on_hatch_vm(lv_event_t *e) { (void)e; }
#if CONFIG_MUSE_OPENCLAW
static int muse_settings_set_openclaw_url(const char *text) {
    ++save_count;
    if (fail_save) return -1;
    snprintf(saved_url, sizeof(saved_url), "%s", text); return ESP_OK;
}
static void muse_state_set_caption(const char *text) {
    snprintf(caption, sizeof(caption), "%s", text);
}
""" + function(settings, "on_openclaw_url_done") + "\n#endif\n" + \
            function(settings, "build_hatch_page") + "\n" + function(settings, "build_home") + r"""
static bool has_label(const char *text) {
    for (int i = 0; i < label_count; ++i) if (!strcmp(labels[i], text)) return true;
    return false;
}
int main(void) {
    build_home(NULL);
    build_hatch_page(NULL);
    assert(!strcmp(titles[0], "SETTINGS"));
    assert(has_label("Wi-Fi") && has_label("Sound") && has_label("Sleep"));
    assert(has_label("Battery") && has_label("Power off"));
#if CONFIG_MUSE_OPENCLAW
    assert(!strcmp(titles[1], "OPENCLAW"));
    assert(has_label("OpenClaw") && !has_label("OpenAI"));
    assert(has_label("Voice key") && has_label("Test OpenAI key"));
    assert(has_label("Mac bridge") && has_label("Bridge token"));
    assert(has_label("New conversation") && has_label("Remove voice key"));
    assert(!has_label("Chat backend") && !has_label("Use direct OpenAI chat"));
    bool voice_explained = false, test_explained = false;
    for (int i = 0; i < note_count; ++i) {
        voice_explained |= strstr(notes[i], "OpenAI is still required") != NULL;
        test_explained |= strstr(notes[i], "not the Mac bridge") != NULL;
    }
    assert(voice_explained && test_explained);
    on_openclaw_url_done("");
    assert(!save_count && !strcmp(saved_url, "https://existing.invalid/v1/chat/completions"));
    on_openclaw_url_done("https://new.invalid/v1/chat/completions");
    assert(save_count == 1 && !strcmp(saved_url, "https://new.invalid/v1/chat/completions"));
    fail_save = true;
    on_openclaw_url_done("https://failed.invalid/v1/chat/completions");
    assert(!strcmp(caption, "OPENCLAW HTTPS URL NOT SAVED"));
#elif CONFIG_MUSE_OPENAI
    assert(!strcmp(titles[1], "OPENAI"));
    assert(has_label("OpenAI") && has_label("API key") && has_label("Test API key"));
    assert(has_label("New conversation") && has_label("Remove API key"));
    assert(!has_label("Mac bridge") && !has_label("Voice key"));
#else
    assert(!strcmp(titles[1], "MUSE"));
    assert(has_label("Muse") && has_label("Reset pairing"));
    assert(has_label("Server") && has_label("VM ID") && has_label("Device token"));
#endif
    return 0;
}
"""
        file = directory / "settings.c"
        file.write_text(source)
        cls.binaries = {}
        for name, openai, openclaw in (("muse", 0, 0), ("openai", 1, 0), ("openclaw", 1, 1)):
            binary = directory / name
            result = subprocess.run([
                *shlex.split(os.environ.get("CC", "cc")), "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                f"-DCONFIG_MUSE_OPENAI={openai}", f"-DCONFIG_MUSE_OPENCLAW={openclaw}",
                str(file), "-o", str(binary),
            ], capture_output=True, text=True)
            if result.returncode:
                cls.tmp.cleanup()
                raise RuntimeError(result.stdout + result.stderr)
            cls.binaries[name] = binary

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def run_variant(self, name: str) -> None:
        result = subprocess.run([str(self.binaries[name])], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_openclaw_settings_explain_voice_and_preserve_bridge_on_empty_input(self) -> None:
        self.run_variant("openclaw")

    def test_standalone_openai_settings_are_preserved(self) -> None:
        self.run_variant("openai")

    def test_original_muse_settings_are_preserved(self) -> None:
        self.run_variant("muse")


if __name__ == "__main__":
    unittest.main()
