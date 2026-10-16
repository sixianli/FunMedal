#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

esp_err_t ble_link_start(void);
bool ble_link_connected(void);
uint32_t ble_link_writes(void);
