#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define ROWS_FORMAT 1
#define ROWS_MAX 6
#define ROWS_NAME_MAX 32
#define ROWS_HEADER_SIZE 4
#define ROWS_ROW_HEADER_SIZE 5
#define ROWS_FRAME_MAX (ROWS_HEADER_SIZE + ROWS_MAX * (ROWS_ROW_HEADER_SIZE + ROWS_NAME_MAX))
#define ROWS_HEARTBEAT_FLAG 0x01
#define ROWS_FRESH_BIT 0x80
#define ROWS_PROJECT_BIT 0x40
#define ROWS_STATE_MASK 0x0f
#define ROWS_HIDDEN_MINUTES 65535
#define ROWS_MINUTES_CAP 44640
#define ROWS_SILENCE_US (90 * 1000000LL)

typedef enum {
    ROW_WAITING = 1,
    ROW_ERROR = 2,
    ROW_RUNNING = 3,
    ROW_DONE = 4,
} row_state_t;

typedef enum {
    ROWS_INVALID,
    ROWS_HEARTBEAT,
    ROWS_TABLE,
} rows_kind_t;

typedef struct {
    uint8_t id;
    row_state_t state;
    bool fresh;
    bool project;
    uint16_t minutes;
    char name[ROWS_NAME_MAX + 1];
} row_t;

typedef struct {
    uint8_t total;
    uint8_t shown;
    row_t rows[ROWS_MAX];
} rows_frame_t;

typedef struct {
    bool linked;
    bool has_table;
    rows_frame_t frame;
    int64_t table_us;
    int64_t heard_us;
} rows_table_t;

rows_kind_t rows_parse(const uint8_t *data, size_t len, rows_frame_t *out);
const char *rows_state_name(row_state_t state);
const char *rows_top_name(const rows_frame_t *frame);
void rows_init(rows_table_t *table);
void rows_link(rows_table_t *table, int64_t now_us);
void rows_heard(rows_table_t *table, rows_kind_t kind, const rows_frame_t *frame, int64_t now_us);
bool rows_silent(const rows_table_t *table, int64_t now_us);
void rows_lose(rows_table_t *table);
uint16_t rows_minutes(const rows_table_t *table, int index, int64_t now_us);
