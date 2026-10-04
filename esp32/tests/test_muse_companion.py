# SPDX-License-Identifier: Apache-2.0
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LocalTimerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        folder = Path(cls.tmp.name)
        source = (ROOT / "components/muse/muse_companion.c").read_text()
        source = re.sub(r"^#include .*$", "", source, flags=re.MULTILINE)
        harness = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <stdarg.h>
#include "muse_companion.h"
#define ESP_OK 0
#define ESP_ERR_NO_MEM 1
#define ESP_ERR_NVS_NOT_FOUND 2
#define NVS_READWRITE 1
#define portMAX_DELAY 0
#define pdPASS 1
#define pdMS_TO_TICKS(n) (n)
#define ESP_ERROR_CHECK(n) assert((n)==0)
#define ESP_LOGE(tag, ...) ((void)(tag))
#define ESP_LOGW(tag, ...) ((void)(tag))
typedef int esp_err_t;
typedef int nvs_handle_t;
typedef int SemaphoreHandle_t;
static int64_t micros, clock_time = 1700000000;
static bool stored, fail_save, asleep;
static uint8_t phase, pending_phase;
static int64_t epoch, pending_epoch;
static char caption[128];
const char *esp_err_to_name(int e) { (void)e; return "test"; }
static int xSemaphoreCreateMutex(void) { return 1; }
static void xSemaphoreTake(int l, int w) { (void)l; (void)w; }
static void xSemaphoreGive(int l) { (void)l; }
static void vTaskDelay(int n) { (void)n; }
static int xTaskCreate(void (*f)(void *), const char *n, int s, void *a, int p, void *o) {
    (void)f; (void)n; (void)s; (void)a; (void)p; (void)o; return 1;
}
static int64_t esp_timer_get_time(void) { return micros; }
static time_t fake_time(time_t *out) { if (out) *out=clock_time; return clock_time; }
#define time fake_time
static bool muse_state_asleep(void) { return asleep; }
static void muse_state_set_asleep(bool value) { asleep=value; }
static void muse_state_set_caption(const char *format, ...) {
    va_list args; va_start(args,format); vsnprintf(caption,sizeof(caption),format,args); va_end(args);
}
static int nvs_open(const char *n, int mode, int *h) { (void)n; (void)mode; *h=1; return 0; }
static void nvs_close(int h) { (void)h; }
static int nvs_set_u8(int h,const char *key,uint8_t value) { (void)h; (void)key; pending_phase=value; return 0; }
static int nvs_set_i64(int h,const char *key,int64_t value) { (void)h; (void)key; pending_epoch=value; return 0; }
static int nvs_commit(int h) { (void)h; if(fail_save)return 1; stored=true; phase=pending_phase; epoch=pending_epoch; return 0; }
static int nvs_get_u8(int h,const char *key,uint8_t *out) { (void)h;(void)key;*out=phase;return stored?0:2; }
static int nvs_get_i64(int h,const char *key,int64_t *out) { (void)h;(void)key;*out=epoch;return stored?0:2; }
""" + source + r"""
int main(void) {
    muse_companion_start();
    assert(muse_timer_status(NULL)==MUSE_TIMER_OFF);
    clock_time=0;
    assert(muse_timer_start(10));
    unsigned remaining;
    assert(muse_timer_status(&remaining)==MUSE_TIMER_RUNNING && remaining==10);
    assert(!muse_timer_start(30) && muse_timer_pending()==30);
    assert(!muse_timer_start(60) && muse_timer_pending()==60);
    assert(!muse_timer_confirm_replace(30));
    assert(muse_timer_status(&remaining)==MUSE_TIMER_RUNNING && remaining==10);
    muse_timer_cancel_replace();
    assert(muse_timer_pending()==0);
    micros=9999999; timer_tick();
    assert(muse_timer_status(&remaining)==MUSE_TIMER_RUNNING && remaining==1);
    asleep=true; micros=10000000; timer_tick();
    assert(!asleep && muse_timer_status(NULL)==MUSE_TIMER_RINGING);
    fail_save=true;
    assert(!muse_timer_dismiss() && muse_timer_status(NULL)==MUSE_TIMER_RINGING);
    fail_save=false;
    assert(muse_timer_snooze(300));
    assert(muse_timer_status(&remaining)==MUSE_TIMER_RUNNING && remaining==300);
    assert(!muse_timer_start(20));
    assert(muse_timer_confirm_replace(20));
    assert(muse_timer_status(&remaining)==MUSE_TIMER_RUNNING && remaining==20);
    assert(muse_timer_dismiss());
    clock_time=1700000000;
    assert(muse_timer_start(10));
    s_phase=MUSE_TIMER_OFF; s_epoch=0; s_deadline=0; s_pending=0;
    clock_time=0;
    muse_companion_start();
    assert(muse_timer_status(NULL)==MUSE_TIMER_WAIT_CLOCK);
    clock_time=1700000002; timer_tick();
    assert(muse_timer_status(&remaining)==MUSE_TIMER_RUNNING && remaining==8);
    assert(muse_timer_dismiss());
    assert(!muse_timer_start(0) && !muse_timer_start(604801));
    assert(muse_timer_start(604800));
    assert(muse_timer_status(&remaining)==MUSE_TIMER_RUNNING && remaining==604800);
    return 0;
}
"""
        (folder / "timer.c").write_text(harness)
        cls.binary = folder / "timer"
        result = subprocess.run([*shlex.split(os.environ.get("CC", "cc")), "-std=gnu11",
            "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "components/muse"),
            str(folder / "timer.c"), "-o", str(cls.binary)], capture_output=True, text=True)
        if result.returncode:
            cls.tmp.cleanup()
            raise RuntimeError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_real_timer_thresholds_offline_wake_snooze_confirmation_and_clock_restore(self):
        result = subprocess.run([str(self.binary)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
