#include <stdio.h>
#include <string.h>

#include "events.h"

#define MS 1000LL

static int failures;

static void check(int ok, const char *what)
{
    if (!ok) {
        printf("FAIL %s\n", what);
        failures++;
    }
}

static row_t row(uint8_t id, row_state_t state, bool fresh, const char *name)
{
    row_t r = {.id = id, .state = state, .fresh = fresh, .minutes = ROWS_HIDDEN_MINUTES, .count = 1};
    snprintf(r.name, sizeof r.name, "%s", name);
    return r;
}

static rows_frame_t frame(int count, const row_t *rows)
{
    rows_frame_t f = {.total = (uint8_t)count, .shown = (uint8_t)count};
    memcpy(f.rows, rows, sizeof(row_t) * (size_t)count);
    return f;
}

static void expect_view(const events_t *events, int64_t now, event_kind_t kind, const char *name, uint32_t ms,
                        const char *what)
{
    event_view_t view;
    int64_t daily;
    events_view(events, now, &view, &daily);
    char label[160];
    snprintf(label, sizeof label, "%s: kind %s (got %s)", what, events_name(kind), events_name(view.kind));
    check(view.kind == kind, label);
    if (kind != EVENT_NONE) {
        snprintf(label, sizeof label, "%s: name %s (got %s)", what, name, view.name);
        check(strcmp(view.name, name) == 0, label);
        snprintf(label, sizeof label, "%s: %u ms in (got %u)", what, (unsigned)ms, (unsigned)view.ms);
        check(view.ms == ms, label);
        check(daily == now, "the daily loop holds still during an event");
    }
}

static int64_t daily_start(const events_t *events, int64_t now)
{
    event_view_t view;
    int64_t daily;
    events_view(events, now, &view, &daily);
    return daily;
}

static void test_fresh_alerts(void)
{
    events_t e;
    events_init(&e, 0);
    row_t calm[] = {row(1, ROW_RUNNING, false, "alpha"), row(2, ROW_WAITING, false, "beta")};
    rows_frame_t f = frame(2, calm);
    check(events_heard(&e, &f, true, 100 * MS) == EVENT_NONE, "a waiting row without the fresh bit does not alert");
    row_t fresh_wait[] = {row(1, ROW_WAITING, true, "alpha"), row(2, ROW_WAITING, true, "beta")};
    f = frame(2, fresh_wait);
    check(events_heard(&e, &f, true, 200 * MS) == EVENT_WAIT, "fresh waiting rows alert once");
    expect_view(&e, 250 * MS, EVENT_WAIT, "alpha", 50, "two fresh waiting rows give one alert with the first name");
    row_t mixed[] = {row(1, ROW_WAITING, true, "alpha"), row(2, ROW_ERROR, true, "beta"),
                     row(3, ROW_ERROR, true, "gamma")};
    f = frame(3, mixed);
    check(events_heard(&e, &f, true, 1000 * MS) == EVENT_ERROR, "an error among fresh rows uses the error alert");
    expect_view(&e, 1000 * MS, EVENT_ERROR, "beta", 0, "a new alert restarts the shown one with the first error");
    row_t again[] = {row(1, ROW_WAITING, true, "alpha")};
    f = frame(1, again);
    check(events_heard(&e, &f, true, 3000 * MS) == EVENT_WAIT, "a waiting alert may replace an error alert");
    expect_view(&e, 3000 * MS + 5999 * MS, EVENT_WAIT, "alpha", 5999, "an alert lasts six seconds");
    expect_view(&e, 3000 * MS + 6000 * MS, EVENT_NONE, "", 0, "an alert closes after six seconds");
    check(daily_start(&e, 9500 * MS) == 9000 * MS, "the daily loop starts again when the alert closes");
}

static void test_cheer_and_hello(void)
{
    events_t e;
    events_init(&e, 0);
    events_link(&e, 10 * MS);
    row_t running[] = {row(4, ROW_RUNNING, false, "alpha"), row(5, ROW_RUNNING, false, "beta")};
    rows_frame_t f = frame(2, running);
    check(events_heard(&e, &f, true, 100 * MS) == EVENT_HELLO, "the first table after linking waves hello");
    expect_view(&e, 2999 * MS + 100 * MS, EVENT_HELLO, "", 2999, "hello lasts three seconds");
    check(events_heard(&e, &f, true, 200 * MS) == EVENT_NONE, "hello waves only once per link");
    row_t done[] = {row(5, ROW_DONE, false, "beta"), row(4, ROW_RUNNING, false, "alpha")};
    f = frame(2, done);
    check(events_heard(&e, &f, true, 500 * MS) == EVENT_CHEER, "a row going from running to done cheers");
    expect_view(&e, 500 * MS, EVENT_CHEER, "beta", 0, "cheer replaces hello and names the finished row");
    expect_view(&e, 3500 * MS, EVENT_NONE, "", 0, "cheer closes after three seconds");
    check(events_heard(&e, &f, true, 4000 * MS) == EVENT_NONE, "a row already done does not cheer again");
    row_t done_new[] = {row(6, ROW_DONE, false, "delta")};
    f = frame(1, done_new);
    check(events_heard(&e, &f, true, 5000 * MS) == EVENT_NONE, "a row first seen as done does not cheer");
    events_link(&e, 6000 * MS);
    row_t waiting[] = {row(7, ROW_WAITING, true, "gamma")};
    f = frame(1, waiting);
    check(events_heard(&e, &f, true, 6100 * MS) == EVENT_WAIT, "an alert in the first table wins over hello");
    expect_view(&e, 6100 * MS, EVENT_WAIT, "gamma", 0, "the alert shows instead of hello");
}

