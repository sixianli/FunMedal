#include "power.h"

#include <string.h>

static power_change_t light(power_t *power, int64_t now_us)
{
    power->quiet_since_us = now_us;
    if (!power->dark) {
        return POWER_KEEP;
    }
    power->dark = false;
    return POWER_LIGHT;
}

void power_init(power_t *power, int64_t now_us)
{
    *power = (power_t){.usb = false, .dark = false, .idle_us = POWER_IDLE_US, .quiet_since_us = now_us};
}

power_change_t power_set_idle(power_t *power, int64_t idle_us, int64_t now_us)
{
    power->idle_us = idle_us;
    return light(power, now_us);
}

power_change_t power_touch(power_t *power, bool wakes, int64_t now_us)
{
    if (power->dark && !wakes) {
        return POWER_KEEP;
    }
    return light(power, now_us);
}

power_change_t power_check(power_t *power, bool usb, bool busy, int64_t now_us)
{
    bool unplugged = power->usb && !usb;
    power->usb = usb;
    if (usb) {
        return light(power, now_us);
    }
    if (power->dark) {
        return POWER_KEEP;
    }
    if (unplugged || busy) {
        power->quiet_since_us = now_us;
        return POWER_KEEP;
    }
    if (now_us - power->quiet_since_us < power->idle_us) {
        return POWER_KEEP;
    }
    power->dark = true;
    return POWER_DIM;
}

bool power_rows_differ(const rows_frame_t *before, const rows_frame_t *after)
{
    if (before->total != after->total || before->shown != after->shown || before->urgent != after->urgent) {
        return true;
    }
    for (int i = 0; i < after->shown; i++) {
        const row_t *a = &before->rows[i];
        const row_t *b = &after->rows[i];
        if (a->id != b->id || a->state != b->state || a->project != b->project || a->count != b->count ||
            strcmp(a->name, b->name) != 0) {
            return true;
        }
    }
    return false;
}
