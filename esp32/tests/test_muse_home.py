# SPDX-License-Identifier: Apache-2.0
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_cardputer_navigation import function
sys.path.pop(0)


class HomeTest(unittest.TestCase):
    def test_home_cards_open_their_companion_sections(self):
        ui = (ROOT / "components/muse/muse_ui.c").read_text()
        source = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "muse_home.h"
typedef struct { intptr_t data; } lv_event_t;
typedef struct { int state; } muse_wifi_status_t;
#define MUSE_WIFI_CONNECTED 1
static cJSON *s_home_data;
static int destination,build_requests;
static char detail_text[1024];
static void *lv_event_get_user_data(lv_event_t *event){return (void *)event->data;}
static void muse_wifi_status(muse_wifi_status_t *status){status->state=MUSE_WIFI_CONNECTED;}
static int muse_settings_home_offset(void){return 0;}
static void muse_settings_ui_open_companion(const char *text){
    destination=1;snprintf(detail_text,sizeof(detail_text),"%s",text);
}
static void muse_settings_ui_open_companion_reminders(void){destination=2;}
static void muse_settings_ui_open_companion_briefing(const char *text){
    destination=3;snprintf(detail_text,sizeof(detail_text),"%s",text);
}
static bool muse_openai_companion_command(const char *text){(void)text;build_requests++;return true;}
static void muse_state_set_caption(const char *text){(void)text;}
''' + function(ui, "home_detail") + r'''
int main(void){
    s_home_data=cJSON_Parse("{}");lv_event_t event={0};
    home_detail(&event);assert(destination==1 && strstr(detail_text,"WEATHER"));
    event.data=1;home_detail(&event);assert(destination==2);
    event.data=2;home_detail(&event);assert(destination==3 && build_requests==1);
    cJSON_Delete(s_home_data);
    s_home_data=cJSON_Parse("{\"briefing\":{\"state\":\"ready\",\"body\":\"Test daily digest\"}}");
    home_detail(&event);
    assert(destination==3 && !strcmp(detail_text,"Test daily digest") && build_requests==1);
    cJSON_Delete(s_home_data);
    return 0;
}
'''
        self.compile_case(source)

    def compile_case(self, source):
        cjson = ROOT / "managed_components/espressif__cjson/cJSON"
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            harness = folder / "check.c"
            harness.write_text(source)
            binary = folder / "check"
            command = [*shlex.split(os.environ.get("CC", "cc")), "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                       "-I", str(ROOT / "components/muse"), "-I", str(cjson),
                       str(harness), str(ROOT / "components/muse/muse_home.c"), str(cjson / "cJSON.c"),
                       "-o", str(binary)]
            build = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_real_tile_callbacks_distinguish_manual_and_automatic_changes(self):
        ui = (ROOT / "components/muse/muse_ui.c").read_text()
        source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include "muse_home.h"
#define ESP_OK 0
#define TAG "test"
#define ESP_LOGI(tag,...) ((void)(tag))
#define LV_EVENT_SCROLL_BEGIN 1
#define LV_EVENT_VALUE_CHANGED 2
#define LV_OBJ_FLAG_SCROLLABLE 1
typedef int lv_obj_t;
typedef int lv_anim_enable_t;
typedef int lv_event_code_t;
typedef struct { int code; void *param; } lv_event_t;
static lv_obj_t home, face, settings, tileview, input;
static lv_obj_t *s_home=&home, *s_face=&face, *s_settings=&settings, *s_tv=&tileview;
static lv_obj_t *s_indev=&input, *active=&home;
static bool s_home_manual_scroll, s_home_selecting, fail_save;
static muse_home_navigation_t s_home_navigation;
static int saves, errors;
static lv_event_code_t lv_event_get_code(lv_event_t *e) { return e->code; }
static void *lv_event_get_param(lv_event_t *e) { return e->param; }
static void *lv_indev_active(void) { return s_indev; }
static lv_obj_t *lv_tileview_get_tile_active(lv_obj_t *t) { (void)t; return active; }
static int muse_settings_set_home_face(bool selected) { (void)selected; saves++; return fail_save; }
static void muse_state_set_caption(const char *caption) { (void)caption; errors++; }
static void lv_obj_add_flag(lv_obj_t *obj, int flag) { (void)obj; (void)flag; }
static void lv_tileview_set_tile(lv_obj_t *obj,lv_obj_t *tile,lv_anim_enable_t animation);
''' + function(ui, "home_navigation") + "\n" + function(ui, "home_select") + r'''
static void lv_tileview_set_tile(lv_obj_t *obj,lv_obj_t *tile,lv_anim_enable_t animation) {
    (void)obj; (void)animation; active=tile;
    lv_event_t event={LV_EVENT_SCROLL_BEGIN,NULL}; home_navigation(&event);
}
int main(void) {
    lv_event_t begin={LV_EVENT_SCROLL_BEGIN,NULL}, end={LV_EVENT_VALUE_CHANGED,NULL};
    s_home_navigation.preferred=MUSE_HOME_DASHBOARD;
    home_select(s_face,1); home_navigation(&end);
    assert(saves==0 && s_home_navigation.preferred==MUSE_HOME_DASHBOARD);
    home_navigation(&begin); active=s_face; home_navigation(&end);
    assert(saves==1 && s_home_navigation.preferred==MUSE_HOME_FACE);
    muse_home_borrow(&s_home_navigation); home_select(s_face,1); home_navigation(&end);
    assert(saves==1 && muse_home_return(&s_home_navigation)==MUSE_HOME_FACE);
    muse_home_borrow(&s_home_navigation);
    home_navigation(&begin); active=s_home; home_navigation(&end);
    assert(saves==2 && s_home_navigation.preferred==MUSE_HOME_DASHBOARD);
    assert(muse_home_return(&s_home_navigation)==MUSE_HOME_KEEP);
    muse_home_borrow(&s_home_navigation);
    home_navigation(&begin); active=s_settings; home_navigation(&end);
    assert(saves==2 && s_home_navigation.preferred==MUSE_HOME_DASHBOARD);
    assert(muse_home_return(&s_home_navigation)==MUSE_HOME_KEEP);
    fail_save=true;
    home_navigation(&begin); active=s_face; home_navigation(&end);
    assert(errors==1);
    return 0;
}
'''
        self.compile_case(source)

    def test_production_preferences_survive_reload_and_report_failed_saves(self):
        settings = (ROOT / "components/muse/muse_settings.c").read_text()
        setters = settings[settings.index("bool muse_settings_home_face"):settings.index("/* Home Link owns")]
        load = settings[settings.index("    esp_err_t home_err"):settings.index("    s.volume = clampi")]
        source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "muse_home.h"
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_NVS_NOT_FOUND 2
#define ESP_ERR_INVALID_ARG 3
#define ESP_LOGE(tag,...) ((void)(tag))
#define ESP_LOGW(tag,...) ((void)(tag))
#define LOCKED(body) do { body; } while(0)
static const char *TAG="test";
static int s_nvs;
static struct { bool home_face; int32_t home_offset; } s={false,INT32_MAX};
static bool has_face, has_offset, fail_write, fail_commit;
static uint8_t stored_face, pending_face;
static int32_t stored_offset, pending_offset;
static int writes;
static const char *esp_err_to_name(int err) { (void)err; return "failed"; }
static int nvs_set_u8(int h,const char *key,uint8_t v) {
    (void)h;(void)key;writes++;pending_face=v;return fail_write;
}
static int nvs_set_i32(int h,const char *key,int32_t v) {
    (void)h;(void)key;writes++;pending_offset=v;return fail_write;
}
static int nvs_commit(int h) {
    (void)h;if(fail_commit)return 1;
    has_face=has_offset=true;stored_face=pending_face;stored_offset=pending_offset;return 0;
}
static int nvs_get_u8(int h,const char *key,uint8_t *v) {
    (void)h;(void)key;*v=stored_face;return has_face?0:ESP_ERR_NVS_NOT_FOUND;
}
static int nvs_get_i32(int h,const char *key,int32_t *v) {
    (void)h;(void)key;*v=stored_offset;return has_offset?0:ESP_ERR_NVS_NOT_FOUND;
}
''' + setters + "\nstatic void reload(void) {uint8_t b;\n" + load + r'''
}
int main(void) {
    (void)esp_err_to_name;
    reload(); assert(!muse_settings_home_face() && muse_settings_home_offset()==INT32_MAX);
    assert(muse_settings_set_home_face(true)==0);
    assert(muse_settings_set_home_offset(46800)==0);
    s.home_face=false;s.home_offset=INT32_MAX;reload();
    assert(muse_settings_home_face() && muse_settings_home_offset()==46800);
    int before=writes;
    assert(muse_settings_set_home_face(true)==0 && muse_settings_set_home_offset(46800)==0);
    assert(writes==before);
    fail_commit=true;
    assert(muse_settings_set_home_face(false)!=0 && muse_settings_home_face());
    assert(muse_settings_set_home_offset(43200)!=0 && muse_settings_home_offset()==46800);
    fail_commit=false;fail_write=true;
    assert(muse_settings_set_home_face(false)!=0 && muse_settings_home_face());
    assert(muse_settings_set_home_offset(1)==ESP_ERR_INVALID_ARG);
    stored_face=7;stored_offset=1;s.home_face=false;s.home_offset=INT32_MAX;reload();
    assert(!muse_settings_home_face() && muse_settings_home_offset()==INT32_MAX);
    return 0;
}
'''
        self.compile_case(source)

    def test_production_navigation_clock_cache_and_utf8_previews(self):
        cjson = ROOT / "managed_components/espressif__cjson/cJSON"
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            harness = folder / "home.c"
            harness.write_text(r'''
