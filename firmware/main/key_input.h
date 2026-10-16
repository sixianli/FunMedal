#pragma once

#include "esp_err.h"
#include "keys.h"

typedef void (*key_input_handler_t)(const key_event_t *event);

esp_err_t key_input_start(key_input_handler_t handler);
void key_input_stream(int seconds);
int key_input_take_max_gap_ms(void);
uint32_t key_input_adc_errors(void);
#define KEY_INJECT_HOLD_MAX_MS 20000

typedef enum {
    KEY_INJECT_TAP,
    KEY_INJECT_LONG,
    KEY_INJECT_HOLD,
} key_inject_kind_t;

bool key_input_inject(key_id_t key, key_inject_kind_t kind, uint32_t hold_ms);
