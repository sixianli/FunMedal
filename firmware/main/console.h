#pragma once

#include "esp_err.h"

esp_err_t console_start(void);
void console_printf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void console_event(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
