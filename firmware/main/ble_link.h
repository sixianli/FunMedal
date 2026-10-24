#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#define BLE_REFUSE_MAX_TIMES 9

typedef enum { BLE_REFUSE_NONE, BLE_REFUSE_VERSION } ble_refusal_t;

esp_err_t ble_link_start(void);
bool ble_link_connected(void);
uint32_t ble_link_writes(void);
bool ble_link_ready(void);
void ble_link_confirm_pairing(bool accept);
void ble_link_security(char *out, size_t size);
int ble_link_unpair(void);
int ble_link_disconnect(void);
esp_err_t ble_link_forget_layout(void);
uint16_t ble_link_changed_handle(void);
void ble_link_refuse_next(ble_refusal_t what, int times);
