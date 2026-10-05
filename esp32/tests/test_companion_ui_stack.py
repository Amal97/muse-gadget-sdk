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
        settings = settings.replace("\nvoid muse_settings_ui_open_companion", "\nstatic void muse_settings_ui_open_companion")
        start = settings.index("static lv_obj_t *companion_note(")
        end = settings.index("\n}", start) + 2
        section_start = settings.index("typedef enum {\n    COMPANION_MENU")
        section_end = settings.index("} companion_section_t;", section_start) + len("} companion_section_t;")
        titles_start = settings.index("static const char *const COMPANION_TITLES[]")
        titles_end = settings.index("\n};", titles_start) + 3
        source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "cJSON.h"
#include "muse_text.h"
#include "muse_companion.h"
#define COLOR_DIM 0
#define COLOR_WARN 1
#define COLOR_TEXT 2
#define COLOR_DANGER 3
#define LV_OBJ_FLAG_HIDDEN 1
#define LV_LABEL_LONG_MODE_WRAP 0
#define LV_ANIM_OFF 0
#define LV_SYMBOL_RIGHT ">"
typedef struct { char text[2048]; } lv_obj_t;
typedef struct { void *data; } lv_event_t;
typedef int lv_font_t;
static const lv_font_t lv_font_montserrat_16;
static lv_obj_t object;
static lv_obj_t home_object,text_object;
static lv_obj_t *s_companion=&object,*s_home=&home_object,*s_text=&text_object,*s_current=&object;
''' + settings[section_start:section_end] + "\n" + settings[titles_start:titles_end] + r'''
static companion_section_t s_companion_section;
static int32_t s_companion_scroll[COMPANION_SECTION_COUNT];
static bool s_companion_restore_scroll;
static lv_obj_t *s_companion_title=&object;
static void fill_companion(void);
static char rows[64][96];
static int row_count;
static int switch_count;
static int32_t fake_scroll;
static int32_t fake_scroll_limit=1000,next_scroll_limit=1000;
static lv_obj_t *s_companion_list=&object,*s_timer_status,*s_replace_timer_text;
static lv_obj_t *s_replace_timer,*s_cancel_replace,*s_companion_status;
static char *s_companion_view;
static char s_companion_detail[2048],s_companion_shown[2048],s_draft_id[33],s_latest_job[33];
static char s_calendar_ids[32][257];
static char s_memory_ids[6][33];
static char s_forget_memory[33],last_action[64],last_memory[33];
static int64_t s_forget_memory_us,now_us=1;
static int s_memory_offset,commands,last_offset;
static unsigned s_companion_view_version,s_shown_replace_seconds,pending_timer;
static int64_t s_companion_refresh;
static int mac_refreshes,settings_opened,builds;
static lv_obj_t *s_tile;
static const char *first_snapshot;
typedef struct { int state; char detail[96]; } muse_hatch_status_t;
#define MUSE_HATCH_UNREACHABLE 1
static void muse_hatch_status(muse_hatch_status_t *status){status->state=0;}
muse_timer_state_t muse_timer_status(unsigned *seconds){*seconds=0;return MUSE_TIMER_OFF;}
unsigned muse_timer_pending(void){return pending_timer;}
bool muse_openai_companion_snapshot(char *out,size_t cap,unsigned *version){
    (void)out;(void)cap;(void)version;return false;
}
bool muse_openai_companion_busy(void){return false;}
bool muse_openai_companion_command(const char *text){(void)text;mac_refreshes++;return true;}
static void muse_ui_show_settings(void){settings_opened++;}
static void build_companion_page(lv_obj_t *tile){
    (void)tile;builds++;s_companion=&object;s_companion_view=(char *)first_snapshot;
}
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
static int32_t lv_obj_get_scroll_y(lv_obj_t *o){(void)o;return fake_scroll;}
static void lv_obj_clean(lv_obj_t *o){(void)o;}
static void lv_obj_add_flag(lv_obj_t *o,int f){assert(o);(void)f;}
static void lv_obj_remove_flag(lv_obj_t *o,int f){assert(o);(void)f;}
static void lv_obj_update_layout(lv_obj_t *o){(void)o;fake_scroll_limit=next_scroll_limit;}
static void lv_obj_scroll_to_view(lv_obj_t *o,int a){(void)o;(void)a;}
static void lv_obj_scroll_to_y(lv_obj_t *o,int y,int a){
    (void)o;(void)a;fake_scroll=y>fake_scroll_limit?fake_scroll_limit:y;
}
static void show(lv_obj_t *o){s_current=o;}
static void close_text(void){show(s_companion);}
static void set_text(lv_obj_t *o,const char *text){if(o)lv_label_set_text(o,text);}
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
CALLBACK(on_memory_add)
CALLBACK(on_calendar_lead)
CALLBACK(on_calendar_quiet)
static lv_obj_t *note(lv_obj_t *o,const char *t){(void)t;return o;}
static lv_obj_t *row(lv_obj_t *o,const char *i,const char *t,lv_obj_t **out,
                     void (*cb)(lv_event_t *),void *arg){
    (void)i;(void)cb;(void)arg;if(out)*out=o;
    assert(row_count<64);snprintf(rows[row_count++],sizeof(rows[0]),"%s",t);
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
    switch_count++;
}
static void *lv_event_get_user_data(lv_event_t *e){return e->data;}
static int64_t esp_timer_get_time(void){return now_us;}
static void muse_state_set_caption(const char *text){(void)text;}
static cJSON *action_body(const char *action){
    cJSON *body=cJSON_CreateObject();cJSON_AddStringToObject(body,"action",action);return body;
}
static void send_companion(cJSON *body){
    commands++;
    snprintf(last_action,sizeof(last_action),"%s",cJSON_GetObjectItem(body,"action")->valuestring);
    cJSON *id=cJSON_GetObjectItem(body,"id");
    if(cJSON_IsString(id))snprintf(last_memory,sizeof(last_memory),"%s",id->valuestring);
    cJSON *offset=cJSON_GetObjectItem(body,"offset");if(offset)last_offset=offset->valueint;
    cJSON_Delete(body);
}
''' + settings[start:end] + "\n" + function(settings, "on_memory_forget") + "\n" + function(settings, "on_memory_page") + "\n" + function(settings, "fill_personal") + "\n" + function(settings, "companion_select") + "\n" + function(settings, "on_companion_section") + "\n" + function(settings, "companion_menu_row") + "\n" + function(settings, "fill_companion") + "\n" + function(settings, "go_back") + "\n" + function(settings, "tick_companion") + "\n" + function(settings, "open_companion_section") + "\n" + function(settings, "muse_settings_ui_open_companion") + "\n" + function(settings, "muse_settings_ui_open_companion_reminders") + "\n" + function(settings, "muse_settings_ui_open_companion_briefing") + r'''
int main(void){
    static char long_text[2048];
    memset(long_text,'X',2047);long_text[2047]=0;
    companion_note(&object,long_text);
    assert(strlen(object.text)==2047 && !strcmp(object.text,long_text));
    companion_note(&object,"short");assert(!strcmp(object.text,"short"));
    s_companion_view="{}";fill_companion();
    assert(row_count==9 && !strcmp(rows[0],"Conversation") && !strcmp(rows[8],"Connection & costs"));
    assert(s_timer_status==NULL && s_replace_timer==NULL);
    row_count=0;s_companion_section=COMPANION_REPLIES;
    s_companion_view="{\"draft\":{\"id\":\"abc\",\"state\":\"unconfirmed\","
        "\"recipient\":\"Test recipient\",\"text\":\"Reviewed ASCII reply\"}}";
    fill_companion();assert(confirmations==1);row_count=0;
    confirmations=0;
    s_companion_view="{\"draft\":{\"id\":\"abc\",\"state\":\"unconfirmed\","
        "\"recipient\":\"Test recipient\",\"text\":\"Unsupported \\ud83d\\ude00\"}}";
    fill_companion();assert(confirmations==0);row_count=0;
    s_companion_view="{\"draft\":{\"state\":\"sent\"}}";
    fill_companion();assert(confirmations==0);row_count=0;
    s_companion_view="{}";fill_companion();row_count=0;
    s_companion_view="{\"conversation\":{\"persistent\":true},\"memory_count\":7,\"memory_offset\":6,"
        "\"memories\":[{\"id\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"text\":\"Explicit test memory\"}]}";
    s_companion_section=COMPANION_MEMORY;fill_companion();
    assert(s_memory_offset==6 && !strcmp(s_memory_ids[0],"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"));
    lv_event_t forget={.data=(void *)(intptr_t)0},previous={.data=(void *)(intptr_t)-6};
    on_memory_forget(&forget);assert(commands==0);
    now_us+=15000001;on_memory_forget(&forget);assert(commands==0);
    now_us++;on_memory_forget(&forget);
    assert(commands==1 && !strcmp(last_action,"memory_forget") &&
           !strcmp(last_memory,"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"));
    on_memory_forget(&forget);assert(commands==1);
    snprintf(s_memory_ids[0],sizeof(s_memory_ids[0]),"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");
    on_memory_forget(&forget);assert(commands==1);
    on_memory_page(&previous);assert(commands==2 && last_offset==0 && !strcmp(last_action,"memory_list"));
    s_memory_offset=0;on_memory_page(&previous);assert(commands==2);
    row_count=0;
    lv_event_t nav={.data=(void *)(intptr_t)COMPANION_TIMERS};
    fake_scroll=37;
    on_companion_section(&nav);
    assert(s_companion_section==COMPANION_TIMERS && s_timer_status && s_replace_timer);
    assert(!s_forget_memory[0]);
    assert(s_companion_scroll[COMPANION_MEMORY]==37 && fake_scroll==0);
    fake_scroll=99;row_count=0;go_back();
    assert(row_count==9 && s_timer_status==NULL && s_replace_timer_text==NULL && s_cancel_replace==NULL);
    assert(s_current==s_companion && s_companion_section==COMPANION_MENU);
    fake_scroll_limit=0;
    row_count=0;on_companion_section(&nav);assert(fake_scroll==99);
    row_count=0;fill_companion();assert(fake_scroll==99);
    s_current=s_text;go_back();assert(s_current==s_companion && s_companion_section==COMPANION_TIMERS);
    row_count=0;go_back();assert(s_companion_section==COMPANION_MENU);
    go_back();assert(s_current==s_home);
    for(int section=COMPANION_CONVERSATION;section<COMPANION_SECTION_COUNT;section++){
        row_count=0;switch_count=0;companion_select((companion_section_t)section);
        assert(s_companion_section==(companion_section_t)section);
        assert((s_timer_status!=NULL)==(section==COMPANION_TIMERS));
        if(section!=COMPANION_REPLIES)assert(confirmations==0);
        const int expected_rows[]={9,1,3,6,2,0,2,1,4,1,0};
        assert(row_count==expected_rows[section]);
        assert(switch_count==(section==COMPANION_CALENDARS?3:
                             section==COMPANION_BRIEFING?1:section==COMPANION_FAVOURITES?6:0));
        tick_companion();
    }
    assert(mac_refreshes>0);
    row_count=0;pending_timer=300;muse_settings_ui_open_companion(NULL);
    assert(s_companion_section==COMPANION_TIMERS && s_current==s_companion);
    pending_timer=0;s_companion=NULL;s_companion_view=NULL;
    first_snapshot="{\"draft\":{\"id\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"state\":\"unconfirmed\","
        "\"recipient\":\"Test recipient\",\"text\":\"Reviewed ASCII reply\"}}";
    row_count=0;muse_settings_ui_open_companion(NULL);
    assert(builds==1 && s_companion_section==COMPANION_REPLIES);
    row_count=0;muse_settings_ui_open_companion_reminders();
    assert(s_companion_section==COMPANION_REMINDERS);
    row_count=0;muse_settings_ui_open_companion_briefing("Briefing is building");
    assert(s_companion_section==COMPANION_BRIEFING);
    row_count=0;muse_settings_ui_open_companion("Weather details");
    assert(s_companion_section==COMPANION_DETAILS && settings_opened==5);
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
                       "-DCONFIG_MUSE_OPENCLAW=1",
                       "-Werror", "-Wframe-larger-than=512", "-I", str(ROOT / "components/muse"),
                       "-I", str(cjson), str(harness), str(ROOT / "components/muse/muse_text.c"),
                       str(cjson / "cJSON.c"), "-o", str(binary)]
            build = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
