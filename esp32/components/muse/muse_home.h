// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <time.h>
#include "cJSON.h"

typedef enum { MUSE_HOME_KEEP = -1, MUSE_HOME_DASHBOARD, MUSE_HOME_FACE, MUSE_HOME_SETTINGS } muse_home_page_t;
typedef struct {
    muse_home_page_t preferred;
    bool borrowed;
} muse_home_navigation_t;

void muse_home_choose(muse_home_navigation_t *navigation, muse_home_page_t page);
void muse_home_borrow(muse_home_navigation_t *navigation);
muse_home_page_t muse_home_return(muse_home_navigation_t *navigation);
bool muse_home_clock_offset(const cJSON *root, int32_t *offset);
const char *muse_home_text(const cJSON *root, const char *key);

typedef struct {
    char time[8], date[48];
    char weather[112], weather_detail[160];
    char reminder[176], reminder_detail[80];
    char briefing[272], footer[96];
    char copilot[272];
    bool task_visible, evening;
} muse_home_view_t;

void muse_home_format(muse_home_view_t *view, const cJSON *root, time_t now,
                      int32_t offset, bool wifi_connected);
