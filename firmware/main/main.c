#include <string.h>

#include "app.h"
#include "audio.h"
#include "ble_link.h"
#include "console.h"
#include "display.h"
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
#include "screen.h"

#define TICK_US (THEME_TICK_MS * 1000LL)
#define THEME_NAMESPACE "funmedal"
#define THEME_KEY "theme"
#define THEME_SAVE_DELAY_US 1000000LL

static const char *TAG = "app";
static SemaphoreHandle_t lock;
static TaskHandle_t display_task_handle;
static scene_t scene;
static uint32_t draws;
static uint32_t draw_errors;
static uint32_t shown_tick;
static const char *top_state = "idle";
static rows_table_t rows_table;
static esp_timer_handle_t theme_save_timer;

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

void app_rows_link(void)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    rows_link(&rows_table, esp_timer_get_time());
    xSemaphoreGive(lock);
}

void app_rows_heard(rows_kind_t kind, const rows_frame_t *frame)
{
    const char *top = kind == ROWS_TABLE ? rows_top_name(frame) : NULL;
    xSemaphoreTake(lock, portMAX_DELAY);
    rows_heard(&rows_table, kind, frame, esp_timer_get_time());
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
}

void app_rows_lose(void)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    rows_lose(&rows_table);
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
    bool silent = rows_silent(&rows_table, esp_timer_get_time());
    if (silent) {
        rows_lose(&rows_table);
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
    return (uint32_t)((TICK_US - now % TICK_US + 999) / 1000);
}

static void display_task(void *arg)
{
    static scene_t snapshot;
    region_t drawn[DIRTY_MAX_REGIONS];
    int drawn_count = 0;
    bool valid = false;
    uint32_t drawn_generation = 0;
    uint32_t wait_ms = 0;
    for (;;) {
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(wait_ms));
        drop_silent_link();
        wait_ms = take_snapshot(&snapshot);
        region_t regions[DIRTY_MAX_REGIONS];
        int count = scene_regions(&snapshot, regions);
        uint32_t generation = display_generation();
        bool same_panel = valid && generation == drawn_generation;
        span_t spans[DIRTY_MAX_REGIONS];
        int span_count = dirty_spans(drawn, same_panel ? drawn_count : 0, regions, count, spans);
        if (span_count == 0) {
            continue;
        }
        esp_err_t err = display_draw_spans(&snapshot, spans, span_count, NULL, NULL);
        valid = err == ESP_OK;
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
    }
}

void app_set_pairing(scene_pairing_t pairing, uint32_t code)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    scene.pairing = pairing;
    scene.pair_code = code;
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
    scene.theme = load_theme();
    const esp_timer_create_args_t save_args = {.callback = save_theme, .name = "theme_save"};
    ESP_ERROR_CHECK(esp_timer_create(&save_args, &theme_save_timer));
    bool fonts_ok = font_init();
    ESP_ERROR_CHECK(display_init(&DEFAULT_DISPLAY));
    xTaskCreate(display_task, "display", 4096, NULL, 5, &display_task_handle);
    ESP_ERROR_CHECK(console_start());
    if (!fonts_ok) {
        ESP_LOGE(TAG, "embedded fonts are damaged");
        console_event("FONT damaged\n");
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
