#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "keys.h"

static int failures;

#define CHECK(cond)                                                          \
    do {                                                                     \
        if (!(cond)) {                                                       \
            printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #cond);            \
            failures++;                                                      \
        }                                                                    \
    } while (0)

static const key_level_t LEVELS[] = {
    {KEY_NONE, 3000},
    {KEY_OK, 300},
    {KEY_UP, 1000},
    {KEY_DOWN, 1700},
};
static const int LEVEL_COUNT = sizeof(LEVELS) / sizeof(LEVELS[0]);

typedef struct {
    key_event_t events[64];
    int count;
} event_log_t;

static void feed(keys_debouncer_t *d, event_log_t *log, key_id_t raw, uint32_t now_ms)
{
    key_event_t out[4];
    int n = keys_debounce_update(d, raw, now_ms, out, 4);
    for (int i = 0; i < n && log->count < 64; i++) {
        log->events[log->count++] = out[i];
    }
}

static int feed_run(keys_debouncer_t *d, event_log_t *log, key_id_t raw, uint32_t *now_ms, int samples)
{
    int before = log->count;
    for (int i = 0; i < samples; i++) {
        *now_ms += KEYS_SAMPLE_MS;
        feed(d, log, raw, *now_ms);
    }
    return log->count - before;
}

static void test_classify_centers(void)
{
    CHECK(keys_classify(3000, LEVELS, LEVEL_COUNT, 150) == KEY_NONE);
    CHECK(keys_classify(300, LEVELS, LEVEL_COUNT, 150) == KEY_OK);
    CHECK(keys_classify(1000, LEVELS, LEVEL_COUNT, 150) == KEY_UP);
    CHECK(keys_classify(1700, LEVELS, LEVEL_COUNT, 150) == KEY_DOWN);
}

static void test_classify_tolerance_edges(void)
{
    CHECK(keys_classify(1150, LEVELS, LEVEL_COUNT, 150) == KEY_UP);
    CHECK(keys_classify(850, LEVELS, LEVEL_COUNT, 150) == KEY_UP);
    CHECK(keys_classify(1151, LEVELS, LEVEL_COUNT, 150) == KEY_UNKNOWN);
    CHECK(keys_classify(849, LEVELS, LEVEL_COUNT, 150) == KEY_UNKNOWN);
}

static void test_classify_out_of_range(void)
{
    CHECK(keys_classify(-20, LEVELS, LEVEL_COUNT, 150) == KEY_UNKNOWN);
    CHECK(keys_classify(0, LEVELS, LEVEL_COUNT, 150) == KEY_UNKNOWN);
    CHECK(keys_classify(4000, LEVELS, LEVEL_COUNT, 150) == KEY_UNKNOWN);
}

static void test_classify_overlap_picks_nearest(void)
{
    CHECK(keys_classify(1340, LEVELS, LEVEL_COUNT, 400) == KEY_UP);
    CHECK(keys_classify(1360, LEVELS, LEVEL_COUNT, 400) == KEY_DOWN);
}

static void test_single_glitch_ignored(void)
{
    keys_debouncer_t d;
    event_log_t log = {0};
    uint32_t now = 0;
    keys_debouncer_init(&d);
    feed_run(&d, &log, KEY_NONE, &now, 5);
    feed_run(&d, &log, KEY_OK, &now, KEYS_STABLE_SAMPLES - 1);
    feed_run(&d, &log, KEY_NONE, &now, 5);
    CHECK(log.count == 0);
}

static void test_press_after_stable_samples(void)
{
    keys_debouncer_t d;
    event_log_t log = {0};
    uint32_t now = 0;
    keys_debouncer_init(&d);
    feed_run(&d, &log, KEY_NONE, &now, 5);
    CHECK(feed_run(&d, &log, KEY_UP, &now, KEYS_STABLE_SAMPLES - 1) == 0);
    CHECK(feed_run(&d, &log, KEY_UP, &now, 1) == 1);
    CHECK(log.events[0].key == KEY_UP && log.events[0].type == KEY_EVENT_PRESS);
}

static void test_release_after_stable_samples(void)
{
    keys_debouncer_t d;
    event_log_t log = {0};
    uint32_t now = 0;
    keys_debouncer_init(&d);
    feed_run(&d, &log, KEY_DOWN, &now, KEYS_STABLE_SAMPLES);
    CHECK(feed_run(&d, &log, KEY_NONE, &now, KEYS_STABLE_SAMPLES - 1) == 0);
    CHECK(feed_run(&d, &log, KEY_NONE, &now, 1) == 1);
    CHECK(log.count == 2);
    CHECK(log.events[1].key == KEY_DOWN && log.events[1].type == KEY_EVENT_RELEASE);
}

