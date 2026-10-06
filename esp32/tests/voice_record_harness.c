// SPDX-License-Identifier: Apache-2.0
#include <assert.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "muse_openai.h"
#define CONFIG_MUSE_OPENCLAW 1
#define MUSE_AUDIO_RATE 1000
#define MUSE_AUDIO_CHUNK 20
#define MAX_FRAMES 15000
#define TAIL_FRAMES 120
#define SETTLE_CHUNKS 0
#define ESP_OK 0
#define MUSE_MODE_IDLE 0
#define MUSE_MODE_LISTENING 1
#define MUSE_HATCH_EV_NONE 0
#define MUSE_HATCH_EV_HEARD 1
#define MUSE_HATCH_EV_ERROR 2
#define MUSE_PTT_UP 1
#define MUSE_PTT_DOWN 2
#define TAG "test"
static void log_message(const char *tag,const char *format,...){(void)tag;(void)format;}
#define ESP_LOGI log_message
#define ESP_LOGW log_message
enum { DICTATION_OFF, DICTATION_RESERVING, DICTATION_START, DICTATION_ACTIVE, DICTATION_SEND, DICTATION_CANCEL };
static atomic_int s_dictation;
static char s_dictation_target[33],captured[33];
static int s_queue=1,mode,reads,control,generic_begins,cancelled;
static bool asleep,s_live,s_tried,stale;
static int16_t *s_rec,s_chunk[MUSE_AUDIO_CHUNK];
static size_t s_rec_n,s_sent,s_pre_fill;
static int s_held_count;
typedef int muse_hatch_ev_t;
typedef struct { size_t chunks,frames; double acc; unsigned peak; float floor_db,tail_db[25]; int clipped; } rec_stats_t;
static size_t fake_strlcpy(char *out,const char *text,size_t cap){
    size_t n=strlen(text);if(cap)snprintf(out,cap,"%s",text);return n;
}
#undef strlcpy
#define strlcpy fake_strlcpy
static int muse_state_mode(void *out){(void)out;return mode;}
static bool muse_state_asleep(void){return asleep;}
static void muse_state_set_caption(const char *format,...){(void)format;}
static void muse_state_poke(void){}
static void muse_state_set_mode(int value){mode=value;}
static void muse_state_set_progress(float value){(void)value;}
static void muse_state_set_level(float value){(void)value;}
static void go_live(void){generic_begins++;}
static bool muse_hatch_ready(void){return true;}
static int muse_audio_read(int16_t *pcm,size_t n){
    (void)pcm;(void)n;reads++;
    if(reads==4 && control==1)atomic_store(&s_dictation,DICTATION_SEND);
    if(reads==4 && control==2)atomic_store(&s_dictation,DICTATION_CANCEL);
    return reads==4 && control==5 ? -1 : ESP_OK;
}
static float muse_audio_level(const int16_t *pcm,size_t n){(void)pcm;(void)n;return 0;}
static void take(rec_stats_t *stats,const int16_t *pcm){(void)stats;(void)pcm;}
static void pre_get(size_t i,int16_t *pcm){(void)i;(void)pcm;}
static muse_hatch_ev_t muse_hatch_turn_event(char *text,size_t cap){(void)text;(void)cap;return MUSE_HATCH_EV_NONE;}
static void muse_hatch_turn_cancel(void){cancelled++;}
bool muse_openai_copilot_record_begin(const char *id){strlcpy(captured,id,sizeof(captured));return !stale;}
static bool got_event(int type){return type==MUSE_PTT_DOWN ? control==4 && reads==4 : true;}
static int muse_settings_mic_gain(void){return 0;}
static double fake_log10(double n){(void)n;return 0;}
#define log10 fake_log10
#define log10f fake_log10
#if CONFIG_MUSE_WAKEWORD
#include "muse_voice_endpoint.c"
static bool s_hands_free_recording;
static bool muse_settings_wakeword_on(void){return !(control==6 && reads>=4);}
static bool muse_wakeword_speech(int16_t *pcm){(void)pcm;return control==7 && reads<=20;}
#endif
