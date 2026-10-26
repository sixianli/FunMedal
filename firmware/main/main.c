#include <string.h>

#include "app.h"
#include "character_art.h"
#include "audio.h"
#include "driver/usb_serial_jtag.h"
#include "ble_link.h"
#include "console.h"
#include "display.h"
#include "events.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "font.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "key_input.h"
#include "link_version.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "power.h"
#include "screen.h"

#define TICK_US (THEME_TICK_MS * 1000LL)
#define THEME_NAMESPACE "funmedal"
#define THEME_KEY "theme"
#define THEME_SAVE_DELAY_US 1000000LL
#define DARK_CHECK_MS 1000

static const char *TAG = "app";
static SemaphoreHandle_t lock;
static TaskHandle_t display_task_handle;
static scene_t scene;
static uint32_t draws;
static uint32_t draw_errors;
static uint32_t shown_tick;
static const char *top_state = "idle";
static rows_table_t rows_table;
static events_t events;
static esp_timer_handle_t theme_save_timer;
static power_t power;
static bool pretend_battery;
static const char *wake_reason = "";

static const display_config_t DEFAULT_DISPLAY = {
    .invert = true,
    .bgr = false,
    .mirror_x = false,
    .mirror_y = false,
    .gap_x = 0,
    .gap_y = 0,
};

static int64_t locked_scene(scene_t *out)
{
    int64_t now = esp_timer_get_time();
    *out = scene;
    screen_from_rows(&out->screen, &rows_table, now, (uint32_t)(now / TICK_US));
    int64_t daily_us;
    events_view(&events, now, &out->screen.event, &daily_us);
    out->screen.daily = (uint32_t)((now - daily_us) / TICK_US);
    return now;
}

void app_get_scene(scene_t *out)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    locked_scene(out);
    xSemaphoreGive(lock);
}

const char *app_top_state(void)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    const char *name = top_state;
    xSemaphoreGive(lock);
    return name;
}

void app_get_draw_stats(uint32_t *done, uint32_t *failed, uint32_t *tick)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    *done = draws;
    *failed = draw_errors;
    *tick = shown_tick;
    xSemaphoreGive(lock);
}

void app_show_board(void)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    scene.test_pattern = false;
    scene.fill = false;
    xSemaphoreGive(lock);
    xTaskNotifyGive(display_task_handle);
}

static uint8_t load_theme(void)
{
    nvs_handle_t nvs;
    uint8_t value = 0;
    bool found = false;
    if (nvs_open(THEME_NAMESPACE, NVS_READONLY, &nvs) == ESP_OK) {
        found = nvs_get_u8(nvs, THEME_KEY, &value) == ESP_OK;
        nvs_close(nvs);
    }
    return screen_stored_theme(found, value);
}

static void save_theme(void *arg)
{
    (void)arg;
    xSemaphoreTake(lock, portMAX_DELAY);
    uint8_t theme = scene.theme;
    xSemaphoreGive(lock);
    nvs_handle_t nvs;
    esp_err_t err = nvs_open(THEME_NAMESPACE, NVS_READWRITE, &nvs);
    if (err == ESP_OK) {
        err = nvs_set_u8(nvs, THEME_KEY, theme);
        if (err == ESP_OK) {
            err = nvs_commit(nvs);
        }
        nvs_close(nvs);
    }
    console_event("THEME saved %u rc=%s\n", (unsigned)theme, esp_err_to_name(err));
}

uint8_t app_get_theme(void)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    uint8_t theme = scene.theme;
    xSemaphoreGive(lock);
    return theme;
}

void app_set_theme(uint8_t theme)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    scene.theme = theme;
    xSemaphoreGive(lock);
    xTaskNotifyGive(display_task_handle);
    esp_timer_stop(theme_save_timer);
    esp_timer_start_once(theme_save_timer, THEME_SAVE_DELAY_US);
    console_event("THEME %u\n", (unsigned)theme);
}

void app_set_fill(uint32_t rgb)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    scene.fill = true;
    scene.fill_rgb = rgb;
    xSemaphoreGive(lock);
    xTaskNotifyGive(display_task_handle);
}

void app_set_pattern(void)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    scene.fill = false;
    scene.test_pattern = true;
    xSemaphoreGive(lock);
    xTaskNotifyGive(display_task_handle);
}

static bool locked_touch(bool wakes, const char *reason, int64_t now)
{
    bool lit = power_touch(&power, wakes, now) == POWER_LIGHT;
    if (lit) {
        wake_reason = reason;
    }
    return lit;
}

static bool wake(const char *reason)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    bool lit = locked_touch(true, reason, esp_timer_get_time());
    xSemaphoreGive(lock);
    if (lit) {
        xTaskNotifyGive(display_task_handle);
    }
    return lit;
}

void app_get_power(app_power_t *out)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    int64_t now = esp_timer_get_time();
    *out = (app_power_t){.usb = power.usb, .pretend_battery = pretend_battery, .dark = power.dark,
                         .idle_us = power.idle_us, .quiet_us = now - power.quiet_since_us};
    xSemaphoreGive(lock);
}

