#pragma once

#include "esp_err.h"
#include "keys.h"

typedef void (*key_input_handler_t)(const key_event_t *event);

esp_err_t key_input_start(key_input_handler_t handler);
void key_input_stream(int seconds);
int key_input_take_max_gap_ms(void);