static void test_long_press_once(void)
{
    keys_debouncer_t d;
    event_log_t log = {0};
    uint32_t now = 0;
    keys_debouncer_init(&d);
    feed_run(&d, &log, KEY_OK, &now, KEYS_STABLE_SAMPLES);
    uint32_t pressed = now;
    while (now + KEYS_SAMPLE_MS < pressed + KEYS_LONG_PRESS_MS) {
        now += KEYS_SAMPLE_MS;
        feed(&d, &log, KEY_OK, now);
    }
    CHECK(log.count == 1);
    feed_run(&d, &log, KEY_OK, &now, 1);
    CHECK(log.count == 2);
    CHECK(log.events[1].key == KEY_OK && log.events[1].type == KEY_EVENT_LONG);
    feed_run(&d, &log, KEY_OK, &now, 200);
    CHECK(log.count == 2);
    feed_run(&d, &log, KEY_NONE, &now, KEYS_STABLE_SAMPLES);
    CHECK(log.count == 3 && log.events[2].type == KEY_EVENT_RELEASE);
}

static void test_unknown_samples_do_not_release(void)
{
    keys_debouncer_t d;
    event_log_t log = {0};
    uint32_t now = 0;
    keys_debouncer_init(&d);
    feed_run(&d, &log, KEY_UP, &now, KEYS_STABLE_SAMPLES);
    feed_run(&d, &log, KEY_UNKNOWN, &now, 10);
    feed_run(&d, &log, KEY_UP, &now, 3);
    CHECK(log.count == 1);
}

static void test_direct_switch(void)
{
    keys_debouncer_t d;
    event_log_t log = {0};
    uint32_t now = 0;
    keys_debouncer_init(&d);
    feed_run(&d, &log, KEY_UP, &now, KEYS_STABLE_SAMPLES);
    feed_run(&d, &log, KEY_DOWN, &now, KEYS_STABLE_SAMPLES);
    CHECK(log.count == 3);
    CHECK(log.events[1].key == KEY_UP && log.events[1].type == KEY_EVENT_RELEASE);
    CHECK(log.events[2].key == KEY_DOWN && log.events[2].type == KEY_EVENT_PRESS);
}

static void test_chatter_produces_nothing(void)
{
    keys_debouncer_t d;
    event_log_t log = {0};
    uint32_t now = 0;
    keys_debouncer_init(&d);
    for (int i = 0; i < 100; i++) {
        feed_run(&d, &log, (i % 2) ? KEY_OK : KEY_NONE, &now, 1);
    }
    CHECK(log.count == 0);
}

static void test_long_press_across_clock_wrap(void)
{
    keys_debouncer_t d;
    event_log_t log = {0};
    uint32_t now = UINT32_MAX - 200;
    keys_debouncer_init(&d);
    feed_run(&d, &log, KEY_OK, &now, KEYS_STABLE_SAMPLES);
    CHECK(log.count == 1);
    feed_run(&d, &log, KEY_OK, &now, KEYS_LONG_PRESS_MS / KEYS_SAMPLE_MS + 1);
    CHECK(log.count == 2 && log.events[1].type == KEY_EVENT_LONG);
}

static void test_board_levels_cover_each_key_once(void)
{
    for (key_id_t key = KEY_NONE; key < KEY_COUNT; key++) {
        int seen = 0;
        for (int i = 0; i < keys_board_level_count; i++) {
            seen += keys_board_levels[i].key == key;
        }
        CHECK(seen == 1);
    }
}

static void test_board_levels_do_not_overlap(void)
{
    CHECK(keys_board_tolerance_mv > 0);
    for (int i = 0; i < keys_board_level_count; i++) {
        for (int j = i + 1; j < keys_board_level_count; j++) {
            int gap = abs(keys_board_levels[i].center_mv - keys_board_levels[j].center_mv);
            CHECK(gap > 2 * keys_board_tolerance_mv);
        }
    }
}

static void test_names(void)
{
    CHECK(strcmp(keys_name(KEY_OK), "ok") == 0);
    CHECK(strcmp(keys_name(KEY_UP), "up") == 0);
    CHECK(strcmp(keys_name(KEY_DOWN), "down") == 0);
    CHECK(strcmp(keys_name(KEY_NONE), "none") == 0);
}

int main(void)
{
    test_classify_centers();
    test_classify_tolerance_edges();
    test_classify_out_of_range();
    test_classify_overlap_picks_nearest();
    test_single_glitch_ignored();
    test_press_after_stable_samples();
    test_release_after_stable_samples();
    test_long_press_once();
    test_unknown_samples_do_not_release();
    test_direct_switch();
    test_chatter_produces_nothing();
    test_long_press_across_clock_wrap();
    test_board_levels_cover_each_key_once();
    test_board_levels_do_not_overlap();
    test_names();
    printf("%s\n", failures == 0 ? "ALL PASSED" : "FAILED");
    return failures == 0 ? 0 : 1;
}
