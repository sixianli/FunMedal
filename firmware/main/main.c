#include "app.h"
#include "ble_link.h"
#include "console.h"
#include "display.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "key_input.h"
#include "nvs_flash.h"

#define FRAME_PERIOD_MS 500

static const char *TAG = "app";
static SemaphoreHandle_t lock;
static TaskHandle_t display_task_handle;
static scene_t scene = {.state = PET_IDLE};
static int frame_mode = APP_FRAME_AUTO;
static uint32_t draws;
static uint32_t draw_errors;
static int shown_frame = -1;

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

static void display_task(void *arg)
{
    for (;;) {
        bool notified = ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(FRAME_PERIOD_MS)) > 0;
        xSemaphoreTake(lock, portMAX_DELAY);
        if (!notified && frame_mode == APP_FRAME_AUTO) {
            scene.frame = (uint8_t)((scene.frame + 1) % PET_FRAMES);
        }
        scene_t snapshot = scene;
        xSemaphoreGive(lock);
        esp_err_t err = display_draw(&snapshot);
        xSemaphoreTake(lock, portMAX_DELAY);
        if (err == ESP_OK) {
            draws++;
            shown_frame = snapshot.frame;
        } else {
            draw_errors++;
        }
        xSemaphoreGive(lock);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "draw failed: %s", esp_err_to_name(err));
        }
    }
}

static void on_key(const key_event_t *event)
{
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
    ESP_ERROR_CHECK(display_init(&DEFAULT_DISPLAY));
    xTaskCreate(display_task, "display", 4096, NULL, 5, &display_task_handle);
    ESP_ERROR_CHECK(console_start());
    ESP_ERROR_CHECK(key_input_start(on_key));
    ESP_ERROR_CHECK(ble_link_start());
    console_event("READY funmedal-0.1\n");
}