static void test_priority(void)
{
    events_t e;
    events_init(&e, 0);
    check(events_start(&e, EVENT_WAIT, "alpha", 0), "an alert starts");
    check(!events_start(&e, EVENT_CHEER, "beta", 1000 * MS), "cheer is dropped while an alert shows");
    check(!events_start(&e, EVENT_HELLO, "", 1000 * MS), "hello is dropped while an alert shows");
    row_t running[] = {row(1, ROW_RUNNING, false, "beta")};
    rows_frame_t f = frame(1, running);
    events_heard(&e, &f, true, 1100 * MS);
    row_t done[] = {row(1, ROW_DONE, false, "beta")};
    f = frame(1, done);
    check(events_heard(&e, &f, true, 1200 * MS) == EVENT_NONE, "a cheer from the table is dropped during an alert");
    expect_view(&e, 1200 * MS, EVENT_WAIT, "alpha", 1200, "the alert keeps showing");
    check(events_start(&e, EVENT_CHEER, "beta", 6000 * MS), "cheer starts once the alert closed");
    check(events_start(&e, EVENT_CHEER, "gamma", 7000 * MS), "an equal event replaces the shown one");
    expect_view(&e, 7000 * MS, EVENT_CHEER, "gamma", 0, "the newer cheer shows");
    check(!events_start(&e, EVENT_HELLO, "", 7100 * MS), "hello is dropped while cheer shows");
    check(events_start(&e, EVENT_ERROR, "delta", 7200 * MS), "an alert replaces cheer");
    check(!events_start(&e, EVENT_NONE, "x", 7300 * MS), "no event is not an event");
    events_lose(&e, 8000 * MS);
    expect_view(&e, 8000 * MS, EVENT_NONE, "", 0, "losing the link clears the event");
    check(daily_start(&e, 9000 * MS) == 8000 * MS, "the daily loop starts again when the link drops");
    event_kind_t kind = EVENT_NONE;
    check(events_kind("error", &kind) && kind == EVENT_ERROR, "error names the error alert");
    check(!events_kind("none", &kind) && !events_kind("", &kind), "none and empty are not event names");
}

static void test_dark_screen(void)
{
    events_t e;
    events_init(&e, 0);
    events_link(&e, 0);
    row_t running[] = {row(1, ROW_RUNNING, false, "alpha")};
    rows_frame_t f = frame(1, running);
    check(events_heard(&e, &f, false, 100 * MS) == EVENT_NONE, "a dark screen does not wave hello");
    expect_view(&e, 100 * MS, EVENT_NONE, "", 0, "no hello is queued for later");
    row_t done[] = {row(1, ROW_DONE, false, "alpha")};
    f = frame(1, done);
    check(events_heard(&e, &f, false, 200 * MS) == EVENT_NONE, "a dark screen does not cheer");
    expect_view(&e, 200 * MS, EVENT_NONE, "", 0, "no cheer is queued for later");
    row_t waiting[] = {row(1, ROW_WAITING, true, "alpha")};
    f = frame(1, waiting);
    check(events_heard(&e, &f, false, 300 * MS) == EVENT_WAIT, "a dark screen still alerts on a fresh waiting row");
    events_init(&e, 400 * MS);
    row_t failed[] = {row(2, ROW_ERROR, true, "beta")};
    f = frame(1, failed);
    check(events_heard(&e, &f, false, 500 * MS) == EVENT_ERROR, "a dark screen still alerts on a fresh error row");
}

int main(int argc, char **argv)
{
    if (argc == 2 && strcmp(argv[1], "dark") == 0) {
        test_dark_screen();
    } else {
        test_fresh_alerts();
        test_cheer_and_hello();
        test_priority();
    }
    if (failures == 0) {
        printf("ALL PASSED\n");
    }
    return failures == 0 ? 0 : 1;
}
