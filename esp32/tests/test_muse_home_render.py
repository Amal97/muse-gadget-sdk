# SPDX-License-Identifier: Apache-2.0
"""Render the production Home constructors with native LVGL, without a device."""
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest

from test_cardputer_navigation import function

ROOT = Path(__file__).resolve().parents[1]


class HomeRenderTest(unittest.TestCase):
    @classmethod
    def run_command(cls, command):
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f"{shlex.join(command)}\n{result.stdout}\n{result.stderr}")

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="muse-home-render-")
        cls.addClassCleanup(cls.tmp.cleanup)
        folder = Path(cls.tmp.name)
        config = folder / "lv_conf.h"
        config.write_text("""
#pragma once
#define LV_COLOR_DEPTH 32
#define LV_USE_OS LV_OS_NONE
#define LV_USE_STDLIB_MALLOC LV_STDLIB_CLIB
#define LV_USE_STDLIB_STRING LV_STDLIB_CLIB
#define LV_USE_STDLIB_SPRINTF LV_STDLIB_CLIB
#define LV_USE_LOG 0
#define LV_FONT_MONTSERRAT_14 1
#define LV_FONT_MONTSERRAT_16 1
#define LV_FONT_MONTSERRAT_20 1
#define LV_FONT_MONTSERRAT_28 1
#define LV_FONT_MONTSERRAT_48 1
""")
        lvgl = ROOT / "managed_components/lvgl__lvgl"
        build = folder / "build"
        cls.run_command(["cmake", "-S", str(lvgl), "-B", str(build),
                         f"-DLV_BUILD_CONF_PATH={config}", "-DCONFIG_LV_BUILD_DEMOS=OFF",
                         "-DCONFIG_LV_BUILD_EXAMPLES=OFF", "-DCONFIG_LV_USE_THORVG_INTERNAL=OFF"])
        cls.run_command(["cmake", "--build", str(build), "--parallel", "4"])
        libraries = list(build.rglob("liblvgl.a"))
        if len(libraries) != 1:
            raise RuntimeError(f"Expected one native LVGL library, found {libraries}")

        ui = (ROOT / "components/muse/muse_ui.c").read_text()
        colors = "\n".join(re.findall(
            r"^#define (?:HOME_\w+|COLOR_LIT|COLOR_DOT_OFF) .*$", ui, re.M))
        source = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lvgl.h"
#define MUSE_COMPANION_SNAPSHOT_CAP 1024
#define ESP_LOGE(tag,...) ((void)(tag))
static const char *TAG="test";
static int s_w=466, s_h=466, actions;
static lv_obj_t *s_home, *s_home_time, *s_home_date, *s_home_weather_title;
static lv_obj_t *s_home_weather, *s_home_weather_detail, *s_home_reminder, *s_home_reminder_detail;
static lv_obj_t *s_home_briefing, *s_home_briefing_title, *s_home_footer;
static char *s_home_json;
static uint32_t pixels[466*466];
static void muse_state_set_caption(const char *text) { fprintf(stderr,"%s\n",text); abort(); }
static void home_detail(lv_event_t *event) {
    intptr_t index=(intptr_t)lv_event_get_user_data(event);
    assert(index>=0 && index<=2); actions|=1<<index;
}
''' + colors + "\n" + "\n".join(
            function(ui, name) for name in ("make_label", "home_card", "build_home")) + r'''
