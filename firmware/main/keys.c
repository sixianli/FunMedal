#include "keys.h"

#include <stdlib.h>

key_id_t keys_classify(int mv, const key_level_t *levels, int count, int tolerance_mv)
{
    key_id_t best = KEY_UNKNOWN;
    int best_distance = tolerance_mv + 1;
    for (int i = 0; i < count; i++) {
        int distance = abs(mv - levels[i].center_mv);
        if (distance < best_distance) {
            best = levels[i].key;
            best_distance = distance;
        }
    }
    return best;
}

void keys_debouncer_init(keys_debouncer_t *d)
{
    d->candidate = KEY_NONE;
    d->candidate_count = 0;
    d->stable = KEY_NONE;
    d->pressed_at_ms = 0;
    d->long_sent = false;
}

static int emit(key_event_t *events, int max_events, int n, key_id_t key, key_event_type_t type)
{
    if (n < max_events) {
        events[n].key = key;
        events[n].type = type;
        return n + 1;
    }
    return n;
}

int keys_debounce_update(keys_debouncer_t *d, key_id_t raw, uint32_t now_ms, key_event_t *events, int max_events)
{
    int n = 0;
    if (raw != KEY_UNKNOWN) {
        if (raw == d->candidate) {
            if (d->candidate_count < KEYS_STABLE_SAMPLES) {
                d->candidate_count++;
            }
        } else {
            d->candidate = raw;
            d->candidate_count = 1;
        }
        if (d->candidate_count >= KEYS_STABLE_SAMPLES && d->candidate != d->stable) {
            if (d->stable != KEY_NONE) {
                n = emit(events, max_events, n, d->stable, KEY_EVENT_RELEASE);
            }
            d->stable = d->candidate;
            d->long_sent = false;
            if (d->stable != KEY_NONE) {
                d->pressed_at_ms = now_ms;
                n = emit(events, max_events, n, d->stable, KEY_EVENT_PRESS);
            }
            return n;
        }
    }
    if (d->stable != KEY_NONE && !d->long_sent && (uint32_t)(now_ms - d->pressed_at_ms) >= KEYS_LONG_PRESS_MS) {
        d->long_sent = true;
        n = emit(events, max_events, n, d->stable, KEY_EVENT_LONG);
    }
    return n;
}

const char *keys_name(key_id_t key)
{
    switch (key) {
    case KEY_NONE:
        return "none";
    case KEY_OK:
        return "ok";
    case KEY_UP:
        return "up";
    case KEY_DOWN:
        return "down";
    default:
        return "unknown";
    }
}

const char *keys_event_name(key_event_type_t type)
{
    switch (type) {
    case KEY_EVENT_PRESS:
        return "press";
    case KEY_EVENT_RELEASE:
        return "release";
    default:
        return "long";
    }
}
