#pragma once

#include <stdbool.h>
#include <stdint.h>

#define KEYS_SAMPLE_MS 10
#define KEYS_STABLE_SAMPLES 3
#define KEYS_LONG_PRESS_MS 700

typedef enum {
    KEY_UNKNOWN = -1,
    KEY_NONE = 0,
    KEY_OK,
    KEY_UP,
    KEY_DOWN,
    KEY_COUNT,
} key_id_t;

typedef enum {
    KEY_EVENT_PRESS,
    KEY_EVENT_RELEASE,
    KEY_EVENT_LONG,
} key_event_type_t;

typedef struct {
    key_id_t key;
    key_event_type_t type;
} key_event_t;

typedef struct {
    key_id_t key;
    int center_mv;
} key_level_t;

typedef struct {
    key_id_t candidate;
    int candidate_count;
    key_id_t stable;
    uint32_t pressed_at_ms;
    bool long_sent;
} keys_debouncer_t;

extern const key_level_t keys_board_levels[];
extern const int keys_board_level_count;
extern const int keys_board_tolerance_mv;

key_id_t keys_classify(int mv, const key_level_t *levels, int count, int tolerance_mv);
void keys_debouncer_init(keys_debouncer_t *d);
int keys_debounce_update(keys_debouncer_t *d, key_id_t raw, uint32_t now_ms, key_event_t *events, int max_events);
const char *keys_name(key_id_t key);
const char *keys_event_name(key_event_type_t type);
