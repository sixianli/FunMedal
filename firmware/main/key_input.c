#include "key_input.h"

#include <stdatomic.h>

#include "board.h"
#include "console.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_check.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#define KEY_TASK_STACK 4096
#define INJECT_QUEUE_LEN 4

typedef struct {
    key_id_t key;
    key_inject_kind_t kind;
    uint32_t hold_ms;
} injected_press_t;

typedef struct {
    bool active;
    key_id_t key;
    uint32_t since_ms;
    uint32_t hold_ms;
    bool long_sent;
} injected_hold_t;

static const char *TAG = "keys";
static QueueHandle_t injected;

static adc_oneshot_unit_handle_t adc;
static adc_cali_handle_t cali;
static key_input_handler_t on_event;
static volatile int64_t stream_until_us;
static _Atomic uint32_t max_gap_us;
static _Atomic uint32_t adc_errors;

void key_input_stream(int seconds)
{
    stream_until_us = esp_timer_get_time() + (int64_t)seconds * 1000000;
}

uint32_t key_input_adc_errors(void)
{
    return atomic_load(&adc_errors);
}

int key_input_take_max_gap_ms(void)
{
    return (int)(atomic_exchange(&max_gap_us, 0) / 1000);
}

bool key_input_inject(key_id_t key, key_inject_kind_t kind, uint32_t hold_ms)
{
    injected_press_t press = {key, kind, hold_ms};
    return xQueueSend(injected, &press, 0) == pdTRUE;
}

static void deliver(const key_event_t *events, int n)
{
    for (int i = 0; i < n; i++) {
        console_event("KEY %s %s\n", keys_event_name(events[i].type), keys_name(events[i].key));
        if (on_event != NULL) {
            on_event(&events[i]);
        }
    }
}

static void continue_hold(injected_hold_t *hold, uint32_t now_ms)
{
    uint32_t held_ms = now_ms - hold->since_ms;
    if (!hold->long_sent && held_ms >= KEYS_LONG_PRESS_MS && hold->hold_ms >= KEYS_LONG_PRESS_MS) {
        hold->long_sent = true;
        deliver(&(key_event_t){hold->key, KEY_EVENT_LONG}, 1);
    }
    if (held_ms >= hold->hold_ms) {
        hold->active = false;
        deliver(&(key_event_t){hold->key, KEY_EVENT_RELEASE}, 1);
    }
}

static void deliver_injected(injected_hold_t *hold, uint32_t now_ms)
{
    if (hold->active) {
        continue_hold(hold, now_ms);
        return;
    }
    injected_press_t press;
    while (xQueueReceive(injected, &press, 0) == pdTRUE) {
        if (press.kind == KEY_INJECT_HOLD) {
            *hold = (injected_hold_t){true, press.key, now_ms, press.hold_ms, false};
            deliver(&(key_event_t){press.key, KEY_EVENT_PRESS}, 1);
            return;
        }
        key_event_t events[3];
        int n = 0;
        events[n++] = (key_event_t){press.key, KEY_EVENT_PRESS};
        if (press.kind == KEY_INJECT_LONG) {
            events[n++] = (key_event_t){press.key, KEY_EVENT_LONG};
        }
        events[n++] = (key_event_t){press.key, KEY_EVENT_RELEASE};
        deliver(events, n);
    }
}

static void key_task(void *arg)
{
    keys_debouncer_t debouncer;
    keys_debouncer_init(&debouncer);
    injected_hold_t hold = {.active = false};
    TickType_t wake = xTaskGetTickCount();
    bool streaming = false;
    uint32_t sample = 0;
    int64_t last_us = esp_timer_get_time();
    for (;;) {
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(KEYS_SAMPLE_MS));
        int64_t now_us = esp_timer_get_time();
        uint32_t gap_us = (uint32_t)(now_us - last_us);
        uint32_t seen = atomic_load(&max_gap_us);
        while (gap_us > seen && !atomic_compare_exchange_weak(&max_gap_us, &seen, gap_us)) {
        }
        last_us = now_us;
        int raw = 0;
        int mv = 0;
        if (adc_oneshot_read(adc, BOARD_KEY_ADC_CHANNEL, &raw) != ESP_OK ||
            adc_cali_raw_to_voltage(cali, raw, &mv) != ESP_OK) {
            atomic_fetch_add(&adc_errors, 1);
            continue;
        }
        key_id_t key = keys_classify(mv, keys_board_levels, keys_board_level_count, keys_board_tolerance_mv);
        if (now_us < stream_until_us) {
            streaming = true;
            if (sample++ % 2 == 0) {
                console_event("ADC %d %d %s\n", mv, raw, keys_name(key));
            }
        } else if (streaming) {
            streaming = false;
            console_event("ADC END\n");
        }
        key_event_t events[4];
        uint32_t now_ms = (uint32_t)(now_us / 1000);
        int n = keys_debounce_update(&debouncer, key, now_ms, events, 4);
        deliver(events, n);
        deliver_injected(&hold, now_ms);
    }
}

esp_err_t key_input_start(key_input_handler_t handler)
{
    on_event = handler;
    injected = xQueueCreate(INJECT_QUEUE_LEN, sizeof(injected_press_t));
    ESP_RETURN_ON_FALSE(injected != NULL, ESP_ERR_NO_MEM, TAG, "inject queue");
    adc_oneshot_unit_init_cfg_t unit = {.unit_id = ADC_UNIT_1};
    ESP_RETURN_ON_ERROR(adc_oneshot_new_unit(&unit, &adc), TAG, "adc unit");
    adc_oneshot_chan_cfg_t channel = {.atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT};
    ESP_RETURN_ON_ERROR(adc_oneshot_config_channel(adc, BOARD_KEY_ADC_CHANNEL, &channel), TAG, "adc channel");
    adc_cali_curve_fitting_config_t cali_config = {
        .unit_id = ADC_UNIT_1,
        .chan = BOARD_KEY_ADC_CHANNEL,
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    ESP_RETURN_ON_ERROR(adc_cali_create_scheme_curve_fitting(&cali_config, &cali), TAG, "adc cali");
    BaseType_t ok = xTaskCreate(key_task, "keys", KEY_TASK_STACK, NULL, 6, NULL);
    return ok == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}
