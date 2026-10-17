#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

esp_err_t ble_link_start(void);
bool ble_link_connected(void);
uint32_t ble_link_writes(void);
bool ble_link_ready(void);
bool ble_link_send_decision(uint32_t id, bool allow);
bool ble_link_send_audio(const uint8_t *data, size_t len);
void ble_link_confirm_pairing(bool accept);
void ble_link_security(char *out, size_t size);
int ble_link_unpair(void);
esp_err_t ble_link_forget_layout(void);
uint16_t ble_link_changed_handle(void);
