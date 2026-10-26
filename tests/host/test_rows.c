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

static size_t urgent_frame(uint8_t *out, uint8_t total, uint8_t shown, uint8_t flags, uint8_t urgent)
{
    out[0] = ROWS_FORMAT;
    out[1] = total;
    out[2] = shown;
    out[3] = flags;
    out[4] = urgent;
    return ROWS_HEADER_SIZE;
}

static size_t frame(uint8_t *out, uint8_t total, uint8_t shown, uint8_t flags)
{
    return urgent_frame(out, total, shown, flags, 0);
}

static size_t counted_row(uint8_t *out, size_t at, uint8_t id, uint8_t state, uint16_t minutes, uint8_t count,
                          const char *name)
{
    size_t len = strlen(name);
    out[at] = id;
    out[at + 1] = state;
    out[at + 2] = (uint8_t)(minutes & 0xff);
    out[at + 3] = (uint8_t)(minutes >> 8);
    out[at + 4] = count;
    out[at + 5] = (uint8_t)len;
    memcpy(out + at + ROWS_ROW_HEADER_SIZE, name, len);
    return at + ROWS_ROW_HEADER_SIZE + len;
}

static size_t row(uint8_t *out, size_t at, uint8_t id, uint8_t state, uint16_t minutes, const char *name)
{
    return counted_row(out, at, id, state, minutes, 1, name);
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
    size_t at = urgent_frame(data, 3, 3, 0, 1);
    at = row(data, at, 7, ROW_WAITING | ROWS_FRESH_BIT, ROWS_HIDDEN_MINUTES, "web");
    at = row(data, at, 2, ROW_RUNNING, ROWS_HIDDEN_MINUTES, "\xe9\xa1\xb9\xe7\x9b\xae");
    at = row(data, at, 9, ROW_DONE, 61, "api");
    rows_frame_t out;
    check(parse(data, at, &out) == ROWS_TABLE, "three rows parse as a table");
    check(out.total == 3 && out.shown == 3 && out.urgent == 1, "three rows give total and shown 3, one urgent");
    check(out.rows[0].count == 1 && out.rows[2].count == 1, "single rows count one session");
    check(out.rows[0].id == 7 && out.rows[0].state == ROW_WAITING && out.rows[0].fresh && !out.rows[0].project,
          "first row is a fresh wait");
    check(strcmp(out.rows[1].name, "\xe9\xa1\xb9\xe7\x9b\xae") == 0 && !out.rows[1].fresh, "utf-8 name kept");
    check(out.rows[2].state == ROW_DONE && out.rows[2].minutes == 61, "done row keeps its minutes");
    check(strcmp(rows_state_name(ROW_WAITING), "waiting") == 0 && strcmp(rows_state_name(ROW_DONE), "done") == 0,
          "state names");
    check(strcmp(rows_top_name(&out), "waiting") == 0, "top name follows the first row");
    out.rows[0].state = ROW_RUNNING;
    check(strcmp(rows_top_name(&out), "working") == 0, "a running first row is working");

    at = frame(data, 0, 0, 0);
    check(parse(data, at, &out) == ROWS_TABLE && out.total == 0 && out.shown == 0, "empty table");
    check(strcmp(rows_top_name(&out), "idle") == 0, "an empty table is idle");

    at = urgent_frame(data, 3, 3, 0, 1);
    at = row(data, at, 1, ROW_RUNNING | ROWS_PROJECT_BIT, ROWS_HIDDEN_MINUTES, "a");
    at = row(data, at, 2, ROW_WAITING | ROWS_PROJECT_BIT | ROWS_FRESH_BIT, ROWS_HIDDEN_MINUTES, "b");
    at = row(data, at, 3, ROW_DONE, 4, "c");
    check(parse(data, at, &out) == ROWS_TABLE, "project rows parse");
    check(out.rows[0].project && out.rows[0].state == ROW_RUNNING && !out.rows[0].fresh, "running project row");
    check(out.rows[1].project && out.rows[1].state == ROW_WAITING && out.rows[1].fresh, "fresh waiting project row");
    check(!out.rows[2].project && out.rows[2].minutes == 4, "plain done row");

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
    check(parse(data, at, &out) == ROWS_HEARTBEAT, "five byte heartbeat");
    data[ROWS_HEADER_SIZE] = 0;
    expect_invalid(data, ROWS_HEADER_SIZE + 1, "heartbeat with extra bytes");
    frame(data, 1, 1, ROWS_HEARTBEAT_FLAG);
    expect_invalid(data, ROWS_HEADER_SIZE, "heartbeat with rows");
    urgent_frame(data, 4, 0, ROWS_HEARTBEAT_FLAG, 2);
    check(parse(data, ROWS_HEADER_SIZE, &out) == ROWS_HEARTBEAT, "heartbeat with urgent sessions");
    urgent_frame(data, 4, 0, ROWS_HEARTBEAT_FLAG, 5);
    expect_invalid(data, ROWS_HEADER_SIZE, "heartbeat with more urgent sessions than sessions");
}

