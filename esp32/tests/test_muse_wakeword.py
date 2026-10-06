# SPDX-License-Identifier: Apache-2.0
import csv
from pathlib import Path
import re
import unittest

import test_muse_home as host_tests

ROOT = Path(__file__).resolve().parents[1]


class WakeWordTest(unittest.TestCase):
    def test_model_partition_preserves_every_existing_partition(self):
        def partitions(name):
            with (ROOT / name).open() as stream:
                return {row[0].strip(): tuple(cell.strip() for cell in row[1:])
                        for row in csv.reader(stream)
                        if row and not row[0].lstrip().startswith("#")}

        existing = partitions("partitions_muse.csv")
        updated = partitions("partitions_muse_wakeword.csv")
        self.assertEqual(set(updated) - set(existing), {"model"})
        for name, geometry in existing.items():
            self.assertEqual(updated[name], geometry, name)
        self.assertEqual(updated["model"], ("data", "spiffs", "0x900000", "2M", ""))

        def bytes_value(value):
            return int(value[:-1]) * 1024 * 1024 if value.endswith("M") else int(value, 0)

        offset, size = map(bytes_value, updated["model"][2:4])
        self.assertGreaterEqual(offset, max(bytes_value(row[2]) + bytes_value(row[3])
                                            for row in existing.values()))
        self.assertLessEqual(offset + size, 16 * 1024 * 1024)

    def test_wakenet_buffers_model_sized_frames_and_resets_after_playback(self):
        engine = (ROOT / "components/muse/muse_wakeword.c").read_text()
        engine = re.sub(r"^#include .*$", "", engine, flags=re.MULTILINE)
        source = r'''
#include <assert.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#define CONFIG_MUSE_WAKEWORD 1
#define ESP_OK 0
#define ESP_FAIL -1
#define MUSE_AUDIO_RATE 16000
#define MALLOC_CAP_SPIRAM 1
#define MALLOC_CAP_8BIT 2
#define WAKENET_DETECTED 1
#define VAD_SPEECH 1
#define VAD_MODE_3 3
#define DET_MODE_95 1
typedef int esp_err_t;
typedef int srmodel_list_t;
typedef int model_iface_data_t;
typedef int *vad_handle_t;
typedef struct {
    model_iface_data_t *(*create)(const void *,int);
    int (*get_samp_chunksize)(model_iface_data_t *);
    int (*get_channel_num)(model_iface_data_t *);
    int (*get_samp_rate)(model_iface_data_t *);
    void (*clean)(model_iface_data_t *);
    void (*destroy)(model_iface_data_t *);
    int (*detect)(model_iface_data_t *,int16_t *);
} esp_wn_iface_t;
bool muse_wakeword_ready(void);
static int storage,case_id,detections,creates,destroys,loads,errors;
static bool trigger;
static void log_message(const char *tag,const char *format,...){(void)tag;(void)format;}
#define ESP_LOGI log_message
#define ESP_LOGE(...) (errors++,log_message(__VA_ARGS__))
static srmodel_list_t *esp_srmodel_init(const char *name){
    assert(!strcmp(name,"model"));loads++;return case_id==1 ? NULL : &storage;
}
static const char *esp_srmodel_filter(srmodel_list_t *models,const char *type,const char *word){
    assert(models==&storage && !strcmp(type,"wn9") && !strcmp(word,"hiesp"));return "wn9_hiesp";
}
static void esp_srmodel_deinit(srmodel_list_t *models){assert(models==&storage);}
static model_iface_data_t *create(const void *name,int mode){
    assert(!strcmp(name,"wn9_hiesp") && mode==DET_MODE_95);creates++;return case_id==8 ? NULL : &storage;
}
static int size(model_iface_data_t *model){(void)model;return case_id==4 ? 0 : case_id==5 ? 32000 : 512;}
static int channels(model_iface_data_t *model){(void)model;return case_id==2 ? 2 : 1;}
static int rate(model_iface_data_t *model){(void)model;return case_id==3 ? 8000 : 16000;}
static void destroy(model_iface_data_t *model){assert(model==&storage);destroys++;}
static int detect(model_iface_data_t *model,int16_t *samples){
    assert(model==&storage);
    for(int i=0;i<512;i++)assert(samples[i]==detections*512+i);
    detections++;return trigger ? WAKENET_DETECTED : 0;
}
static const esp_wn_iface_t iface={create,size,channels,rate,NULL,destroy,detect};
static const esp_wn_iface_t *esp_wn_handle_from_name(const char *name){
    assert(!strcmp(name,"wn9_hiesp"));return &iface;
}
static void *heap_caps_malloc(size_t n,int caps){(void)caps;return case_id==6 ? NULL : malloc(n);}
static vad_handle_t vad_create(int mode){assert(mode==VAD_MODE_3);return case_id==7 ? NULL : &storage;}
static void vad_destroy(vad_handle_t vad){assert(vad==&storage);}
static int vad_process(vad_handle_t vad,int16_t *pcm,int hz,int ms){
    assert(vad==&storage && pcm && hz==16000 && ms==20);return VAD_SPEECH;
}
''' + engine + r'''
int main(void) {
    int16_t pcm[1024];for(int i=0;i<1024;i++)pcm[i]=i;
    for(case_id=1;case_id<=8;case_id++){
        assert(muse_wakeword_init()==ESP_FAIL && !muse_wakeword_ready());
        assert(!muse_wakeword_feed(pcm,320));
    }
    assert(errors==8);
    case_id=0;assert(muse_wakeword_init()==ESP_OK && muse_wakeword_ready());
    assert(muse_wakeword_init()==ESP_OK);
    int before=destroys,created=creates,mapped=loads;
    muse_wakeword_reset();assert(destroys==before+1 && creates==created+1 && s_fill==0 && loads==mapped);
    assert(!muse_wakeword_feed(pcm,320) && detections==0);
    assert(!muse_wakeword_feed(pcm+320,320) && detections==1 && s_fill==128);
    trigger=true;
    assert(muse_wakeword_feed(pcm+640,384) && detections==2);
    assert(muse_wakeword_speech(pcm));
    muse_wakeword_reset();assert(s_fill==0 && destroys==before+2 && loads==mapped);
    assert(!muse_wakeword_feed(pcm,320) && detections==2);
    case_id=8;muse_wakeword_reset();
    assert(!muse_wakeword_ready() && !s_model && !s_frame && !s_vad && loads==mapped);
    assert(!muse_wakeword_feed(pcm,320) && !muse_wakeword_speech(pcm));
    case_id=0;assert(muse_wakeword_init()==ESP_OK && muse_wakeword_ready() && loads==mapped);
    return 0;
}
'''
        host_tests.HomeTest().compile_case(source)

    def test_endpoint_measures_speech_silence_timeout_and_duration_exactly(self):
        source = r'''
#include <assert.h>
#include "muse_voice_endpoint.c"
int main(void) {
    muse_voice_endpoint_t state = {0};
    for(int i=0;i<249;i++) assert(muse_voice_endpoint_feed(&state,false)==MUSE_ENDPOINT_LISTEN);
    assert(muse_voice_endpoint_feed(&state,false)==MUSE_ENDPOINT_EMPTY && state.elapsed_ms==5000);
    state=(muse_voice_endpoint_t){0};
    for(int i=0;i<9;i++) assert(muse_voice_endpoint_feed(&state,true)==MUSE_ENDPOINT_LISTEN);
    assert(!state.heard);
    assert(muse_voice_endpoint_feed(&state,true)==MUSE_ENDPOINT_LISTEN && state.heard);
    for(int i=0;i<44;i++) assert(muse_voice_endpoint_feed(&state,false)==MUSE_ENDPOINT_LISTEN);
    assert(muse_voice_endpoint_feed(&state,false)==MUSE_ENDPOINT_SEND && state.silence_ms==900);
    state=(muse_voice_endpoint_t){0};
    for(int i=0;i<10;i++) muse_voice_endpoint_feed(&state,true);
    for(int i=0;i<40;i++) muse_voice_endpoint_feed(&state,false);
    assert(muse_voice_endpoint_feed(&state,true)==MUSE_ENDPOINT_LISTEN && state.silence_ms==0);
    for(int i=0;i<44;i++) assert(muse_voice_endpoint_feed(&state,false)==MUSE_ENDPOINT_LISTEN);
    assert(muse_voice_endpoint_feed(&state,false)==MUSE_ENDPOINT_SEND);
    state=(muse_voice_endpoint_t){0};
    for(int i=0;i<749;i++) assert(muse_voice_endpoint_feed(&state,true)==MUSE_ENDPOINT_LISTEN);
    assert(muse_voice_endpoint_feed(&state,true)==MUSE_ENDPOINT_SEND && state.elapsed_ms==15000);
    /* Isolated noise bursts must not accumulate into a confirmed command. */
    state=(muse_voice_endpoint_t){0};
    for(int i=0;i<24;i++) {
        for(int j=0;j<9;j++) assert(muse_voice_endpoint_feed(&state,true)==MUSE_ENDPOINT_LISTEN);
        assert(muse_voice_endpoint_feed(&state,false)==MUSE_ENDPOINT_LISTEN);
    }
    assert(!state.heard);
    return 0;
}
'''
        host_tests.HomeTest().compile_case(source)

    def test_disabled_engine_has_no_detector_or_automatic_capture(self):
        engine = (ROOT / "components/muse/muse_wakeword.c").read_text()
        engine = re.sub(r"^#include .*$", "", engine, flags=re.MULTILINE)
        source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#define CONFIG_MUSE_WAKEWORD 0
