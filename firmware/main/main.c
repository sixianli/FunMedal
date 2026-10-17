#include <string.h>

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
#include "voice.h"

#define FRAME_PERIOD_MS 500

static const char *TAG = "app";
static SemaphoreHandle_t lock;
static TaskHandle_t display_task_handle;
static scene_t scene = {.state = PET_IDLE};
static int frame_mode = APP_FRAME_AUTO;
static uint32_t draws;
static uint32_t draw_errors;
static int shown_frame = -1;
static uint8_t voice_pixels[VOICE_IMAGE_BYTES];

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

void app_set_prompt(const prompt_t *prompt)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    bool changed = !prompt_equal(&scene.prompt, prompt);
    if (changed) {
        scene.prompt = *prompt;
        scene.decision = SCENE_DECISION_NONE;
    }
    xSemaphoreGive(lock);
    if (!changed) {
        return;
    }
    xTaskNotifyGive(display_task_handle);
    if (prompt->active) {
        console_event("PROMPT %lu %u/%u %s\n", (unsigned long)prompt->id, (unsigned)prompt->index,
                      (unsigned)prompt->count, prompt->tool);
    } else {
        console_event("PROMPT none\n");
    }
}

void app_set_voice(scene_voice_t voice, voice_result_t result)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    scene.voice = voice;
    scene.voice_result = result;
    xSemaphoreGive(lock);
    xTaskNotifyGive(display_task_handle);
}

void app_set_voice_image(const uint8_t *pixels)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    if (pixels != NULL) {
        memcpy(voice_pixels, pixels, sizeof(voice_pixels));
    }
    scene.voice_image = pixels != NULL ? voice_pixels : NULL;
    xSemaphoreGive(lock);
}

void app_set_pairing(scene_pairing_t pairing, uint32_t code)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    scene.pairing = pairing;
    scene.pair_code = code;
    xSemaphoreGive(lock);
    xTaskNotifyGive(display_task_handle);
}

static bool mark_decision(uint32_t id, scene_decision_t decision)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    bool open = scene.prompt.active && scene.prompt.id == id && scene.decision == SCENE_DECISION_NONE;
    if (open) {
        scene.decision = decision;
    }
    xSemaphoreGive(lock);
    return open;
}

static void unmark_decision(uint32_t id)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    if (scene.prompt.active && scene.prompt.id == id) {
        scene.decision = SCENE_DECISION_NONE;
    }
    xSemaphoreGive(lock);
}

static void decide(uint32_t id, bool allow)
{
    if (!mark_decision(id, allow ? SCENE_DECISION_ALLOW : SCENE_DECISION_DENY)) {
        return;
    }
    const char *word = allow ? "allow" : "deny";
    if (!ble_link_send_decision(id, allow)) {
        unmark_decision(id);
        console_event("DECISION %lu %s unsent\n", (unsigned long)id, word);
        return;
    }
    xTaskNotifyGive(display_task_handle);
    console_event("DECISION %lu %s\n", (unsigned long)id, word);
}

typedef struct {
    bool armed;
    uint32_t id;
} held_approval_t;

static held_approval_t held_approval;

static void on_pairing_key(const scene_t *now, const key_event_t *event)
{
    if (now->pairing == SCENE_PAIRING_NUMCMP && event->type == KEY_EVENT_PRESS
        && (event->key == KEY_OK || event->key == KEY_DOWN)) {
        ble_link_confirm_pairing(event->key == KEY_OK);
    }
}

static void on_ok_key(const scene_t *now, bool open, key_event_type_t type)
{
    if (type == KEY_EVENT_PRESS) {
        held_approval = (held_approval_t){open, now->prompt.id};
        return;
    }
    if (type == KEY_EVENT_LONG && held_approval.armed && open && now->prompt.id == held_approval.id) {
        decide(held_approval.id, true);
    }
    held_approval.armed = false;
}

static void on_key(const key_event_t *event)
{
    scene_t now;
    app_get_scene(&now);
    if (now.pairing != SCENE_PAIRING_NONE) {
        held_approval.armed = false;
        on_pairing_key(&now, event);
        return;
    }
    if (event->key == KEY_UP) {
        if (event->type == KEY_EVENT_PRESS) {
            voice_press();
        } else if (event->type == KEY_EVENT_RELEASE) {
            voice_release();
        }
        return;
    }
    bool open = now.voice == SCENE_VOICE_NONE && now.prompt.active && now.decision == SCENE_DECISION_NONE;
    if (event->key == KEY_OK) {
        on_ok_key(&now, open, event->type);
    } else if (event->key == KEY_DOWN && event->type == KEY_EVENT_PRESS && open) {
        decide(now.prompt.id, false);
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
    ESP_ERROR_CHECK(display_init(&DEFAULT_DISPLAY));
    xTaskCreate(display_task, "display", 4096, NULL, 5, &display_task_handle);
    ESP_ERROR_CHECK(console_start());
    ESP_ERROR_CHECK(key_input_start(on_key));
    ESP_ERROR_CHECK(ble_link_start());
    err = voice_start();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "voice unavailable: %s", esp_err_to_name(err));
        console_event("VOICE unavailable %s\n", esp_err_to_name(err));
    }
    console_event("READY funmedal-0.1\n");
}
