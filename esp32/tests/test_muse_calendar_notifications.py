# SPDX-License-Identifier: Apache-2.0
"""Compile the actual calendar parsing/expiry path without Wi-Fi or an ESP32."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_cardputer_navigation import function
from test_muse_home import HomeTest
sys.path.pop(0)

ROOT = Path(__file__).resolve().parents[1]


class CalendarNotificationTest(unittest.TestCase):
    def test_confirmed_personal_reset_and_disabling_cached_calendar_alerts(self):
        native = (ROOT / "components/muse/muse_openai.c").read_text()
        start = native.index("static bool companion_result(")
        end = native.index("\n}", start) + 2
        source = r'''
#include <assert.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include "cJSON.h"
#include "muse_openai.h"
#define JSON_CAP 32768
#define TEXT_CAP 2048
#define portMAX_DELAY 0
typedef struct { unsigned generation; } job_t;
static int s_data=1;
static unsigned s_companion_version;
static char s_companion_json[JSON_CAP],s_notification_ack[33],s_copilot_focus[33];
static muse_notification_t s_notification,s_draft_notice;
static atomic_bool s_clear_history;
static bool same_generation=true;
static const char *response;
static void muse_state_set_caption(const char *format,...){(void)format;}
static void xSemaphoreTake(int s,int timeout){(void)s;(void)timeout;}
static void xSemaphoreGive(int s){(void)s;}
static bool current(unsigned generation){(void)generation;return same_generation;}
static size_t fake_strlcpy(char *out,const char *text,size_t cap){
    size_t n=strlen(text);if(cap){size_t k=n<cap-1?n:cap-1;memcpy(out,text,k);out[k]=0;}return n;
}
#undef strlcpy
#define strlcpy fake_strlcpy
static char *json_request(const job_t *job,const char *path,cJSON *body,bool speech,bool mac,char *why,size_t cap){
    (void)job;(void)path;(void)body;(void)speech;(void)mac;(void)why;(void)cap;
    return response?strdup(response):NULL;
}
''' + native[start:end] + r'''
int main(void){
    job_t job={0};char why[96];
    cJSON *body=cJSON_Parse("{\"action\":\"conversation_reset\"}");
    const char *invalid[]={NULL,"{}", "{\"conversation\":{\"id\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\","
        "\"turn_count\":1}}","{\"conversation\":{\"id\":\"invalid\",\"turn_count\":0}}",
        "{\"error\":{\"message\":\"Failed\"}}"};
    for(unsigned i=0;i<sizeof(invalid)/sizeof(invalid[0]);i++){
        response=invalid[i];assert(!companion_result(&job,body,why,sizeof(why)));
        assert(!atomic_load(&s_clear_history) && s_companion_version==0);
    }
    response="{\"conversation\":{\"id\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"turn_count\":0}}";
    same_generation=false;assert(!companion_result(&job,body,why,sizeof(why)));
    assert(!atomic_load(&s_clear_history));
    same_generation=true;assert(companion_result(&job,body,why,sizeof(why)));
    assert(atomic_load(&s_clear_history) && s_companion_version==1);
    atomic_store(&s_clear_history,false);
    cJSON_ReplaceItemInObject(body,"action",cJSON_CreateString("memory_forget"));
    assert(companion_result(&job,body,why,sizeof(why)) && atomic_load(&s_clear_history));
    atomic_store(&s_clear_history,false);
    cJSON_ReplaceItemInObject(body,"action",cJSON_CreateString("settings"));
    strlcpy(s_notification.id,"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",sizeof(s_notification.id));
    strlcpy(s_notification.kind,"calendar",sizeof(s_notification.kind));
    response="{\"settings\":{\"calendar_alerts_enabled\":false}}";
    assert(companion_result(&job,body,why,sizeof(why)));
    assert(!s_notification.id[0] && !strcmp(s_notification_ack,"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"));
    assert(!atomic_load(&s_clear_history));
    cJSON_Delete(body);
    body=cJSON_Parse("{\"action\":\"copilot_choice\",\"id\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\",\"option\":2}");
    strcpy(s_notification.id,"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");
    strcpy(s_copilot_focus,s_notification.id);
    response="{\"copilot\":{\"id\":\"cccccccccccccccccccccccccccccccc\",\"state\":\"answered\"}}";
    assert(!companion_result(&job,body,why,sizeof(why)) && s_notification.id[0] && s_copilot_focus[0]);
    response="{\"copilot\":{\"id\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\",\"state\":\"pending\"}}";
    assert(!companion_result(&job,body,why,sizeof(why)) && s_notification.id[0]);
    response="{\"copilot\":{\"id\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\",\"state\":\"answered\"}}";
    assert(companion_result(&job,body,why,sizeof(why)) && !s_notification.id[0] && !s_copilot_focus[0]);
    strcpy(s_notification.id,"cccccccccccccccccccccccccccccccc");
    strcpy(s_copilot_focus,s_notification.id);
    assert(companion_result(&job,body,why,sizeof(why)));
    assert(!strcmp(s_notification.id,"cccccccccccccccccccccccccccccccc") && s_copilot_focus[0]);
    cJSON_Delete(body);
    return 0;
}
'''
        HomeTest().compile_case(source)

    def test_strict_epoch_expiry_ack_sleep_and_timer_priority(self):
        native = (ROOT / "components/muse/muse_openai.c").read_text()
        start = native.index("bool muse_openai_notification(")
        end = native.index("\n}", start) + 2
        source = r'''
#include <assert.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "cJSON.h"
#include "muse_companion.h"
#include "muse_openai.h"
#define TAG "test"
#define ESP_LOGI(tag,...) ((void)(tag))
#define ESP_LOGW(tag,...) ((void)(tag),warnings++)
#define portMAX_DELAY 0
#define MUSE_MODE_IDLE 0
#define JOB_NOTIFICATION 0
typedef struct { int kind; unsigned generation; } job_t;
static int s_data=1,warnings,requests;
static atomic_uint s_generation;
static atomic_bool s_busy;
static muse_notification_t s_notification,s_draft_notice;
static char s_notification_ack[33],s_reply_arm[33],sent_ack[33];
static bool asleep,online=true,dismiss_during_request;
static bool watch;
static unsigned replacement;
static muse_timer_state_t timer_state;
static time_t clock_time=1700000000;
static const char *response;
static time_t fake_time(time_t *out){if(out)*out=clock_time;return clock_time;}
#define time fake_time
static void xSemaphoreTake(int s,int timeout){(void)s;(void)timeout;}
static void xSemaphoreGive(int s){(void)s;}
unsigned muse_timer_pending(void){return replacement;}
muse_timer_state_t muse_timer_status(unsigned *out){if(out)*out=0;return timer_state;}
static bool openclaw_chat(void){return true;}
static bool muse_settings_openclaw_token_set(void){return true;}
static bool muse_settings_copilot_watch(void){return watch;}
static void muse_state_set_asleep(bool value){asleep=value;}
static bool muse_wifi_connected(void){return online;}
static bool muse_state_asleep(void){return asleep;}
static bool muse_state_on_battery(void){return true;}
static int muse_state_mode(float *out){(void)out;return MUSE_MODE_IDLE;}
static bool current(unsigned generation){return generation==atomic_load(&s_generation);}
static size_t fake_strlcpy(char *out,const char *text,size_t cap){
    size_t n=strlen(text);if(cap){size_t k=n<cap-1?n:cap-1;memcpy(out,text,k);out[k]=0;}return n;
}
#undef strlcpy
#define strlcpy fake_strlcpy
static char *json_request(const job_t *job,const char *path,cJSON *body,bool speech,bool mac,char *why,size_t cap){
    (void)job;(void)path;(void)speech;(void)mac;(void)why;(void)cap;requests++;
    strlcpy(sent_ack,cJSON_GetObjectItemCaseSensitive(body,"ack")->valuestring,sizeof(sent_ack));
    if(dismiss_during_request){
        strlcpy(s_notification_ack,s_notification.id,sizeof(s_notification_ack));
        memset(&s_notification,0,sizeof(s_notification));
    }
    return strdup(response);
}
''' + native[start:end] + "\n" + function(native, "poll_notifications") + r'''
int main(void){
    muse_notification_t out;
    response="{\"notification\":{\"id\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\","
        "\"kind\":\"calendar\",\"sender\":\"Calendar\",\"preview\":\"Upcoming\",\"body\":\"Full event\","
        "\"expires_at\":1700000600}}";
    asleep=true;poll_notifications();assert(requests==0);
    asleep=false;online=false;poll_notifications();assert(requests==0);
    online=true;poll_notifications();assert(requests==1);
    assert(muse_openai_notification(&out) && !strcmp(out.kind,"calendar"));
    assert(out.expires_at==1700000600 && !strcmp(out.body,"Full event"));
    response="{\"notification\":{\"id\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\","
        "\"kind\":\"copilot_allow\",\"sender\":\"Copilot approval\",\"preview\":\"Review work\","
        "\"body\":\"Workspace: /safe/project\\nprintf harmless\","
        "\"expires_at\":1700000600,\"respondable\":true,\"choices\":[],\"allow_freeform\":false}}";
    strlcpy(s_reply_arm,"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",sizeof(s_reply_arm));
    watch=true;asleep=true;poll_notifications();
    assert(!asleep && !s_reply_arm[0]);
    assert(muse_openai_notification(&out) && !strcmp(out.kind,"copilot_allow") && out.respondable);
    int before=requests;poll_notifications();assert(requests==before+1);
    response="{\"notification\":{\"id\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\","
        "\"kind\":\"copilot_ask\",\"sender\":\"Copilot question\",\"preview\":\"Question\","
        "\"body\":\"Full question\",\"expires_at\":1700000600}}";
    int old_warnings=warnings;poll_notifications();assert(warnings==old_warnings+1);
    response="{\"notification\":{\"id\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\","
        "\"kind\":\"copilot_allow\",\"sender\":\"Copilot approval\",\"preview\":\"Review work\","
        "\"body\":\"hidden \\u202ecommand\",\"expires_at\":1700000600,\"respondable\":true}}";
    old_warnings=warnings;poll_notifications();assert(warnings==old_warnings+1);
    response="{\"notification\":{\"id\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\","
        "\"kind\":\"copilot_ask\",\"sender\":\"Copilot question\",\"preview\":\"Question\","
        "\"body\":\"Full question\",\"expires_at\":1700000600,\"respondable\":true,"
        "\"choices\":[\"Brief\",\"Detailed\",\"Third\"],\"allow_freeform\":true}}";
    poll_notifications();assert(muse_openai_notification(&out));
    assert(out.choice_count==3 && out.allow_freeform && !strcmp(out.choices[1],"Detailed"));
    const char *bad_choices[]={"{}", "[1]", "[\"hidden\\u202echoice\"]", "[\"\"]"};
    for(unsigned i=0;i<sizeof(bad_choices)/sizeof(bad_choices[0]);i++){
        char payload[1024];snprintf(payload,sizeof(payload),
            "{\"notification\":{\"id\":\"cccccccccccccccccccccccccccccccc\","
            "\"kind\":\"copilot_ask\",\"sender\":\"Copilot question\",\"preview\":\"Question\","
            "\"body\":\"Question\",\"expires_at\":1700000600,\"respondable\":true,"
            "\"choices\":%s,\"allow_freeform\":true}}",bad_choices[i]);
        response=payload;old_warnings=warnings;poll_notifications();
        assert(warnings==old_warnings+1 && !strcmp(s_notification.id,"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"));
    }
    watch=false;
    response="{\"notification\":null}";poll_notifications();
    assert(!muse_openai_notification(&out));
    response="{\"notification\":{\"id\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\","
        "\"kind\":\"calendar\",\"sender\":\"Calendar\",\"preview\":\"Upcoming\","
        "\"expires_at\":1700000600}}";
    poll_notifications();assert(muse_openai_notification(&out));
    dismiss_during_request=true;poll_notifications();
    assert(!muse_openai_notification(&out) && s_notification_ack[0]);
    dismiss_during_request=false;response="{\"notification\":null}";poll_notifications();
    assert(!s_notification_ack[0]);
    response="{\"notification\":{\"id\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\","
        "\"kind\":\"calendar\",\"sender\":\"Calendar\",\"preview\":\"Upcoming\","
        "\"expires_at\":1700000600}}";
    poll_notifications();assert(muse_openai_notification(&out));
    clock_time=1700000599;assert(muse_openai_notification(&out));
    timer_state=MUSE_TIMER_RINGING;clock_time=1700000600;
    assert(muse_openai_notification(&out) && !strcmp(out.kind,"timer"));
    assert(s_notification_ack[0]==0);
    timer_state=MUSE_TIMER_OFF;assert(!muse_openai_notification(&out));
    assert(!strcmp(s_notification_ack,"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"));
    response="{\"notification\":null}";poll_notifications();
    assert(!strcmp(sent_ack,"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") && !s_notification_ack[0]);
    old_warnings=warnings;
    const char *invalid[]={
        "{\"notification\":{\"id\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\",\"kind\":\"calendar\","
        "\"sender\":\"Calendar\",\"preview\":\"Upcoming\"}}",
        "{\"notification\":{\"id\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\",\"kind\":\"calendar\","
        "\"sender\":\"Calendar\",\"preview\":\"Upcoming\",\"expires_at\":1700000800.5}}",
        "{\"notification\":{\"id\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\",\"kind\":\"calendar\","
        "\"sender\":\"Calendar\",\"preview\":\"Upcoming\",\"expires_at\":1e100}}"
    };
    for(unsigned i=0;i<sizeof(invalid)/sizeof(invalid[0]);i++){
        response=invalid[i];poll_notifications();assert(!s_notification.id[0]);
    }
    assert(warnings==old_warnings+3);
    response="{\"notification\":{\"id\":\"cccccccccccccccccccccccccccccccc\","
        "\"kind\":\"imessage\",\"sender\":\"Test\",\"preview\":\"Unchanged message behavior\"}}";
    poll_notifications();assert(muse_openai_notification(&out) && out.expires_at==0);
    clock_time+=10000;assert(muse_openai_notification(&out));
    replacement=60;assert(muse_openai_notification(&out) && !strcmp(out.kind,"timer_replace"));
    return 0;
}
'''
        HomeTest().compile_case(source)
