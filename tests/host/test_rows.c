#include <stdio.h>
#include <string.h>

#include "rows.h"

#define SECOND_US 1000000LL
#define MINUTE_US (60 * SECOND_US)

static int failures;

static void check(int ok, const char *what)
{
    if (!ok) {
        printf("FAIL %s\n", what);
        failures++;
    }
}

static size_t frame(uint8_t *out, uint8_t total, uint8_t shown, uint8_t flags)
{
    out[0] = 1;
    out[1] = total;
    out[2] = shown;
    out[3] = flags;
    return 4;
}

static size_t row(uint8_t *out, size_t at, uint8_t id, uint8_t state, uint16_t minutes, const char *name)
{
    size_t len = strlen(name);
    out[at] = id;
    out[at + 1] = state;
    out[at + 2] = (uint8_t)(minutes & 0xff);
    out[at + 3] = (uint8_t)(minutes >> 8);
    out[at + 4] = (uint8_t)len;
    memcpy(out + at + 5, name, len);
    return at + 5 + len;
}

static rows_kind_t parse(const uint8_t *data, size_t len, rows_frame_t *out)
{
    return rows_parse(data, len, out);
}

static void expect_invalid(const uint8_t *data, size_t len, const char *what)
{
    rows_frame_t out;
    check(parse(data, len, &out) == ROWS_INVALID, what);
}

static void test_table(void)
{
    uint8_t data[ROWS_FRAME_MAX];
    size_t at = frame(data, 3, 3, 0);
    at = row(data, at, 7, ROW_WAITING | ROWS_FRESH_BIT, ROWS_HIDDEN_MINUTES, "web");
    at = row(data, at, 2, ROW_RUNNING, ROWS_HIDDEN_MINUTES, "\xe9\xa1\xb9\xe7\x9b\xae");
    at = row(data, at, 9, ROW_DONE, 61, "api");
    rows_frame_t out;
    check(parse(data, at, &out) == ROWS_TABLE, "three rows parse as a table");
    check(out.total == 3 && out.shown == 3, "three rows give total and shown 3");
    check(out.rows[0].id == 7 && out.rows[0].state == ROW_WAITING && out.rows[0].fresh, "first row is a fresh wait");
    check(strcmp(out.rows[1].name, "\xe9\xa1\xb9\xe7\x9b\xae") == 0 && !out.rows[1].fresh, "utf-8 name kept");
    check(out.rows[2].state == ROW_DONE && out.rows[2].minutes == 61, "done row keeps its minutes");
    check(strcmp(rows_state_name(ROW_WAITING), "waiting") == 0 && strcmp(rows_state_name(ROW_DONE), "done") == 0,
          "state names");

    at = frame(data, 0, 0, 0);
    check(parse(data, at, &out) == ROWS_TABLE && out.total == 0 && out.shown == 0, "empty table");

    at = frame(data, 9, 6, 0);
    for (int i = 0; i < 6; i++) {
        at = row(data, at, (uint8_t)(i + 1), ROW_RUNNING, ROWS_HIDDEN_MINUTES, "12345678901234567890123456789012");
    }
    check(at == ROWS_FRAME_MAX, "six rows with 32 byte names fill the largest frame");
    check(parse(data, at, &out) == ROWS_TABLE && out.total == 9 && out.shown == 6, "largest frame parses");
    check(strlen(out.rows[5].name) == ROWS_NAME_MAX, "32 byte name kept whole");

    at = frame(data, 1, 1, 0);
    at = row(data, at, 1, ROW_DONE, ROWS_MINUTES_CAP, "");
    check(parse(data, at, &out) == ROWS_TABLE && out.rows[0].name[0] == '\0', "empty name and minutes cap");
}

static void test_heartbeat(void)
{
    uint8_t data[8];
    size_t at = frame(data, 4, 0, ROWS_HEARTBEAT_FLAG);
    rows_frame_t out;
    check(parse(data, at, &out) == ROWS_HEARTBEAT, "four byte heartbeat");
    data[4] = 0;
    expect_invalid(data, 5, "heartbeat with extra bytes");
    frame(data, 1, 1, ROWS_HEARTBEAT_FLAG);
    expect_invalid(data, 4, "heartbeat with rows");
}

