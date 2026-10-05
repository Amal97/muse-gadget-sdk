# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_cardputer_navigation import function
import test_muse_home
sys.path.pop(0)

ROOT = Path(__file__).resolve().parents[1]


class NotificationUITest(unittest.TestCase):
    def test_popup_expands_in_place_scrolls_resets_and_pauses_expiry(self):
        ui = (ROOT / "components/muse/muse_ui.c").read_text()
        source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cJSON.h"
#include "muse_openai.h"
#define TAG "test"
#define ESP_LOGI(tag,...) ((void)(tag))
#define LV_MAX(a,b) ((a)>(b)?(a):(b))
#define LV_OBJ_FLAG_SCROLLABLE 1
#define LV_OBJ_FLAG_HIDDEN 2
#define LV_SCROLLBAR_MODE_AUTO 1
#define LV_SCROLLBAR_MODE_OFF 0
#define LV_LABEL_LONG_MODE_WRAP 1
#define LV_LABEL_LONG_DOT 0
#define LV_ALIGN_CENTER 0
#define LV_SIZE_CONTENT -1
#define LV_ANIM_OFF 0
#define MUSE_MODE_IDLE 0
#define MUSE_MODE_THINKING 1
typedef int muse_mode_t;
typedef struct { int unused; } lv_event_t;
typedef struct { int w,h,flags,mode,scroll_y; char text[2048]; } lv_obj_t;
static lv_obj_t backdrop,card,sender,preview,details,dismiss,dismiss_label,action,action_label,hint,stop;
static lv_obj_t *s_notification_backdrop=&backdrop;
static lv_obj_t *s_notification_card=&card,*s_notification_sender=&sender,*s_notification_preview=&preview;
static lv_obj_t *s_notification_details=&details,*s_notification_dismiss=&dismiss,*s_notification_action=&action;
static lv_obj_t *s_notification_action_label=&action_label,*s_notification_hint=&hint,*s_stop_job=&stop;
static lv_obj_t *s_notification_dismiss_label=&dismiss_label;
static int s_w=466,s_h=466;
static bool s_small,s_notification_expanded,available=true;
static char s_notification_text[2048],s_notification_id[33],s_copilot_chimed[33],focused[33];
static muse_notification_t s_shown_notification,incoming;
static float s_notification_seconds,s_notification_tick,s_alarm_chirp;
static int dismissed,pokes,navigations,chirps;
static bool speaker;
static void lv_obj_set_size(lv_obj_t *o,int w,int h){o->w=w;o->h=h;}
static void lv_obj_align(lv_obj_t *o,int a,int x,int y){(void)o;(void)a;(void)x;(void)y;}
static void lv_obj_set_height(lv_obj_t *o,int h){o->h=h;}
static void lv_obj_set_width(lv_obj_t *o,int w){o->w=w;}
static void lv_obj_set_flag(lv_obj_t *o,int flag,bool enabled){if(enabled)o->flags|=flag;else o->flags&=~flag;}
static void lv_obj_add_flag(lv_obj_t *o,int flag){o->flags|=flag;}
static void lv_obj_remove_flag(lv_obj_t *o,int flag){o->flags&=~flag;}
static bool lv_obj_has_flag(lv_obj_t *o,int flag){return (o->flags&flag)!=0;}
static void lv_obj_set_scrollbar_mode(lv_obj_t *o,int mode){(void)o;(void)mode;}
static void lv_label_set_long_mode(lv_obj_t *o,int mode){o->mode=mode;}
static void lv_obj_scroll_to_y(lv_obj_t *o,int y,int animation){(void)animation;o->scroll_y=y;}
static const char *lv_label_get_text(lv_obj_t *o){return o->text;}
static void lv_label_set_text(lv_obj_t *o,const char *text){snprintf(o->text,sizeof(o->text),"%s",text);}
static const char *muse_text_showable(const char *s,char *b,size_t n){(void)b;(void)n;return s;}
static void home_set_text(lv_obj_t *o,const char *s){lv_label_set_text(o,s);}
static void muse_state_poke(void){pokes++;}
static bool muse_settings_speaker_on(void){return speaker;}
static void muse_voice_request_chirp(void){chirps++;}
void muse_openai_copilot_focus(const char *id){snprintf(focused,sizeof(focused),"%s",id?id:"");}
static size_t fake_strlcpy(char *out,const char *text,size_t cap){
    size_t n=strlen(text);if(cap)snprintf(out,cap,"%s",text);return n;
}
#undef strlcpy
#define strlcpy fake_strlcpy
bool muse_openai_job_active(void){return false;}
bool muse_openai_notification(muse_notification_t *out){*out=incoming;return available;}
void muse_settings_ui_open_companion(const char *text){(void)text;navigations++;}
bool muse_openai_reply_begin(const char *id){(void)id;return true;}
bool muse_timer_snooze(unsigned seconds){(void)seconds;return false;}
bool muse_openai_companion_command(const char *text){(void)text;return false;}
static void muse_state_set_caption(const char *format,...){(void)format;}
static void dismiss_notification(lv_event_t *e){(void)e;dismissed++;available=false;}
static void notification_content(void);
''' + "\n".join(function(ui, name) for name in (
            "notification_layout", "notification_content", "open_notification", "notification_action", "update_notification")) + r'''
