#include "rows.h"

#include <string.h>

#define MINUTE_US (60 * 1000000LL)

static const char *const STATE_NAMES[] = {
    [ROW_WAITING] = "waiting",
    [ROW_ERROR] = "error",
    [ROW_RUNNING] = "running",
    [ROW_DONE] = "done",
};

static bool continuation(uint8_t byte)
{
    return (byte & 0xc0) == 0x80;
}

static size_t utf8_char_size(const uint8_t *text, size_t left)
{
    uint8_t lead = text[0];
    if (lead >= 0x20 && lead < 0x7f) {
        return 1;
    }
    size_t size;
    uint8_t low = 0x80;
    uint8_t high = 0xbf;
    if (lead >= 0xc2 && lead <= 0xdf) {
        size = 2;
    } else if (lead >= 0xe0 && lead <= 0xef) {
        size = 3;
        low = lead == 0xe0 ? 0xa0 : 0x80;
        high = lead == 0xed ? 0x9f : 0xbf;
    } else if (lead >= 0xf0 && lead <= 0xf4) {
        size = 4;
        low = lead == 0xf0 ? 0x90 : 0x80;
        high = lead == 0xf4 ? 0x8f : 0xbf;
    } else {
        return 0;
    }
    if (left < size || text[1] < low || text[1] > high) {
        return 0;
    }
    for (size_t i = 2; i < size; i++) {
        if (!continuation(text[i])) {
            return 0;
        }
    }
    return size;
}

static bool valid_name(const uint8_t *text, size_t len)
{
    size_t at = 0;
    while (at < len) {
        size_t size = utf8_char_size(text + at, len - at);
        if (size == 0) {
            return false;
        }
        at += size;
    }
    return true;
}

static bool valid_minutes(row_state_t state, uint16_t minutes)
{
    return state == ROW_DONE ? minutes <= ROWS_MINUTES_CAP : minutes == ROWS_HIDDEN_MINUTES;
}

static size_t parse_row(const uint8_t *data, size_t left, row_t *row)
{
    if (left < ROWS_ROW_HEADER_SIZE) {
        return 0;
    }
    uint8_t state = data[1];
    uint8_t kind = state & ROWS_STATE_MASK;
    bool fresh = (state & ROWS_FRESH_BIT) != 0;
    bool project = (state & ROWS_PROJECT_BIT) != 0;
    uint16_t minutes = (uint16_t)(data[2] | data[3] << 8);
    uint8_t count = data[4];
    size_t name_len = data[5];
    if (data[0] == 0 || (state & ~(ROWS_STATE_MASK | ROWS_FRESH_BIT | ROWS_PROJECT_BIT)) != 0 || kind < ROW_WAITING ||
        kind > ROW_DONE) {
        return 0;
    }
    bool urgent = kind == ROW_WAITING || kind == ROW_ERROR;
    if ((fresh && !urgent) || count == 0 || (count > 1 && !urgent) || !valid_minutes((row_state_t)kind, minutes)) {
        return 0;
    }
    if (name_len > ROWS_NAME_MAX || left < ROWS_ROW_HEADER_SIZE + name_len ||
        !valid_name(data + ROWS_ROW_HEADER_SIZE, name_len)) {
        return 0;
    }
    row->id = data[0];
    row->state = (row_state_t)kind;
    row->fresh = fresh;
    row->project = project;
    row->minutes = minutes;
    row->count = count;
    memcpy(row->name, data + ROWS_ROW_HEADER_SIZE, name_len);
    row->name[name_len] = '\0';
    return ROWS_ROW_HEADER_SIZE + name_len;
}

static bool id_repeated(const rows_frame_t *frame, int count, uint8_t id)
{
    for (int i = 0; i < count; i++) {
        if (frame->rows[i].id == id) {
            return true;
        }
    }
    return false;
}

rows_kind_t rows_parse(const uint8_t *data, size_t len, rows_frame_t *out)
{
    if (len < ROWS_HEADER_SIZE || len > ROWS_FRAME_MAX || data[0] != ROWS_FORMAT ||
        (data[3] & ~ROWS_HEARTBEAT_FLAG) != 0) {
        return ROWS_INVALID;
    }
    uint8_t total = data[1];
    uint8_t shown = data[2];
    uint8_t urgent = data[4];
    if (urgent > total) {
        return ROWS_INVALID;
    }
    if (data[3] & ROWS_HEARTBEAT_FLAG) {
        return len == ROWS_HEADER_SIZE && shown == 0 ? ROWS_HEARTBEAT : ROWS_INVALID;
    }
    if (shown > ROWS_MAX || shown > total) {
        return ROWS_INVALID;
    }
    rows_frame_t frame = {.total = total, .shown = shown, .urgent = urgent};
    size_t at = ROWS_HEADER_SIZE;
    unsigned sessions = 0;
    unsigned waiting = 0;
    for (int i = 0; i < shown; i++) {
        const row_t *row = &frame.rows[i];
        size_t used = parse_row(data + at, len - at, &frame.rows[i]);
        if (used == 0 || id_repeated(&frame, i, row->id)) {
            return ROWS_INVALID;
        }
        sessions += row->count;
        waiting += row->state == ROW_WAITING || row->state == ROW_ERROR ? row->count : 0;
        at += used;
    }
    bool all = shown < ROWS_MAX;
    if (at != len || sessions > total || waiting > urgent || sessions - waiting > (unsigned)(total - urgent) ||
        (all && (sessions != total || waiting != urgent))) {
        return ROWS_INVALID;
    }
    *out = frame;
    return ROWS_TABLE;
}

const char *rows_state_name(row_state_t state)
{
    return state >= ROW_WAITING && state <= ROW_DONE ? STATE_NAMES[state] : "unknown";
}

const char *rows_top_name(const rows_frame_t *frame)
{
    if (frame->shown == 0) {
        return "idle";
    }
    return frame->rows[0].state == ROW_RUNNING ? "working" : rows_state_name(frame->rows[0].state);
}

void rows_init(rows_table_t *table)
{
    memset(table, 0, sizeof *table);
}

void rows_link(rows_table_t *table, int64_t now_us)
{
    rows_init(table);
    table->linked = true;
    table->heard_us = now_us;
    table->table_us = now_us;
}

void rows_heard(rows_table_t *table, rows_kind_t kind, const rows_frame_t *frame, int64_t now_us)
{
    table->heard_us = now_us;
    if (kind == ROWS_TABLE) {
        table->has_table = true;
        table->frame = *frame;
        table->table_us = now_us;
    }
}

bool rows_silent(const rows_table_t *table, int64_t now_us)
{
    return table->linked && now_us - table->heard_us >= ROWS_SILENCE_US;
}

void rows_lose(rows_table_t *table)
{
    rows_init(table);
}

uint16_t rows_minutes(const rows_table_t *table, int index, int64_t now_us)
{
    const row_t *row = &table->frame.rows[index];
    if (row->minutes == ROWS_HIDDEN_MINUTES) {
        return ROWS_HIDDEN_MINUTES;
    }
    int64_t minutes = row->minutes + (now_us - table->table_us) / MINUTE_US;
    return minutes > ROWS_MINUTES_CAP ? ROWS_MINUTES_CAP : (uint16_t)minutes;
}