static void test_invalid(void)
{
    uint8_t data[ROWS_FRAME_MAX + 8];
    size_t at;
    expect_invalid(data, 0, "nothing");
    frame(data, 0, 0, 0);
    expect_invalid(data, ROWS_HEADER_SIZE - 1, "short header");
    data[0] = 1;
    expect_invalid(data, ROWS_HEADER_SIZE, "the old format 1");
    data[0] = 3;
    expect_invalid(data, ROWS_HEADER_SIZE, "unknown format");
    frame(data, 0, 0, 0x02);
    expect_invalid(data, ROWS_HEADER_SIZE, "unknown flag");
    frame(data, 7, 7, 0);
    expect_invalid(data, ROWS_HEADER_SIZE, "seven rows");
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
    at = row(data, at, 1, ROW_RUNNING | 0x20, ROWS_HIDDEN_MINUTES, "a");
    expect_invalid(data, at, "state bit 0x20");
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

static void test_counts(void)
{
    uint8_t data[ROWS_FRAME_MAX + 8];
    rows_frame_t out;
    size_t at = urgent_frame(data, 10, 6, 0, 8);
    at = counted_row(data, at, 3, ROW_WAITING, ROWS_HIDDEN_MINUTES, 3, "FunMedal");
    at = counted_row(data, at, 5, ROW_WAITING | ROWS_FRESH_BIT, ROWS_HIDDEN_MINUTES, 2, "SiBlog");
    at = counted_row(data, at, 6, ROW_WAITING, ROWS_HIDDEN_MINUTES, 2, "agent-skills");
    at = row(data, at, 8, ROW_ERROR, ROWS_HIDDEN_MINUTES, "hermes");
    at = row(data, at, 9, ROW_RUNNING, ROWS_HIDDEN_MINUTES, "shop");
    at = row(data, at, 10, ROW_DONE, 12, "notes");
    check(parse(data, at, &out) == ROWS_TABLE, "ten sessions merged into six rows parse");
    check(out.total == 10 && out.shown == 6 && out.urgent == 8, "merged table keeps sessions and urgent");
    check(out.rows[0].count == 3 && out.rows[1].count == 2 && out.rows[1].fresh && out.rows[3].count == 1,
          "each row keeps its session count");

    at = urgent_frame(data, 9, 6, 0, 9);
    for (int i = 0; i < 6; i++) {
        at = row(data, at, (uint8_t)(i + 1), ROW_WAITING, ROWS_HIDDEN_MINUTES, "w");
    }
    check(parse(data, at, &out) == ROWS_TABLE && out.urgent == 9, "six waiting rows of nine waiting parse");

    at = urgent_frame(data, 4, 2, 0, 4);
    at = counted_row(data, at, 1, ROW_WAITING, ROWS_HIDDEN_MINUTES, 3, "a");
    at = row(data, at, 2, ROW_ERROR, ROWS_HIDDEN_MINUTES, "b");
    check(parse(data, at, &out) == ROWS_TABLE, "two rows holding all four sessions parse");

    at = urgent_frame(data, 4, 2, 0, 2);
    at = row(data, at, 1, ROW_WAITING, ROWS_HIDDEN_MINUTES, "a");
    at = row(data, at, 2, ROW_ERROR, ROWS_HIDDEN_MINUTES, "b");
    expect_invalid(data, at, "fewer sessions than total while every row fits");
    at = urgent_frame(data, 3, 1, 0, 3);
    at = counted_row(data, at, 1, ROW_WAITING, ROWS_HIDDEN_MINUTES, 4, "a");
    expect_invalid(data, at, "more sessions in rows than total");
    at = urgent_frame(data, 1, 1, 0, 1);
    at = counted_row(data, at, 1, ROW_WAITING, ROWS_HIDDEN_MINUTES, 0, "a");
    expect_invalid(data, at, "a row of no session");
    at = frame(data, 2, 1, 0);
    at = counted_row(data, at, 1, ROW_RUNNING, ROWS_HIDDEN_MINUTES, 2, "a");
    expect_invalid(data, at, "merged running row");
    at = frame(data, 2, 1, 0);
    at = counted_row(data, at, 1, ROW_DONE, 4, 2, "a");
    expect_invalid(data, at, "merged done row");
    at = urgent_frame(data, 1, 1, 0, 2);
    at = row(data, at, 1, ROW_WAITING, ROWS_HIDDEN_MINUTES, "a");
    expect_invalid(data, at, "more urgent sessions than sessions");
    at = urgent_frame(data, 6, 6, 0, 5);
    for (int i = 0; i < 6; i++) {
        at = row(data, at, (uint8_t)(i + 1), ROW_RUNNING, ROWS_HIDDEN_MINUTES, "r");
    }
    expect_invalid(data, at, "more running and done sessions in rows than sessions that are not urgent");
    at = urgent_frame(data, 2, 2, 0, 1);
    at = row(data, at, 1, ROW_WAITING, ROWS_HIDDEN_MINUTES, "a");
    at = row(data, at, 2, ROW_ERROR, ROWS_HIDDEN_MINUTES, "b");
    expect_invalid(data, at, "more urgent rows than urgent sessions");
    at = urgent_frame(data, 2, 2, 0, 2);
    at = row(data, at, 1, ROW_WAITING, ROWS_HIDDEN_MINUTES, "a");
    at = row(data, at, 2, ROW_RUNNING, ROWS_HIDDEN_MINUTES, "b");
    expect_invalid(data, at, "an urgent session missing while every row fits");
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
    test_counts();
    test_table_life();
    if (failures == 0) {
        printf("ALL PASSED\n");
    }
    return failures == 0 ? 0 : 1;
}
