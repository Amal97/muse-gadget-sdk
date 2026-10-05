// SPDX-License-Identifier: Apache-2.0
#include "muse_home.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

void muse_home_choose(muse_home_navigation_t *navigation, muse_home_page_t page)
{
    navigation->borrowed = false;
    if (page == MUSE_HOME_DASHBOARD || page == MUSE_HOME_FACE) navigation->preferred = page;
}

void muse_home_borrow(muse_home_navigation_t *navigation) { navigation->borrowed = true; }

muse_home_page_t muse_home_return(muse_home_navigation_t *navigation)
{
    if (!navigation->borrowed) return MUSE_HOME_KEEP;
    navigation->borrowed = false;
    return navigation->preferred;
}

const char *muse_home_text(const cJSON *root, const char *key)
{
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(root, key);
    return cJSON_IsString(value) ? value->valuestring : "";
}

bool muse_home_clock_offset(const cJSON *root, int32_t *offset)
{
    const cJSON *clock = cJSON_GetObjectItemCaseSensitive(root, "clock");
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(clock, "utc_offset_seconds");
    if (!cJSON_IsNumber(value) || !isfinite(value->valuedouble) ||
        value->valuedouble < -50400 || value->valuedouble > 50400 ||
        fmod(value->valuedouble, 60) != 0) return false;
    *offset = (int32_t)value->valuedouble;
    return true;
}

static void preview(char *out, size_t cap, const char *text)
{
    size_t size = strlen(text);
    if (size < cap) {
        memcpy(out, text, size + 1);
        return;
    }
    size = cap - 4;
    while (size && ((unsigned char)text[size] & 0xc0) == 0x80) size--;
    memcpy(out, text, size);
    memcpy(out + size, "...", 4);
}

static const char *condition(int code)
{
    switch (code) {
    case 0: return "Clear sky";
    case 1: return "Mostly clear";
    case 2: return "Partly cloudy";
    case 3: return "Overcast";
    case 45: case 48: return "Fog";
    case 51: case 53: case 55: return "Drizzle";
    case 56: case 57: return "Freezing drizzle";
    case 61: case 63: case 65: return "Rain";
    case 66: case 67: return "Freezing rain";
    case 71: case 73: case 75: case 77: return "Snow";
    case 80: case 81: case 82: return "Rain showers";
    case 85: case 86: return "Snow showers";
    case 95: case 96: case 99: return "Thunderstorm";
    default: return "Unknown condition";
    }
}

