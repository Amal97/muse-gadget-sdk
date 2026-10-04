# SPDX-License-Identifier: Apache-2.0
"""Exercise companion rendering with a compiler-enforced callback stack budget."""
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_cardputer_navigation import function
sys.path.pop(0)

ROOT = Path(__file__).resolve().parents[1]


class CompanionUIStackTest(unittest.TestCase):
    def test_rendering_budget_full_text_and_safe_reply_confirmation(self):
        settings = (ROOT / "components/muse/muse_settings_ui.c").read_text()
        start = settings.index("static lv_obj_t *companion_note(")
        end = settings.index("\n}", start) + 2
        source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "cJSON.h"
#include "muse_text.h"
#define COLOR_DIM 0
#define COLOR_WARN 1
#define COLOR_TEXT 2
#define COLOR_DANGER 3
#define LV_OBJ_FLAG_HIDDEN 1
#define LV_LABEL_LONG_MODE_WRAP 0
#define LV_ANIM_OFF 0
typedef struct { char text[2048]; } lv_obj_t;
typedef struct { int unused; } lv_event_t;
typedef int lv_font_t;
static const lv_font_t lv_font_montserrat_16;
static lv_obj_t object;
static lv_obj_t *s_companion_list=&object,*s_timer_status,*s_replace_timer_text;
static lv_obj_t *s_replace_timer,*s_cancel_replace,*s_companion_status;
static char *s_companion_view;
static char s_companion_detail[2048],s_companion_shown[2048],s_draft_id[33],s_latest_job[33];
static char s_calendar_ids[32][257];
static bool s_focus_draft;
static int confirmations;
static const char *const FAVOURITE_IDS[]={"timer_5","timer_10","timer_custom","reminder","briefing","dashboard"};
static const char *const FAVOURITE_LABELS[]={"5","10","Custom","Reminder","Briefing","Dashboard"};
static lv_obj_t *lv_label_create(lv_obj_t *p){(void)p;return &object;}
static int lv_pct(int p){return p;}
static int lv_color_hex(int c){return c;}
static void lv_obj_set_width(lv_obj_t *o,int w){(void)o;(void)w;}
static void lv_obj_set_style_text_font(lv_obj_t *o,const lv_font_t *f,int p){(void)o;(void)f;(void)p;}
static void lv_obj_set_style_text_color(lv_obj_t *o,int c,int p){(void)o;(void)c;(void)p;}
static void lv_label_set_long_mode(lv_obj_t *o,int m){(void)o;(void)m;}
static void lv_label_set_text(lv_obj_t *o,const char *t){snprintf(o->text,sizeof(o->text),"%s",t);}
static int32_t lv_obj_get_scroll_y(lv_obj_t *o){(void)o;return 0;}
static void lv_obj_clean(lv_obj_t *o){(void)o;}
static void lv_obj_add_flag(lv_obj_t *o,int f){(void)o;(void)f;}
static void lv_obj_update_layout(lv_obj_t *o){(void)o;}
static void lv_obj_scroll_to_view(lv_obj_t *o,int a){(void)o;(void)a;}
static void lv_obj_scroll_to_y(lv_obj_t *o,int y,int a){(void)o;(void)y;(void)a;}
static size_t fake_strlcpy(char *out,const char *text,size_t cap){
    size_t len=strlen(text);if(cap)snprintf(out,cap,"%s",text);return len;
}
#undef strlcpy
#define strlcpy fake_strlcpy
static bool muse_wifi_connected(void){return true;}
static bool muse_settings_speaker_on(void){return true;}
static unsigned muse_settings_openai_key_len(void){return 1;}
#define CALLBACK(name) static void name(lv_event_t *e){(void)e;}
CALLBACK(on_quick_action)
CALLBACK(on_timer_replace)
CALLBACK(on_timer_keep)
CALLBACK(on_timer_dismiss)
CALLBACK(on_draft_action)
CALLBACK(on_edit_draft)
CALLBACK(on_stop_latest_job)
CALLBACK(on_companion_action)
CALLBACK(on_companion_switch)
CALLBACK(on_calendar_toggle)
CALLBACK(on_favourite_toggle)
static lv_obj_t *note(lv_obj_t *o,const char *t){(void)t;return o;}
static lv_obj_t *row(lv_obj_t *o,const char *i,const char *t,lv_obj_t **out,
                     void (*cb)(lv_event_t *),void *arg){
    (void)i;(void)cb;(void)arg;if(out)*out=o;
    if(!strcmp(t,"Confirm and send iMessage"))confirmations++;
    return o;
}
static lv_obj_t *button(lv_obj_t *o,const char *t,int color,
                        void (*cb)(lv_event_t *),lv_obj_t **out){
    (void)color;return row(o,NULL,t,out,cb,NULL);
}
static void switch_row_data(lv_obj_t *o,const char *t,bool value,
                            void (*cb)(lv_event_t *),void *arg){
    (void)o;(void)t;(void)value;(void)cb;(void)arg;
}
''' + settings[start:end] + "\n" + function(settings, "fill_companion") + r'''
int main(void){
    static char long_text[2048];
    memset(long_text,'X',2047);long_text[2047]=0;
    companion_note(&object,long_text);
    assert(strlen(object.text)==2047 && !strcmp(object.text,long_text));
    companion_note(&object,"short");assert(!strcmp(object.text,"short"));
    s_companion_view="{\"draft\":{\"id\":\"abc\",\"state\":\"unconfirmed\","
        "\"recipient\":\"Test recipient\",\"text\":\"Reviewed ASCII reply\"}}";
    fill_companion();assert(confirmations==1);
    confirmations=0;
    s_companion_view="{\"draft\":{\"id\":\"abc\",\"state\":\"unconfirmed\","
        "\"recipient\":\"Test recipient\",\"text\":\"Unsupported \\ud83d\\ude00\"}}";
    fill_companion();assert(confirmations==0);
    s_companion_view="{\"draft\":{\"state\":\"sent\"}}";
    fill_companion();assert(confirmations==0);
    s_companion_view="{}";fill_companion();
    return 0;
}
'''
        json_start = settings.index("static const char *json_text(")
        json_end = settings.index("\n}", json_start) + 2
        source = source.replace(settings[start:end], settings[json_start:json_end] + "\n" + settings[start:end])
        cjson = ROOT / "managed_components/espressif__cjson/cJSON"
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            harness = folder / "check.c"
            harness.write_text(source)
            binary = folder / "check"
            command = [*shlex.split(os.environ.get("CC", "cc")), "-std=gnu11", "-Os", "-Wall", "-Wextra",
                       "-Werror", "-Wframe-larger-than=512", "-I", str(ROOT / "components/muse"),
                       "-I", str(cjson), str(harness), str(ROOT / "components/muse/muse_text.c"),
                       str(cjson / "cJSON.c"), "-o", str(binary)]
            build = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