void app_set_idle(int64_t idle_us)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    if (power_set_idle(&power, idle_us, esp_timer_get_time()) == POWER_LIGHT) {
        wake_reason = "idle";
    }
    xSemaphoreGive(lock);
    xTaskNotifyGive(display_task_handle);
}

void app_pretend_battery(bool on)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    pretend_battery = on;
    xSemaphoreGive(lock);
    xTaskNotifyGive(display_task_handle);
}

void app_rows_link(void)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    int64_t now = esp_timer_get_time();
    rows_link(&rows_table, now);
    events_link(&events, now);
    xSemaphoreGive(lock);
}

static void announce(event_kind_t kind, const char *name)
{
    console_event("EVENT %s %s\n", events_name(kind), name);
    if (kind != EVENT_WAIT && kind != EVENT_ERROR) {
        return;
    }
    esp_err_t err = audio_play(kind == EVENT_ERROR ? CHIME_ERROR : CHIME_WAIT);
    if (err != ESP_OK) {
        console_event("AUDIO skipped %s rc=%s\n", events_name(kind), esp_err_to_name(err));
    }
}

void app_rows_heard(rows_kind_t kind, const rows_frame_t *frame)
{
    const char *top = kind == ROWS_TABLE ? rows_top_name(frame) : NULL;
    xSemaphoreTake(lock, portMAX_DELAY);
    int64_t now = esp_timer_get_time();
    bool differs = kind == ROWS_TABLE && (!rows_table.has_table || power_rows_differ(&rows_table.frame, frame));
    rows_heard(&rows_table, kind, frame, now);
    event_kind_t started = kind == ROWS_TABLE ? events_heard(&events, frame, !power.dark, now) : EVENT_NONE;
    bool alert = started == EVENT_WAIT || started == EVENT_ERROR;
    if (differs || alert) {
        locked_touch(alert, "alert", now);
    }
    char name[ROWS_NAME_MAX + 1];
    memcpy(name, events.name, sizeof name);
    bool changed = top != NULL && strcmp(top, top_state) != 0;
    if (changed) {
        top_state = top;
    }
    xSemaphoreGive(lock);
    if (kind != ROWS_TABLE) {
        return;
    }
    xTaskNotifyGive(display_task_handle);
    app_link_in_use();
    if (changed) {
        console_event("STATE %s\n", top);
    }
    if (started != EVENT_NONE) {
        announce(started, name);
    }
}

bool app_start_event(event_kind_t kind, const char *name)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    int64_t now = esp_timer_get_time();
    bool started = events_start(&events, kind, name, now);
    if (started) {
        locked_touch(kind == EVENT_WAIT || kind == EVENT_ERROR, "alert", now);
    }
    xSemaphoreGive(lock);
    if (started) {
        xTaskNotifyGive(display_task_handle);
        announce(kind, name);
    }
    return started;
}

static void lose_rows(int64_t now)
{
    rows_lose(&rows_table);
    events_lose(&events, now);
}

void app_rows_lose(void)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    lose_rows(esp_timer_get_time());
    xSemaphoreGive(lock);
}

void app_get_rows(rows_table_t *table, int64_t *now_us)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    *table = rows_table;
    *now_us = esp_timer_get_time();
    xSemaphoreGive(lock);
}

static void drop_silent_link(void)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    int64_t now = esp_timer_get_time();
    bool silent = rows_silent(&rows_table, now);
    if (silent) {
        lose_rows(now);
    }
    xSemaphoreGive(lock);
    if (silent) {
        console_event("BLE silent rc=%d\n", ble_link_disconnect());
    }
}

static uint32_t take_snapshot(scene_t *snapshot)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    int64_t now = locked_scene(snapshot);
    xSemaphoreGive(lock);
    uint32_t wait_ms = (uint32_t)((TICK_US - now % TICK_US + 999) / 1000);
    if (screen_rising(&snapshot->screen)) {
        uint32_t rise_ms = SCREEN_RISE_MS - snapshot->screen.event.ms % SCREEN_RISE_MS;
        wait_ms = rise_ms < wait_ms ? rise_ms : wait_ms;
    }
    return wait_ms;
}

static power_change_t check_power(const scene_t *snapshot, bool *dark, const char **reason)
{
    bool usb = usb_serial_jtag_is_connected();
    bool busy = snapshot->screen.event.kind != EVENT_NONE || audio_playing();
    xSemaphoreTake(lock, portMAX_DELAY);
    power_change_t change = power_check(&power, usb && !pretend_battery, busy, esp_timer_get_time());
    if (change == POWER_LIGHT) {
        wake_reason = "usb";
    }
    *dark = power.dark;
    *reason = wake_reason;
    xSemaphoreGive(lock);
    return change;
}

