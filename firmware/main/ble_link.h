#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

esp_err_t ble_link_start(void);
bool ble_link_connected(void);
uint32_t ble_link_writes(void);
bool ble_link_send_decision(uint32_t id, bool allow);
void ble_link_confirm_pairing(bool accept);
void ble_link_security(char *out, size_t size);
int ble_link_unpair(void);
