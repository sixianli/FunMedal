#include "events.h"

#include <stdio.h>
#include <string.h>

static const char *const NAMES[EVENT_KINDS] = {
    [EVENT_NONE] = "none", [EVENT_HELLO] = "hello", [EVENT_CHEER] = "cheer", [EVENT_WAIT] = "wait",
    [EVENT_ERROR] = "error",
};

static int64_t duration(event_kind_t kind)
{
    switch (kind) {
    case EVENT_WAIT:
    case EVENT_ERROR:
        return EVENTS_ALERT_US;
    case EVENT_CHEER:
        return EVENTS_CHEER_US;
    case EVENT_HELLO:
        return EVENTS_HELLO_US;
    default:
        return 0;
    }
}

static int rank(event_kind_t kind)
{
    return kind == EVENT_ERROR ? EVENT_WAIT : (int)kind;
}

static bool active(const events_t *events, int64_t now_us)
{
    return events->kind != EVENT_NONE && now_us - events->start_us < duration(events->kind);
}

static void reset(events_t *events, int64_t now_us, bool hello_due)
{
    memset(events, 0, sizeof *events);
    events->daily_us = now_us;
    events->hello_due = hello_due;
}

void events_init(events_t *events, int64_t now_us)
{
    reset(events, now_us, false);
}

void events_link(events_t *events, int64_t now_us)
{
    reset(events, now_us, true);
}

void events_lose(events_t *events, int64_t now_us)
{
    reset(events, now_us, false);
}

const char *events_name(event_kind_t kind)
{
    return kind < EVENT_KINDS ? NAMES[kind] : NAMES[EVENT_NONE];
}

bool events_kind(const char *name, event_kind_t *kind)
{
    for (int k = EVENT_HELLO; k < EVENT_KINDS; k++) {
        if (strcmp(name, NAMES[k]) == 0) {
            *kind = (event_kind_t)k;
            return true;
        }
    }
    return false;
}

bool events_start(events_t *events, event_kind_t kind, const char *name, int64_t now_us)
{
    if (kind <= EVENT_NONE || kind >= EVENT_KINDS) {
        return false;
    }
    if (active(events, now_us) && rank(kind) < rank(events->kind)) {
        return false;
    }
    events->kind = kind;
    events->start_us = now_us;
    snprintf(events->name, sizeof events->name, "%s", name);
    return true;
}

static int previous_state(const events_t *events, uint8_t id)
{
    for (int i = 0; i < events->shown; i++) {
        if (events->ids[i] == id) {
            return events->states[i];
        }
    }
    return 0;
}

static const row_t *pick(const rows_frame_t *frame, const events_t *events, event_kind_t *kind)
{
    const row_t *alert = NULL;
    const row_t *cheer = NULL;
    for (int i = 0; i < frame->shown; i++) {
        const row_t *row = &frame->rows[i];
        if (row->fresh && row->state == ROW_ERROR && (alert == NULL || alert->state != ROW_ERROR)) {
            alert = row;
        } else if (row->fresh && row->state == ROW_WAITING && alert == NULL) {
            alert = row;
        } else if (row->state == ROW_DONE && cheer == NULL && previous_state(events, row->id) == ROW_RUNNING) {
            cheer = row;
        }
    }
    if (alert != NULL) {
        *kind = alert->state == ROW_ERROR ? EVENT_ERROR : EVENT_WAIT;
        return alert;
    }
    *kind = cheer != NULL ? EVENT_CHEER : EVENT_NONE;
    return cheer;
}

event_kind_t events_heard(events_t *events, const rows_frame_t *frame, bool lit, int64_t now_us)
{
    event_kind_t kind;
    const row_t *row = pick(frame, events, &kind);
    bool hello = events->hello_due;
    events->hello_due = false;
    events->shown = frame->shown;
    for (int i = 0; i < frame->shown; i++) {
        events->ids[i] = frame->rows[i].id;
        events->states[i] = (uint8_t)frame->rows[i].state;
    }
    if (!lit && kind != EVENT_WAIT && kind != EVENT_ERROR) {
        return EVENT_NONE;
    }
    if (kind == EVENT_NONE && hello) {
        return events_start(events, EVENT_HELLO, "", now_us) ? EVENT_HELLO : EVENT_NONE;
    }
    if (kind == EVENT_NONE) {
        return EVENT_NONE;
    }
    return events_start(events, kind, row->name, now_us) ? kind : EVENT_NONE;
}

void events_view(const events_t *events, int64_t now_us, event_view_t *out, int64_t *daily_us)
{
    memset(out, 0, sizeof *out);
    if (active(events, now_us)) {
        out->kind = events->kind;
        out->ms = (uint32_t)((now_us - events->start_us) / 1000);
        snprintf(out->name, sizeof out->name, "%s", events->name);
        *daily_us = now_us;
        return;
    }
    *daily_us = events->kind != EVENT_NONE ? events->start_us + duration(events->kind) : events->daily_us;
}