#define ESP_ERR_NOT_SUPPORTED -2
typedef int esp_err_t;
''' + engine + r'''
int main(void) {
    int16_t pcm[320]={0};
    assert(muse_wakeword_init()==ESP_ERR_NOT_SUPPORTED && !muse_wakeword_ready());
    assert(!muse_wakeword_feed(pcm,320) && !muse_wakeword_speech(pcm));
    muse_wakeword_reset();
    return 0;
}
'''
        host_tests.HomeTest().compile_case(source)

    def test_flash_model_load_precedes_external_stack_voice_task(self):
        voice = (ROOT / "components/muse/muse_voice.c").read_text()
        worker = voice[voice.index("static void voice_task("):voice.index("esp_err_t muse_voice_start(")]
        startup = voice[voice.index("esp_err_t muse_voice_start("):voice.index("void muse_voice_set_monitor(")]
        self.assertNotIn("muse_wakeword_init(", worker)
        self.assertLess(startup.index("muse_wakeword_init("),
                        startup.index("xTaskCreatePinnedToCoreWithCaps("))

    def test_wake_preference_reports_model_and_persistence_failures_without_enabling(self):
        settings = (ROOT / "components/muse/muse_settings.c").read_text()

        def implementation(name):
            start = re.search(r"^(?:bool|esp_err_t)\s+" + name + r"\(",
                              settings, re.MULTILINE).start()
            return settings[start:settings.index("\n}", start) + 2]

        source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#define CONFIG_MUSE_WAKEWORD 1
#define ESP_OK 0
#define ESP_ERR_INVALID_STATE -1
#define LOCKED(body) do { body; } while(0)
#define TAG "test"
typedef int esp_err_t;
static struct { bool wakeword; } s;
static int s_nvs,commit_error,save_error,errors,nudges,persisted;
static bool ready;
static void log_message(const char *tag,const char *format,...){(void)tag;(void)format;}
#define ESP_LOGE(...) (errors++,log_message(__VA_ARGS__))
static bool muse_wakeword_ready(void){return ready;}
static int nvs_set_u8(int handle,const char *key,uint8_t value){
    (void)handle;(void)key;persisted=value;return save_error;
}
static int nvs_commit(int handle){(void)handle;return commit_error;}
static const char *esp_err_to_name(int err){(void)err;return "fixture";}
static void muse_state_nudge(void){nudges++;}
''' + implementation("muse_settings_wakeword_on") + implementation("muse_settings_set_wakeword_on") + r'''
int main(void) {
    assert(muse_settings_set_wakeword_on(true)==ESP_ERR_INVALID_STATE && !muse_settings_wakeword_on());
    ready=true;
    assert(muse_settings_set_wakeword_on(true)==ESP_OK && muse_settings_wakeword_on() && persisted==1);
    commit_error=-2;
    assert(muse_settings_set_wakeword_on(false)==-2 && muse_settings_wakeword_on());
    commit_error=0;save_error=-3;
    assert(muse_settings_set_wakeword_on(false)==-3 && muse_settings_wakeword_on());
    save_error=0;
    assert(muse_settings_set_wakeword_on(false)==ESP_OK && !muse_settings_wakeword_on() && persisted==0);
    assert(errors==2 && nudges==4);
    return 0;
}
'''
        host_tests.HomeTest().compile_case(source)

    def test_hands_free_recording_never_submits_empty_cancelled_or_failed_microphone_audio(self):
        voice = (ROOT / "components/muse/muse_voice.c").read_text()

        def implementation(name):
            start = re.search(r"^(?:static )?(?:void|bool|size_t)\s+" + name + r"\(",
                              voice, re.MULTILINE).start()
            return voice[start:voice.index("\n}", start) + 2]

        source = "#define CONFIG_MUSE_WAKEWORD 1\n"
        source += (ROOT / "tests/voice_record_harness.c").read_text()
        source += "\n".join(implementation(name) for name in (
            "muse_voice_copilot_dictate", "muse_voice_copilot_dictating",
            "muse_voice_copilot_finish", "record"))
        source += r'''
int main(void) {
    size_t held;char why[96];
    s_hands_free_recording=true;
    assert(!record(false,"",&held,why,sizeof(why)));
    assert(reads==250 && cancelled==1 && strstr(why,"NO COMMAND") && generic_begins==1);
    for(int scenario=4;scenario<=6;scenario++) {
        reads=0;control=scenario;
        assert(!record(false,"",&held,why,sizeof(why)));
        assert(reads==4 && strstr(why,"NOT SENT"));
    }
    assert(cancelled==4);
    reads=0;control=7;
    assert(record(false,"",&held,why,sizeof(why)));
    assert(reads==65 && held==1300 && cancelled==4);
    /* Manual push-to-talk still stops on release, not the hands-free timeout. */
    s_hands_free_recording=false;reads=0;control=0;
    assert(record(false,"",&held,why,sizeof(why)));
    assert(reads==7 && cancelled==4);
    assert(muse_voice_copilot_dictating()==false);
    const char *id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    mode=MUSE_MODE_IDLE;assert(muse_voice_copilot_dictate(id));
    muse_voice_copilot_finish(false);
    assert(muse_voice_copilot_dictating());
    (void)s_rec_n;
    return 0;
}
'''
        host_tests.HomeTest().compile_case(source)


if __name__ == "__main__":
    unittest.main()
