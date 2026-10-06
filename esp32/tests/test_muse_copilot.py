# SPDX-License-Identifier: Apache-2.0
"""Execute the production recording binding and voice-worker routing on the host."""
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_muse_home as host_tests
sys.path.pop(0)

ROOT = Path(__file__).resolve().parents[1]


class CopilotNativeTest(unittest.TestCase):
    def test_recording_keeps_exact_displayed_request_and_never_calls_openclaw(self):
        native = (ROOT / "components/muse/muse_openai.c").read_text()
        types_start = native.index("typedef enum { JOB_VOICE")
        types_end = native.index("} job_t;", types_start) + len("} job_t;")
        def implementation(name):
            start = re.search(r"^(?:static )?(?:void|bool|size_t|char\s*\*)\s*" + re.escape(name) + r"\(",
                              native, re.MULTILINE).start()
            end = native.index("\n}", start) + 2
            return native[start:end]
        source = r'''
#include <assert.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <setjmp.h>
#include "cJSON.h"
#include "muse_openai.h"
#include "muse_companion.h"
#define CONFIG_MUSE_OPENCLAW 1
#define CONFIG_MUSE_OPENAI_CHAT_MODEL "test"
#define JSON_CAP 32768
#define TEXT_CAP 2048
#define HISTORY_PAIRS 4
#define RECORD_FRAMES 32
#define MALLOC_CAP_SPIRAM 0
#define MALLOC_CAP_8BIT 0
#define HTTP_METHOD_GET 0
#define MUSE_HATCH_REACHABLE 1
#define MUSE_HATCH_TESTING 2
#define MUSE_HATCH_UNREACHABLE 3
#define MUSE_HATCH_EV_HEARD 1
#define MUSE_HATCH_EV_REPLY 2
#define MUSE_HATCH_EV_DONE 3
#define MUSE_HATCH_EV_ERROR 4
#define TAG "test"
#define ESP_LOGI(tag,...) ((void)(tag))
#define ESP_LOGW(tag,...) ((void)(tag))
#define ESP_LOGE(tag,...) ((void)(tag))
#define portMAX_DELAY 0
#define pdMS_TO_TICKS(n) (n)
#define pdTRUE 1
typedef int TickType_t;
''' + native[types_start:types_end] + r'''
static int s_data=1,s_jobs=1;
static atomic_uint s_generation;
static atomic_bool s_busy,s_clear_history;
static int16_t *s_record;
static size_t s_record_n,s_audio_head,s_audio_n;
static char s_reply[TEXT_CAP],s_copilot_focus[33],s_record_copilot[33],s_reply_arm[33],s_record_reply[33];
static muse_notification_t s_notification;
static muse_timer_state_t timer_state;
static unsigned replacement;
static int posts,transcriptions,completions,timers,messages,errors,replies,spoken,dequeued;
static char posted_id[33],last_reply[96];
static const char *transcript="Approve.",*response;
static job_t queued;
static jmp_buf finished;
static bool current(unsigned generation){return generation==atomic_load(&s_generation);}
static bool begin_job(void){atomic_fetch_add(&s_generation,1);atomic_store(&s_busy,true);return true;}
static void xSemaphoreTake(int s,int timeout){(void)s;(void)timeout;}
static void xSemaphoreGive(int s){(void)s;}
static void *heap_caps_malloc(size_t size,int caps){(void)caps;return malloc(size);}
static int xQueueSend(int queue,const job_t *job,int timeout){(void)queue;(void)timeout;queued=*job;return pdTRUE;}
static int xQueueReceive(int queue,job_t *job,int timeout){
    (void)queue;(void)timeout;if(dequeued++)longjmp(finished,1);*job=queued;return pdTRUE;
}
static void vTaskDelay(int ticks){(void)ticks;}
static size_t fake_strlcpy(char *out,const char *text,size_t cap){
    size_t n=strlen(text);if(cap){size_t k=n<cap-1?n:cap-1;memcpy(out,text,k);out[k]=0;}return n;
}
#undef strlcpy
#define strlcpy fake_strlcpy
muse_timer_state_t muse_timer_status(unsigned *out){if(out)*out=0;return timer_state;}
unsigned muse_timer_pending(void){return replacement;}
static bool openclaw_chat(void){return true;}
static bool muse_wifi_connected(void){return true;}
static bool muse_settings_speaker_on(void){return true;}
static void poll_notifications(void){}
static void report(int state,const char *detail){(void)state;(void)detail;}
static void muse_hatch_console(const char *kind,const char *text,const char *format,...){
    (void)kind;(void)text;(void)format;
}
static void muse_state_set_caption(const char *format,...){(void)format;}
static void emit(const job_t *job,int type,const char *text){
    (void)job;if(type==MUSE_HATCH_EV_ERROR)errors++;
    if(type==MUSE_HATCH_EV_REPLY){replies++;strlcpy(last_reply,text,sizeof(last_reply));}
}
static bool companion_result(const job_t *job,cJSON *body,char *why,size_t cap){
    (void)job;(void)body;(void)why;(void)cap;assert(!"General companion handler must not handle Copilot speech");return false;
}
static char *json_request(const job_t *job,const char *path,cJSON *body,bool speech,bool mac,char *why,size_t cap){
    (void)job;(void)why;(void)cap;
    assert(!strcmp(path,"companion") && !speech && mac);
    assert(!strcmp(cJSON_GetObjectItem(body,"action")->valuestring,"copilot_voice"));
    assert(!strcmp(cJSON_GetObjectItem(body,"text")->valuestring,transcript));
    strlcpy(posted_id,cJSON_GetObjectItem(body,"id")->valuestring,sizeof(posted_id));posts++;
    return response?strdup(response):NULL;
}
static bool request(const job_t *job,const char *path,int method,const char *type,
                    const void *parts,size_t count,char *out,size_t capacity,bool speech,bool mac,char *why,size_t cap){
    (void)job;(void)path;(void)method;(void)type;(void)parts;(void)count;(void)out;(void)capacity;
    (void)speech;(void)mac;(void)why;(void)cap;assert(!"No API-key tests during response");return false;
}
static char *transcribe(const job_t *job,char *why,size_t cap){
    (void)job;(void)why;(void)cap;transcriptions++;return strdup(transcript);
}
static char *complete(const job_t *job,const char *text,cJSON *history,char *why,size_t cap){
    (void)job;(void)text;(void)history;(void)why;(void)cap;completions++;return strdup("Wrong route");
}
static int local_timer(const char *text,char *out,size_t cap){(void)text;(void)out;(void)cap;timers++;return 0;}
static bool add_message(cJSON *history,const char *role,const char *text){(void)history;(void)role;(void)text;messages++;return true;}
static bool speak(const job_t *job,const char *text,char *why,size_t cap){
    (void)job;(void)why;(void)cap;spoken++;assert(!strcmp(text,"Approval submitted for this request only."));return true;
}
static bool show_muted_reply(const job_t *job,const char *text){(void)job;(void)text;return true;}
''' + "\n".join(implementation(name) for name in (
            "muse_openai_copilot_focus", "recording_begin", "muse_hatch_turn_begin", "muse_hatch_turn_cancel",
            "muse_openai_copilot_record_begin", "muse_hatch_turn_end", "copilot_answer", "worker")) + r'''
static void prepare(void){
    muse_openai_copilot_focus("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
    muse_hatch_turn_begin();assert(!strcmp(s_record_copilot,"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"));
    s_record_n=1;
    /* A poll/desktop response changes the cached request during recording. */
    strlcpy(s_notification.id,"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",sizeof(s_notification.id));
    muse_openai_copilot_focus(NULL);muse_hatch_turn_end();
    assert(!strcmp(queued.copilot_target,"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"));
}
int main(void){
    prepare();
    response="{\"copilot\":{\"id\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"state\":\"answered\","
        "\"message\":\"Approval submitted for this request only.\"}}";
    if(!setjmp(finished))worker(NULL);
    assert(posts==1 && transcriptions==1 && !strcmp(posted_id,"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"));
    assert(completions==0 && timers==0 && messages==0 && errors==0 && replies==1 && spoken==1);
    assert(!strcmp(s_notification.id,"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"));
    assert(!atomic_load(&s_busy));
    /* An expired/handled request is an error, never a new agent turn. */
    prepare();dequeued=0;response="{\"error\":{\"message\":\"Expired\"}}";
    if(!setjmp(finished))worker(NULL);
    assert(errors==1 && completions==0 && timers==0 && messages==0 && spoken==1);
    /* A mismatched success-shaped response must not clear a newer request. */
    job_t job={.generation=atomic_load(&s_generation)};char why[96];
    strlcpy(job.copilot_target,"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",sizeof(job.copilot_target));
    response="{\"copilot\":{\"id\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\",\"state\":\"answered\",\"message\":\"Done\"}}";
    assert(!copilot_answer(&job,transcript,why,sizeof(why)));
    assert(!strcmp(s_notification.id,"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"));
    /* An armed iMessage and a local timer must never become Copilot approvals. */
    muse_openai_copilot_focus("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
    strlcpy(s_reply_arm,"cccccccccccccccccccccccccccccccc",sizeof(s_reply_arm));
    muse_hatch_turn_begin();assert(!s_record_copilot[0] && s_record_reply[0]);free(s_record);s_record=NULL;
    timer_state=MUSE_TIMER_RINGING;muse_hatch_turn_begin();assert(!s_record_copilot[0]);free(s_record);s_record=NULL;
    timer_state=MUSE_TIMER_OFF;replacement=30;
    muse_hatch_turn_begin();assert(!s_record_copilot[0]);free(s_record);s_record=NULL;
    replacement=0;
    strcpy(s_notification.id,"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
    strcpy(s_notification.kind,"copilot_ask");s_notification.respondable=true;
    s_notification.allow_freeform=true;s_notification.expires_at=time(NULL)+600;
    assert(muse_openai_copilot_record_begin(s_notification.id));
    assert(!strcmp(s_record_copilot,s_notification.id));muse_hatch_turn_cancel();
    assert(!s_record && !s_record_copilot[0] && !atomic_load(&s_busy));
    /* A stale explicit start cannot fall back to ordinary chat. */
    assert(!muse_openai_copilot_record_begin("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"));
    assert(!s_record && !s_record_copilot[0] && !atomic_load(&s_busy));
    s_notification.allow_freeform=false;
    assert(!muse_openai_copilot_record_begin(s_notification.id));
    assert(!s_record && !s_record_copilot[0]);
    s_notification.allow_freeform=true;s_notification.expires_at=time(NULL)-1;
    assert(!muse_openai_copilot_record_begin(s_notification.id));
    assert(!s_record && !s_record_copilot[0]);
    return 0;
}
'''
        host_tests.HomeTest().compile_case(source)

    def test_touch_dictation_waits_for_send_and_cancel_or_limit_never_submits(self):
        voice = (ROOT / "components/muse/muse_voice.c").read_text()
        def implementation(name):
            start = re.search(r"^(?:static )?(?:void|bool|size_t)\s+" + re.escape(name) + r"\(",
                              voice, re.MULTILINE).start()
            end = voice.index("\n}", start) + 2
            return voice[start:end]
        source = (ROOT / "tests/voice_record_harness.c").read_text() + "\n".join(implementation(name) for name in (
            "muse_voice_copilot_dictate", "muse_voice_copilot_dictating", "muse_voice_copilot_finish", "record")) + r'''
int main(void){
    size_t held;char why[96];const char *id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    assert(muse_voice_copilot_dictate(id) && muse_voice_copilot_dictating());
    assert(!muse_voice_copilot_dictate("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"));
    assert(!strcmp(s_dictation_target,id));
    control=1;
    bool sent=record(false,id,&held,why,sizeof(why));
    if(!sent)fprintf(stderr,"record failed: %s; reads=%d state=%d\n",why,reads,atomic_load(&s_dictation));
    assert(sent);
    assert(reads>=4 && !strcmp(captured,id) && cancelled==0 && generic_begins==0);
    atomic_store(&s_dictation,DICTATION_OFF);mode=MUSE_MODE_IDLE;reads=0;control=2;
    assert(muse_voice_copilot_dictate(id));assert(!record(false,id,&held,why,sizeof(why)));
    assert(cancelled==1 && strstr(why,"CANCELLED") && generic_begins==0);
    atomic_store(&s_dictation,DICTATION_OFF);mode=MUSE_MODE_IDLE;reads=0;control=0;
    assert(muse_voice_copilot_dictate(id));assert(!record(false,id,&held,why,sizeof(why)));
    assert(reads==MAX_FRAMES/MUSE_AUDIO_CHUNK && cancelled==2 && strstr(why,"LIMIT"));
    /* Cancel before voice task starts must not open any recording. */
    atomic_store(&s_dictation,DICTATION_OFF);mode=MUSE_MODE_IDLE;reads=0;
    assert(muse_voice_copilot_dictate(id));muse_voice_copilot_finish(false);
    assert(!record(false,id,&held,why,sizeof(why)) && reads==0);
    atomic_store(&s_dictation,DICTATION_OFF);mode=MUSE_MODE_IDLE;
    assert(muse_voice_copilot_dictate(id));stale=true;
    assert(!record(false,id,&held,why,sizeof(why)) && reads==0 && generic_begins==0);
    /* Send only acts on an active recording. Cancel can still discard its tail. */
    atomic_store(&s_dictation,DICTATION_START);muse_voice_copilot_finish(true);
    assert(atomic_load(&s_dictation)==DICTATION_START);
    atomic_store(&s_dictation,DICTATION_ACTIVE);muse_voice_copilot_finish(true);
    assert(atomic_load(&s_dictation)==DICTATION_SEND);muse_voice_copilot_finish(false);
    assert(atomic_load(&s_dictation)==DICTATION_CANCEL);
    return 0;
}
'''
        host_tests.HomeTest().compile_case(source)


if __name__ == "__main__":
    unittest.main()