static void display_task(void *arg)
{
    static scene_t snapshot;
    region_t drawn[DIRTY_MAX_REGIONS];
    int drawn_count = 0;
    bool valid = false;
    uint32_t drawn_generation = 0;
    uint32_t wait_ms = 0;
    bool lit = true;
    for (;;) {
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(wait_ms));
        drop_silent_link();
        wait_ms = take_snapshot(&snapshot);
        bool dark;
        const char *reason;
        if (check_power(&snapshot, &dark, &reason) == POWER_DIM) {
            display_backlight(0);
            console_event("POWER dark\n");
        }
        if (dark) {
            lit = false;
            wait_ms = DARK_CHECK_MS;
            continue;
        }
        region_t regions[DIRTY_MAX_REGIONS];
        int count = scene_regions(&snapshot, regions);
        uint32_t generation = display_generation();
        bool same_panel = lit && valid && generation == drawn_generation;
        span_t spans[DIRTY_MAX_REGIONS];
        int span_count = dirty_spans(drawn, same_panel ? drawn_count : 0, regions, count, spans);
        if (span_count == 0) {
            continue;
        }
        esp_err_t err = display_draw_spans(&snapshot, spans, span_count, NULL, NULL);
        valid = err == ESP_OK;
        if (!lit) {
            display_backlight(100);
            lit = true;
            console_event("POWER lit %s\n", reason);
        }
        if (valid) {
            memcpy(drawn, regions, sizeof(region_t) * (size_t)count);
            drawn_count = count;
            drawn_generation = generation;
        }
        xSemaphoreTake(lock, portMAX_DELAY);
        if (valid) {
            draws++;
            shown_tick = snapshot.screen.tick;
        } else {
            draw_errors++;
        }
        xSemaphoreGive(lock);
        if (!valid) {
            ESP_LOGE(TAG, "draw failed: %s", esp_err_to_name(err));
        }
        static scene_t after_draw;
        wait_ms = take_snapshot(&after_draw);
    }
}

void app_set_pairing(scene_pairing_t pairing, uint32_t code)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    scene.pairing = pairing;
    scene.pair_code = code;
    if (pairing != SCENE_PAIRING_NONE) {
        locked_touch(true, "pairing", esp_timer_get_time());
    }
    xSemaphoreGive(lock);
    xTaskNotifyGive(display_task_handle);
}

scene_link_t app_set_link(uint16_t peer)
{
    scene_link_t link = scene_link_for(LINK_VERSION, peer);
    xSemaphoreTake(lock, portMAX_DELAY);
    scene.link = link;
    scene.link_peer = peer;
    xSemaphoreGive(lock);
    xTaskNotifyGive(display_task_handle);
    return link;
}

void app_link_in_use(void)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    bool cleared = scene.link == SCENE_LINK_UPDATE_FIRMWARE || scene.link == SCENE_LINK_UPDATE_BRIDGE;
    if (cleared) {
        scene.link = SCENE_LINK_NONE;
    }
    xSemaphoreGive(lock);
    if (cleared) {
        xTaskNotifyGive(display_task_handle);
    }
}

static void on_pairing_key(const scene_t *now, const key_event_t *event)
{
    if (now->pairing == SCENE_PAIRING_NUMCMP && event->type == KEY_EVENT_PRESS
        && (event->key == KEY_OK || event->key == KEY_DOWN)) {
        ble_link_confirm_pairing(event->key == KEY_OK);
    }
}

static void on_key(const key_event_t *event)
{
    if (event->type == KEY_EVENT_PRESS && wake("key")) {
        return;
    }
    scene_t now;
    app_get_scene(&now);
    on_pairing_key(&now, event);
    if (event->type == KEY_EVENT_PRESS && event->key == KEY_UP) {
        app_set_theme(screen_next_theme(now.theme));
    }
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    lock = xSemaphoreCreateMutex();
    events_init(&events, esp_timer_get_time());
    power_init(&power, esp_timer_get_time());
    scene.theme = load_theme();
    const esp_timer_create_args_t save_args = {.callback = save_theme, .name = "theme_save"};
    ESP_ERROR_CHECK(esp_timer_create(&save_args, &theme_save_timer));
    bool fonts_ok = font_init();
    bool art_ok = character_art_init();
    ESP_ERROR_CHECK(display_init(&DEFAULT_DISPLAY));
    xTaskCreate(display_task, "display", 4096, NULL, 5, &display_task_handle);
    ESP_ERROR_CHECK(console_start());
    if (!fonts_ok) {
        ESP_LOGE(TAG, "embedded fonts are damaged");
        console_event("FONT damaged\n");
    }
    if (!art_ok) {
        ESP_LOGE(TAG, "embedded character pack is damaged");
        console_event("ART damaged\n");
    }
    ESP_ERROR_CHECK(key_input_start(on_key));
    ESP_ERROR_CHECK(ble_link_start());
    err = audio_start();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "audio unavailable: %s", esp_err_to_name(err));
        console_event("AUDIO unavailable %s\n", esp_err_to_name(err));
    }
    console_event("READY funmedal-0.1\n");
}