static void flush(lv_display_t *display,const lv_area_t *area,uint8_t *data) {
    (void)area;(void)data;lv_display_flush_ready(display);
}
static void inside_circle(lv_obj_t *obj) {
    lv_area_t area;lv_obj_get_coords(obj,&area);
    lv_obj_get_transformed_area(obj,&area,LV_OBJ_POINT_TRANSFORM_FLAG_RECURSIVE);
    const int x[]={area.x1,area.x2}, y[]={area.y1,area.y2};
    for(int i=0;i<2;i++)for(int j=0;j<2;j++){
        int dx=2*x[i]-(s_w-1),dy=2*y[j]-(s_h-1);
        if(dx*dx+dy*dy>s_w*s_w)fprintf(stderr,"Clipped label at %d px: %s [%d,%d,%d,%d]\n",
            s_w,lv_label_get_text(obj),area.x1,area.y1,area.x2,area.y2);
        assert(dx*dx+dy*dy<=s_w*s_w);
    }
}
static void inside_parent(lv_obj_t *obj) {
    lv_area_t label,parent;lv_obj_get_coords(obj,&label);
    lv_obj_get_coords(lv_obj_get_parent(obj),&parent);
    assert(label.x1>=parent.x1 && label.x2<=parent.x2);
    assert(label.y1>=parent.y1 && label.y2<=parent.y2);
    inside_circle(obj);
}
int main(int argc,char **argv) {
    assert(argc==5);s_w=s_h=atoi(argv[4]);assert(s_w>=320 && s_w<=466);lv_init();
    lv_display_t *display=lv_display_create(s_w,s_h);
    lv_display_set_color_format(display,LV_COLOR_FORMAT_ARGB8888);
    lv_display_set_buffers(display,pixels,NULL,sizeof(pixels),LV_DISPLAY_RENDER_MODE_FULL);
    lv_display_set_flush_cb(display,flush);
    lv_obj_t *tiles=lv_tileview_create(lv_screen_active());
    s_home=lv_tileview_add_tile(tiles,0,0,LV_DIR_RIGHT);
    build_home();
    lv_obj_update_layout(s_home);
    inside_circle(s_home_date);inside_circle(s_home_time);
    lv_label_set_text(s_home_date,"Mon, 05 Oct");
    lv_label_set_text(s_home_time,argv[2]);
    lv_label_set_text(s_home_weather_title,"YOUR WEATHER");
    lv_label_set_text(s_home_weather,"18 C  |  Partly cloudy");
    lv_label_set_text(s_home_weather_detail,"12-21 C  /  Rain 25%");
    lv_label_set_text(s_home_reminder,"Stretch and take a break");
    lv_label_set_text(s_home_reminder_detail,"06 Oct 09:00  /  2 active");
    lv_label_set_text(s_home_briefing_title,!strcmp(argv[3],"copilot")?"COPILOT TASK":"DAILY BRIEFING");
    lv_label_set_text(s_home_briefing,!strcmp(argv[3],"copilot")?
        "Project / waiting\nReview the pending question.":
        "Good morning.\nA little space for what matters.");
    lv_label_set_text(s_home_footer,"Mac live / Copilot connected");
    lv_obj_update_layout(s_home);
    inside_circle(s_home_date);inside_circle(s_home_time);inside_circle(s_home_footer);
    lv_obj_t *labels[]={s_home_weather_title,s_home_weather,s_home_weather_detail,
        s_home_reminder,s_home_reminder_detail,s_home_briefing_title,s_home_briefing};
    for(unsigned i=0;i<sizeof(labels)/sizeof(labels[0]);i++){
        inside_parent(labels[i]);
        assert(lv_obj_get_height(labels[i])>=lv_obj_get_style_text_font(labels[i],0)->line_height);
    }
    lv_obj_t *weather=lv_obj_get_parent(s_home_weather);
    lv_obj_t *reminders=lv_obj_get_parent(s_home_reminder);
    lv_obj_t *briefing=lv_obj_get_parent(s_home_briefing);
    lv_area_t a,b,c;lv_obj_get_coords(weather,&a);lv_obj_get_coords(reminders,&b);
    lv_obj_get_coords(briefing,&c);assert(a.y2<b.y1 && b.y2<c.y1);
    lv_area_t footer;lv_obj_get_coords(s_home_footer,&footer);assert(c.y2<footer.y1);
    lv_area_t date,clock,divider;lv_obj_get_coords(s_home_date,&date);
    lv_obj_get_coords(s_home_time,&clock);
    lv_obj_get_transformed_area(s_home_time,&clock,LV_OBJ_POINT_TRANSFORM_FLAG_RECURSIVE);
    lv_obj_get_coords(lv_obj_get_child(s_home,2),&divider);
    assert(date.y2<clock.y1 && clock.y2<divider.y1);
    assert(lv_obj_get_style_bg_opa(weather,0)==LV_OPA_TRANSP);
    assert(lv_obj_get_style_border_width(weather,0)==0);
    assert(lv_obj_get_style_border_width(briefing,0)==1);
    lv_obj_send_event(weather,LV_EVENT_CLICKED,NULL);
    lv_obj_send_event(reminders,LV_EVENT_CLICKED,NULL);
    lv_obj_send_event(briefing,LV_EVENT_CLICKED,NULL);assert(actions==7);
    lv_refr_now(display);
    FILE *out=fopen(argv[1],"wb");assert(out);fprintf(out,"P6\n%d %d\n255\n",s_w,s_h);
    for(int y=0;y<s_h;y++)for(int x=0;x<s_w;x++){
        int dx=2*x-(s_w-1),dy=2*y-(s_h-1);
        uint32_t color=dx*dx+dy*dy>s_w*s_w?0:pixels[y*s_w+x];
        unsigned char rgb[]={(color>>16)&255,(color>>8)&255,color&255};
        assert(fwrite(rgb,1,3,out)==3);
    }
    assert(!fclose(out));free(s_home_json);lv_display_delete(display);return 0;
}
'''
        harness = folder / "render.c"
        harness.write_text(source)
        cls.binary = folder / "render"
        cls.run_command([*shlex.split(os.environ.get("CC", "cc")), "-std=gnu11",
                         "-Wall", "-Wextra", "-Werror", "-DLV_KCONFIG_IGNORE=1",
                         f'-DLV_CONF_PATH="{config}"', "-I", str(lvgl), str(harness),
                         str(libraries[0]), "-lm", "-o", str(cls.binary)])

    def test_clock_cards_and_text_fit_the_round_display_and_remain_clickable(self):
        folder = Path(os.environ.get("MUSE_HOME_CAPTURE_DIR", self.tmp.name))
        folder.mkdir(parents=True, exist_ok=True)
        frames = []
        for size in (320, 412, 466):
            for clock, state in [("08:00", "briefing"), ("11:11", "briefing"),
                                 ("23:59", "copilot"), ("--:--", "briefing")]:
                with self.subTest(size=size, clock=clock, state=state):
                    frame = folder / f"home-{size}-{clock.replace(':', '-')}-{state}.ppm"
                    self.run_command([str(self.binary), str(frame), clock, state, str(size)])
                    data = frame.read_bytes()
                    header = f"P6\n{size} {size}\n255\n".encode()
                    self.assertTrue(data.startswith(header))
                    self.assertEqual(len(data), len(header) + size * size * 3)
                    self.assertGreater(len(set(data[len(header):])), 20)
                    frames.append(data)
        self.assertEqual(len(set(frames)), 12)


if __name__ == "__main__":
    unittest.main()
