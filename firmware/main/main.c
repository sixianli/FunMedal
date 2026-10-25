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
#include "nvs_flash.h"
#include "screen.h"

#define PET_FRAME_TICKS 5
#define TICK_US (THEME_TICK_MS * 1000LL)

static const char *TAG = "app";
static SemaphoreHandle_t lock;
static TaskHandle_t display_task_handle;
static scene_t scene = {.state = PET_IDLE};
static int frame_mode = APP_FRAME_AUTO;
static uint32_t draws;
static uint32_t draw_errors;
static int shown_frame = -1;
static rows_table_t rows_table;

static const pet_state_t ROW_PET_STATES[] = {
    [ROW_WAITING] = PET_WAITING,
    [ROW_ERROR] = PET_ERROR,
    [ROW_RUNNING] = PET_WORKING,
    [ROW_DONE] = PET_DONE,
};

static const display_config_t DEFAULT_DISPLAY = {
    .invert = true,
    .bgr = false,
    .mirror_x = false,
    .mirror_y = false,
    .gap_x = 0,
    .gap_y = 0,
};

void app_get_scene(scene_t *out)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    *out = scene;
    xSemaphoreGive(lock);
}

void app_get_draw_stats(uint32_t *done, uint32_t *failed, int *frame)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    *done = draws;
    *failed = draw_errors;
    *frame = shown_frame;
    xSemaphoreGive(lock);
}

int app_frame_mode(void)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    int mode = frame_mode;
    xSemaphoreGive(lock);
    return mode;
}

void app_set_state(pet_state_t state)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    bool changed = scene.state != state || scene.test_pattern || scene.fill;
    scene.state = state;
    scene.test_pattern = false;
    scene.fill = false;
    if (frame_mode != APP_FRAME_AUTO) {
        scene.frame = (uint8_t)frame_mode;
    }
    xSemaphoreGive(lock);
    xTaskNotifyGive(display_task_handle);
    if (changed) {
        console_event("STATE %s\n", scene_state_name(state));
    }
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

void app_set_frame_mode(int frame)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    frame_mode = frame;
    if (frame != APP_FRAME_AUTO) {
        scene.frame = (uint8_t)frame;
    }
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
    xSemaphoreTake(lock, portMAX_DELAY);
    rows_heard(&rows_table, kind, frame, esp_timer_get_time());
    xSemaphoreGive(lock);
    if (kind != ROWS_TABLE) {
        return;
    }
    app_link_in_use();
    app_set_state(frame->shown == 0 ? PET_IDLE : ROW_PET_STATES[frame->rows[0].state]);
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
    int64_t now = esp_timer_get_time();
    uint32_t tick = (uint32_t)(now / TICK_US);
    xSemaphoreTake(lock, portMAX_DELAY);
    if (frame_mode == APP_FRAME_AUTO) {
        scene.frame = (uint8_t)(tick / PET_FRAME_TICKS % PET_FRAMES);
    }
    *snapshot = scene;
    screen_from_rows(&snapshot->screen, &rows_table, now, tick);
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
            shown_frame = snapshot.frame;
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
