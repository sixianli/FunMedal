#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "rows.h"

#define POWER_IDLE_US (5 * 60 * 1000000LL)

typedef enum {
    POWER_KEEP,
    POWER_DIM,
    POWER_LIGHT,
} power_change_t;

typedef struct {
    bool usb;
    bool dark;
    int64_t idle_us;
    int64_t quiet_since_us;
} power_t;

void power_init(power_t *power, int64_t now_us);
power_change_t power_set_idle(power_t *power, int64_t idle_us, int64_t now_us);
power_change_t power_touch(power_t *power, bool wakes, int64_t now_us);
power_change_t power_check(power_t *power, bool usb, bool busy, int64_t now_us);
bool power_rows_differ(const rows_frame_t *before, const rows_frame_t *after);
