#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "rows.h"

#define EVENTS_ALERT_US (6000 * 1000LL)
#define EVENTS_CHEER_US (3000 * 1000LL)
#define EVENTS_HELLO_US (3000 * 1000LL)

typedef enum {
    EVENT_NONE,
    EVENT_HELLO,
    EVENT_CHEER,
    EVENT_WAIT,
    EVENT_ERROR,
    EVENT_KINDS,
} event_kind_t;

typedef struct {
    event_kind_t kind;
    uint32_t ms;
    char name[ROWS_NAME_MAX + 1];
} event_view_t;

typedef struct {
    event_kind_t kind;
    int64_t start_us;
    int64_t daily_us;
    char name[ROWS_NAME_MAX + 1];
    bool hello_due;
    uint8_t shown;
    uint8_t ids[ROWS_MAX];
    uint8_t states[ROWS_MAX];
} events_t;

void events_init(events_t *events, int64_t now_us);
void events_link(events_t *events, int64_t now_us);
void events_lose(events_t *events, int64_t now_us);
event_kind_t events_heard(events_t *events, const rows_frame_t *frame, bool lit, int64_t now_us);
bool events_start(events_t *events, event_kind_t kind, const char *name, int64_t now_us);
void events_view(const events_t *events, int64_t now_us, event_view_t *out, int64_t *daily_us);
const char *events_name(event_kind_t kind);
bool events_kind(const char *name, event_kind_t *kind);