int main(void){
    strcpy(incoming.id,"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
    strcpy(incoming.kind,"imessage");strcpy(incoming.sender,"Sender");
    strcpy(incoming.preview,"Preview");
    memset(incoming.body,'X',2047);incoming.body[2047]=0;
    update_notification(MUSE_MODE_IDLE,1,true);
    assert(card.h==233 && details.h==85);
    assert(!strcmp(preview.text,"Preview"));
    open_notification(NULL);
    assert(s_notification_expanded && card.w==340 && card.h==302);
    assert(dismiss.w==138 && action.w==138);
    assert(!(backdrop.flags&LV_OBJ_FLAG_HIDDEN));
    assert(details.flags&LV_OBJ_FLAG_SCROLLABLE);
    assert(preview.h==LV_SIZE_CONTENT && preview.mode==LV_LABEL_LONG_MODE_WRAP);
    assert(strlen(preview.text)==2047 && !strcmp(preview.text,incoming.body));
    assert(navigations==0 && pokes==1);
    details.scroll_y=100;
    update_notification(MUSE_MODE_IDLE,101,true);
    assert(dismissed==0 && s_notification_seconds==0 && details.scroll_y==100);
    strcpy(incoming.body,"Updated details");
    update_notification(MUSE_MODE_IDLE,102,true);
    assert(!strcmp(preview.text,"Updated details") && s_notification_expanded);
    open_notification(NULL);
    assert(!s_notification_expanded && details.scroll_y==0);
    assert(backdrop.flags&LV_OBJ_FLAG_HIDDEN);
    assert(!(details.flags&LV_OBJ_FLAG_SCROLLABLE) && preview.mode==LV_LABEL_LONG_DOT);
    assert(!strcmp(preview.text,"Preview") && s_notification_seconds==0);
    update_notification(MUSE_MODE_IDLE,103,true);
    assert(dismissed==0);
    update_notification(MUSE_MODE_IDLE,119,true);
    assert(dismissed==1);
    available=true;
    strcpy(incoming.id,"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");
    strcpy(incoming.kind,"job");
    update_notification(MUSE_MODE_IDLE,120,true);open_notification(NULL);
    strcpy(incoming.id,"cccccccccccccccccccccccccccccccc");
    strcpy(incoming.kind,"timer");incoming.body[0]=0;
    strcpy(incoming.preview,"Time is up.");
    update_notification(MUSE_MODE_IDLE,121,true);
    assert(!s_notification_expanded);
    open_notification(NULL);
    assert(s_notification_expanded && !strcmp(preview.text,"Time is up."));
    available=false;update_notification(MUSE_MODE_IDLE,122,true);
    assert(!s_notification_expanded && lv_obj_has_flag(&card,LV_OBJ_FLAG_HIDDEN));
    assert(backdrop.flags&LV_OBJ_FLAG_HIDDEN);
    available=true;strcpy(incoming.kind,"reply_wait");
    update_notification(MUSE_MODE_IDLE,123,true);open_notification(NULL);
    assert(strstr(hint.text,"hold Talk to reply"));
    update_notification(MUSE_MODE_THINKING,124,true);
    assert(!s_notification_expanded && lv_obj_has_flag(&card,LV_OBJ_FLAG_HIDDEN));
    assert(navigations==0);
    speaker=true;
    strcpy(incoming.id,"dddddddddddddddddddddddddddddddd");
    strcpy(incoming.kind,"copilot_allow");incoming.respondable=true;
    strcpy(incoming.body,"Workspace: /safe/project\nprintf harmless");
    update_notification(MUSE_MODE_IDLE,125,false);
    assert(s_notification_expanded && !(card.flags&LV_OBJ_FLAG_HIDDEN));
    assert(!strcmp(preview.text,incoming.body) && !strcmp(dismiss_label.text,"Deny"));
    assert(!strcmp(focused,incoming.id) && strstr(hint.text,"approve / deny") && chirps==1);
    notification_layout(false);notification_action(NULL);
    assert(s_notification_expanded && navigations==0);
    update_notification(MUSE_MODE_THINKING,126,false);
    assert(!focused[0] && card.flags&LV_OBJ_FLAG_HIDDEN && chirps==1);
    strcpy(incoming.id,"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee");strcpy(incoming.kind,"timer");
    update_notification(MUSE_MODE_IDLE,127,true);assert(chirps==2 && !focused[0]);
    strcpy(incoming.id,"dddddddddddddddddddddddddddddddd");strcpy(incoming.kind,"copilot_allow");
    update_notification(MUSE_MODE_IDLE,128,false);assert(chirps==2 && focused[0]);
    speaker=false;strcpy(incoming.id,"ffffffffffffffffffffffffffffffff");strcpy(incoming.kind,"copilot_ask");
    update_notification(MUSE_MODE_IDLE,129,false);
    assert(!strcmp(dismiss_label.text,"Skip") && chirps==2 && strstr(hint.text,"option number"));
    s_small=true;s_w=s_h=240;
    notification_layout(false);
    assert(card.h==96 && details.h==32 && dismiss.w==56);
    notification_layout(true);
    assert(card.w==232 && card.h==232 && details.h==168);
    return 0;
}
'''
        test_muse_home.HomeTest().compile_case(source)