void muse_home_format(muse_home_view_t *view, const cJSON *root, time_t now,
                      int32_t offset, bool wifi_connected)
{
    memset(view, 0, sizeof(*view));
    bool clock_ready = now >= 1700000000 && offset != INT32_MAX;
    struct tm local;
    int local_hour = -1;
    time_t shifted = clock_ready ? now + offset : 0;
    if (clock_ready && gmtime_r(&shifted, &local)) {
        strftime(view->time, sizeof(view->time), "%H:%M", &local);
        strftime(view->date, sizeof(view->date), "%a, %d %b", &local);
        local_hour = local.tm_hour;
    } else {
        snprintf(view->time, sizeof(view->time), "--:--");
        snprintf(view->date, sizeof(view->date), "Waiting for local time");
    }
    const cJSON *weather = cJSON_GetObjectItemCaseSensitive(root, "weather");
    const cJSON *temperature = cJSON_GetObjectItemCaseSensitive(weather, "temperature");
    const cJSON *code = cJSON_GetObjectItemCaseSensitive(weather, "code");
    const cJSON *updated = cJSON_GetObjectItemCaseSensitive(weather, "updated_at");
    const char *weather_state = muse_home_text(weather, "state");
    bool stale = strcmp(weather_state, "ready") != 0 ||
        (cJSON_IsNumber(updated) && now - updated->valuedouble >= 1200);
    if (cJSON_IsNumber(temperature) && cJSON_IsNumber(code)) {
        snprintf(view->weather, sizeof(view->weather), "%.0f C  |  %s", temperature->valuedouble,
                 condition(code->valueint));
        const cJSON *low = cJSON_GetObjectItemCaseSensitive(weather, "low");
        const cJSON *high = cJSON_GetObjectItemCaseSensitive(weather, "high");
        const cJSON *rain = cJSON_GetObjectItemCaseSensitive(weather, "rain_probability");
        if (cJSON_IsNumber(low) && cJSON_IsNumber(high) && cJSON_IsNumber(rain))
            snprintf(view->weather_detail, sizeof(view->weather_detail), "%s%.0f-%.0f C  /  Rain %.0f%%",
                     stale ? "Cached: " : "", low->valuedouble, high->valuedouble, rain->valuedouble);
        else snprintf(view->weather_detail, sizeof(view->weather_detail), "Forecast unavailable");
    } else {
        snprintf(view->weather, sizeof(view->weather), "%s",
                 !strcmp(weather_state, "not_configured") ? "Set your weather location" :
                 !strcmp(weather_state, "unavailable") ? "Weather unavailable" : "Updating weather...");
        snprintf(view->weather_detail, sizeof(view->weather_detail), "Tap for details");
    }
    const cJSON *reminders = cJSON_GetObjectItemCaseSensitive(root, "reminders");
    const cJSON *first = cJSON_GetArrayItem(reminders, 0);
    const cJSON *count = cJSON_GetObjectItemCaseSensitive(root, "reminder_count");
    if (cJSON_IsObject(first)) {
        preview(view->reminder, sizeof(view->reminder), muse_home_text(first, "title"));
        const cJSON *due = cJSON_GetObjectItemCaseSensitive(first, "due");
        if (clock_ready && cJSON_IsNumber(due)) {
            shifted = (time_t)due->valuedouble + offset;
            if (gmtime_r(&shifted, &local)) {
                char date[40];
                strftime(date, sizeof(date), "%d %b %H:%M", &local);
                snprintf(view->reminder_detail, sizeof(view->reminder_detail), "%s%s  /  %.0f active",
                         due->valuedouble < now ? "Overdue: " : "", date,
                         cJSON_IsNumber(count) ? count->valuedouble : (double)cJSON_GetArraySize(reminders));
            }
        } else snprintf(view->reminder_detail, sizeof(view->reminder_detail), "Waiting for local time");
    } else {
        snprintf(view->reminder, sizeof(view->reminder), "%s",
                 cJSON_IsArray(reminders) ? "You're all caught up" : "Syncing reminders...");
        snprintf(view->reminder_detail, sizeof(view->reminder_detail), "Tap to view reminders and timers");
    }
    const cJSON *briefing = cJSON_GetObjectItemCaseSensitive(root, "briefing");
    const cJSON *evening = cJSON_GetObjectItemCaseSensitive(root, "evening");
    if (*muse_home_text(evening, "summary") && clock_ready && local_hour >= 17) {
        briefing = evening;
        view->evening = true;
    }
    const char *summary = muse_home_text(briefing, "summary");
    if (*summary) preview(view->briefing, sizeof(view->briefing), summary);
    else snprintf(view->briefing, sizeof(view->briefing), "%s",
                  !strcmp(muse_home_text(briefing, "state"), "building") ? "Building your daily briefing..." :
                  "Tap for your daily briefing");
    const char *brief_date = muse_home_text(briefing, "date");
    if (*summary && clock_ready) {
        char today[16];
        shifted = now + offset;
        if (gmtime_r(&shifted, &local)) {
            strftime(today, sizeof(today), "%Y-%m-%d", &local);
            if (strncmp(today, brief_date, 10)) snprintf(view->briefing, sizeof(view->briefing),
                                                      "Previous briefing. Tap to refresh.");
        }
    }
    const cJSON *served = cJSON_GetObjectItemCaseSensitive(root, "served_at");
    const cJSON *copilot = cJSON_GetObjectItemCaseSensitive(root, "copilot");
    const cJSON *task = cJSON_GetArrayItem(cJSON_GetObjectItemCaseSensitive(copilot, "tasks"), 0);
    const char *task_state = muse_home_text(task, "status");
    const cJSON *task_updated = cJSON_GetObjectItemCaseSensitive(task, "updated_at");
    bool active = !strcmp(task_state, "working") || !strcmp(task_state, "waiting") || !strcmp(task_state, "stopping");
    view->task_visible = cJSON_IsObject(task) && (active || (cJSON_IsNumber(task_updated) &&
        now >= task_updated->valuedouble && now - task_updated->valuedouble < 600));
    if (view->task_visible) snprintf(view->copilot, sizeof(view->copilot), "%s / %s\n%.160s",
        muse_home_text(task, "project"), task_state, muse_home_text(task, "summary"));
    if (!wifi_connected) snprintf(view->footer, sizeof(view->footer), "Wi-Fi offline / cached info");
    else if (!cJSON_IsNumber(served)) snprintf(view->footer, sizeof(view->footer), "Connecting to your Mac...");
    else if (now >= 1700000000 && now - served->valuedouble > 90)
        snprintf(view->footer, sizeof(view->footer), "Mac offline / cached info");
    else if (cJSON_IsObject(copilot)) snprintf(view->footer, sizeof(view->footer), "Mac live / Copilot %s",
                                             muse_home_text(copilot, "state"));
    else snprintf(view->footer, sizeof(view->footer), "Live from your Mac");
}