#include <assert.h>
#include <string.h>
#include "muse_home.h"
int main(void) {
    muse_home_navigation_t nav = {.preferred=MUSE_HOME_DASHBOARD};
    muse_home_borrow(&nav);
    assert(muse_home_return(&nav)==MUSE_HOME_DASHBOARD);
    assert(muse_home_return(&nav)==MUSE_HOME_KEEP);
    muse_home_choose(&nav,MUSE_HOME_FACE);
    muse_home_borrow(&nav);
    assert(muse_home_return(&nav)==MUSE_HOME_FACE);
    muse_home_borrow(&nav);
    muse_home_choose(&nav,MUSE_HOME_DASHBOARD);
    assert(nav.preferred==MUSE_HOME_DASHBOARD && muse_home_return(&nav)==MUSE_HOME_KEEP);
    muse_home_borrow(&nav);
    muse_home_choose(&nav,MUSE_HOME_SETTINGS);
    assert(nav.preferred==MUSE_HOME_DASHBOARD && muse_home_return(&nav)==MUSE_HOME_KEEP);
    cJSON *root=cJSON_Parse("{\"clock\":{\"utc_offset_seconds\":46800},"
      "\"served_at\":1700000000,\"reminders\":[{\"title\":\"Stretch\",\"due\":1700000060}],"
      "\"reminder_count\":1,\"weather\":{\"state\":\"ready\",\"updated_at\":1700000000,"
      "\"temperature\":18.5,\"code\":2,\"low\":12,\"high\":21,\"rain_probability\":25},"
      "\"briefing\":{\"summary\":\"All day Planning\",\"date\":\"2023-11-15T08:00:00+13:00\"}}");
    assert(root);
    int32_t offset=INT32_MAX;
    assert(muse_home_clock_offset(root,&offset) && offset==46800);
    muse_home_view_t view;
    muse_home_format(&view,root,1700000000,offset,true);
    assert(!strcmp(view.time,"11:13"));
    assert(!strcmp(view.date,"Wed, 15 Nov"));
    assert(strstr(view.weather,"Partly cloudy") && strstr(view.weather_detail,"Rain 25%"));
    assert(!strcmp(view.reminder,"Stretch") && strstr(view.reminder_detail,"11:14"));
    assert(!strcmp(view.briefing,"All day Planning"));
    assert(!strcmp(view.footer,"Live from your Mac"));
    muse_home_format(&view,root,1700001300,offset,true);
    assert(strstr(view.weather_detail,"Cached") && strstr(view.footer,"Mac offline"));
    muse_home_format(&view,root,1700086400,offset,false);
    assert(strstr(view.briefing,"Previous briefing") && strstr(view.footer,"Wi-Fi offline"));
    muse_home_format(&view,root,1700000000,-43200,true);
    assert(!strcmp(view.date,"Tue, 14 Nov") && !strcmp(view.time,"10:13"));
    muse_home_format(&view,root,0,offset,true);
    assert(!strcmp(view.time,"--:--") && strstr(view.date,"Waiting"));
    muse_home_format(&view,NULL,1700000000,INT32_MAX,true);
    assert(!strcmp(view.time,"--:--") && strstr(view.reminder,"Syncing"));
    const char *bad[]={"{\"clock\":{\"utc_offset_seconds\":true}}",
                      "{\"clock\":{\"utc_offset_seconds\":50460}}",
                      "{\"clock\":{\"utc_offset_seconds\":0.5}}","{}"};
    for(unsigned i=0;i<sizeof(bad)/sizeof(bad[0]);i++){
        cJSON *invalid=cJSON_Parse(bad[i]);
        assert(!muse_home_clock_offset(invalid,&offset)); cJSON_Delete(invalid);
    }
    cJSON *reminders=cJSON_GetObjectItemCaseSensitive(root,"reminders");
    cJSON *first=cJSON_GetArrayItem(reminders,0);
    char unicode[601];
    for(int i=0;i<200;i++) memcpy(unicode+i*3,"\xe2\x82\xac",3);
    unicode[600]=0;
    cJSON_ReplaceItemInObjectCaseSensitive(first,"title",cJSON_CreateString(unicode));
    muse_home_format(&view,root,1700000000,46800,true);
    assert(strlen(view.reminder)==174);
    assert(!strcmp(view.reminder+171,"..."));
    cJSON_Delete(root);
    return 0;
}
''')
            binary = folder / "home"
            command = [*shlex.split(os.environ.get("CC", "cc")), "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                       "-I", str(ROOT / "components/muse"), "-I", str(cjson),
                       str(harness), str(ROOT / "components/muse/muse_home.c"), str(cjson / "cJSON.c"),
                       "-o", str(binary)]
            build = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