static void test_invalid(void)
{
    uint8_t data[ROWS_FRAME_MAX + 8];
    size_t at;
    expect_invalid(data, 0, "nothing");
    frame(data, 0, 0, 0);
    expect_invalid(data, 3, "short header");
    data[0] = 2;
    expect_invalid(data, 4, "unknown format");
    frame(data, 0, 0, 0x02);
    expect_invalid(data, 4, "unknown flag");
    frame(data, 7, 7, 0);
    expect_invalid(data, 4, "seven rows");
    at = frame(data, 3, 1, 0);
    at = row(data, at, 1, ROW_RUNNING, ROWS_HIDDEN_MINUTES, "a");
    expect_invalid(data, at, "fewer rows than total while total fits");
    at = frame(data, 1, 2, 0);
    expect_invalid(data, at, "more rows than total");
    at = frame(data, 1, 1, 0);
    expect_invalid(data, at, "row missing");
    at = frame(data, 1, 1, 0);
    at = row(data, at, 0, ROW_RUNNING, ROWS_HIDDEN_MINUTES, "a");
    expect_invalid(data, at, "row id zero");
    at = frame(data, 1, 1, 0);
    at = row(data, at, 1, 5, ROWS_HIDDEN_MINUTES, "a");
    expect_invalid(data, at, "unknown state");
    at = frame(data, 1, 1, 0);
    at = row(data, at, 1, 0, ROWS_HIDDEN_MINUTES, "a");
    expect_invalid(data, at, "state zero");
    at = frame(data, 1, 1, 0);
    at = row(data, at, 1, ROW_RUNNING | 0x10, ROWS_HIDDEN_MINUTES, "a");
    expect_invalid(data, at, "unknown state bit");
    at = frame(data, 1, 1, 0);
    at = row(data, at, 1, ROW_RUNNING | ROWS_FRESH_BIT, ROWS_HIDDEN_MINUTES, "a");
    expect_invalid(data, at, "fresh running row");
    at = frame(data, 1, 1, 0);
    at = row(data, at, 1, ROW_RUNNING, 3, "a");
    expect_invalid(data, at, "minutes on a running row");
    at = frame(data, 1, 1, 0);
    at = row(data, at, 1, ROW_DONE, ROWS_MINUTES_CAP + 1, "a");
    expect_invalid(data, at, "minutes over the cap");
    at = frame(data, 1, 1, 0);
    at = row(data, at, 1, ROW_RUNNING, ROWS_HIDDEN_MINUTES, "123456789012345678901234567890123");
    expect_invalid(data, at, "33 byte name");
    at = frame(data, 1, 1, 0);
    at = row(data, at, 1, ROW_RUNNING, ROWS_HIDDEN_MINUTES, "ab\xe9\xa1");
    expect_invalid(data, at, "name cut inside a character");
    at = frame(data, 1, 1, 0);
    at = row(data, at, 1, ROW_RUNNING, ROWS_HIDDEN_MINUTES, "\xc0\xaf");
    expect_invalid(data, at, "overlong utf-8");
    at = frame(data, 1, 1, 0);
    at = row(data, at, 1, ROW_RUNNING, ROWS_HIDDEN_MINUTES, "a\nb");
    expect_invalid(data, at, "control character in name");
    at = frame(data, 2, 2, 0);
    at = row(data, at, 4, ROW_RUNNING, ROWS_HIDDEN_MINUTES, "a");
    at = row(data, at, 4, ROW_RUNNING, ROWS_HIDDEN_MINUTES, "b");
    expect_invalid(data, at, "two rows with one id");
    at = frame(data, 1, 1, 0);
    at = row(data, at, 1, ROW_RUNNING, ROWS_HIDDEN_MINUTES, "a");
    data[at] = 0;
    expect_invalid(data, at + 1, "trailing byte");
    at = frame(data, 1, 1, 0);
    at = row(data, at, 1, ROW_RUNNING, ROWS_HIDDEN_MINUTES, "abc");
    expect_invalid(data, at - 1, "name shorter than its length");
}

static void test_table_life(void)
{
    uint8_t data[64];
    size_t at = frame(data, 2, 2, 0);
    at = row(data, at, 1, ROW_RUNNING, ROWS_HIDDEN_MINUTES, "a");
    at = row(data, at, 2, ROW_DONE, 59, "b");
    rows_frame_t parsed;
    check(parse(data, at, &parsed) == ROWS_TABLE, "life table parses");

    rows_table_t table;
    rows_init(&table);
    check(!table.linked && !rows_silent(&table, 1000 * SECOND_US), "a card that never linked is never silent");
    int64_t t0 = 10 * SECOND_US;
    rows_link(&table, t0);
    check(table.linked && table.frame.shown == 0, "linking starts with an empty table");
    check(!rows_silent(&table, t0 + ROWS_SILENCE_US - 1), "silent only after 90 seconds");
    check(rows_silent(&table, t0 + ROWS_SILENCE_US), "silent at 90 seconds");
    rows_heard(&table, ROWS_TABLE, &parsed, t0 + SECOND_US);
    check(table.frame.shown == 2 && !rows_silent(&table, t0 + ROWS_SILENCE_US), "a table restarts the silence clock");
    check(rows_minutes(&table, 0, t0 + 50 * MINUTE_US) == ROWS_HIDDEN_MINUTES, "running row has no minutes");
    check(rows_minutes(&table, 1, t0 + SECOND_US + 59 * SECOND_US) == 59, "59 minutes before a minute passes");
    check(rows_minutes(&table, 1, t0 + SECOND_US + 60 * SECOND_US) == 60, "60 minutes after one minute");
    check(rows_minutes(&table, 1, t0 + SECOND_US + 40 * 24 * 60 * MINUTE_US) == ROWS_MINUTES_CAP, "minutes stop at 31 days");
    rows_heard(&table, ROWS_HEARTBEAT, NULL, t0 + 80 * SECOND_US);
    check(table.frame.shown == 2, "a heartbeat keeps the table");
    check(rows_minutes(&table, 1, t0 + 61 * SECOND_US) == 60, "a heartbeat keeps the minute count");
    check(!rows_silent(&table, t0 + 80 * SECOND_US + ROWS_SILENCE_US - 1), "a heartbeat restarts the silence clock");
    rows_lose(&table);
    check(!table.linked && table.frame.shown == 0 && table.frame.total == 0, "losing the link clears the table");
}

int main(void)
{
    test_table();
    test_heartbeat();
    test_invalid();
    test_table_life();
    if (failures == 0) {
        printf("ALL PASSED\n");
    }
    return failures == 0 ? 0 : 1;
}
