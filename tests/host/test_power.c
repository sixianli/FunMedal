#include <stdio.h>
#include <string.h>

#include "power.h"

#define SECOND 1000000LL
#define MINUTE (60 * SECOND)

static int failures;

static void check(int ok, const char *what)
{
    if (!ok) {
        printf("FAIL %s\n", what);
        failures++;
    }
}

static void test_usb_keeps_the_screen_on(void)
{
    power_t p;
    power_init(&p, 0);
    check(power_check(&p, true, false, 0) == POWER_KEEP, "plugged into a computer the screen stays on");
    check(power_check(&p, true, false, 60 * MINUTE) == POWER_KEEP, "an hour on a computer still keeps it on");
    check(!p.dark, "the screen is lit on a computer");
}

static void test_battery_dims_after_five_quiet_minutes(void)
{
    power_t p;
    power_init(&p, 0);
    check(p.idle_us == 5 * MINUTE, "the screen dims after five quiet minutes by default");
    check(power_check(&p, false, false, 5 * MINUTE - 1) == POWER_KEEP, "just under five minutes stays lit");
    check(power_check(&p, false, false, 5 * MINUTE) == POWER_DIM, "five quiet minutes on battery dim the screen");
    check(p.dark, "the screen is dark");
    check(power_check(&p, false, false, 6 * MINUTE) == POWER_KEEP, "a dark screen dims only once");
}

static void test_changes_restart_the_count(void)
{
    power_t p;
    power_init(&p, 0);
    check(power_touch(&p, false, 4 * MINUTE) == POWER_KEEP, "a change on a lit screen keeps it lit");
    check(power_check(&p, false, false, 8 * MINUTE) == POWER_KEEP, "a change restarts the five minutes");
    check(power_check(&p, false, false, 9 * MINUTE) == POWER_DIM, "five minutes after the change it dims");
    power_init(&p, 0);
    check(power_check(&p, false, true, 4 * MINUTE) == POWER_KEEP, "an alert or a sound holds the screen on");
    check(power_check(&p, false, true, 20 * MINUTE) == POWER_KEEP, "it stays on while busy");
    check(power_check(&p, false, false, 25 * MINUTE - 1) == POWER_KEEP, "the count starts when busy ends");
    check(power_check(&p, false, false, 25 * MINUTE) == POWER_DIM, "five minutes after busy ends it dims");
}

static void test_waking(void)
{
    power_t p;
    power_init(&p, 0);
    power_check(&p, false, false, 5 * MINUTE);
    check(power_touch(&p, false, 6 * MINUTE) == POWER_KEEP, "a change that does not wake leaves it dark");
    check(p.dark, "a session finishing does not light a dark screen");
    check(power_check(&p, false, true, 7 * MINUTE) == POWER_KEEP, "being busy does not light a dark screen");
    check(power_touch(&p, true, 8 * MINUTE) == POWER_LIGHT, "an alert or a key press lights it");
    check(!p.dark, "the screen is lit again");
    check(power_touch(&p, true, 8 * MINUTE) == POWER_KEEP, "a lit screen does not light twice");
    check(power_check(&p, false, false, 13 * MINUTE - 1) == POWER_KEEP, "waking restarts the five minutes");
    check(power_check(&p, false, false, 13 * MINUTE) == POWER_DIM, "five minutes after waking it dims again");
    check(power_check(&p, true, false, 14 * MINUTE) == POWER_LIGHT, "plugging into a computer lights it");
}

static void test_unplugging(void)
{
    power_t p;
    power_init(&p, 0);
    power_check(&p, true, false, 30 * MINUTE);
    check(power_check(&p, false, false, 30 * MINUTE + 1) == POWER_KEEP, "unplugging does not dim at once");
    check(power_check(&p, false, false, 35 * MINUTE) == POWER_KEEP, "the count starts at unplugging");
    check(power_check(&p, false, false, 35 * MINUTE + 1) == POWER_DIM, "five minutes after unplugging it dims");
}

static void test_shorter_idle(void)
{
    power_t p;
    power_init(&p, 0);
    power_set_idle(&p, 30 * SECOND, 2 * MINUTE);
    check(power_check(&p, false, false, 2 * MINUTE + 29 * SECOND) == POWER_KEEP, "a shorter idle restarts the count");
    check(power_check(&p, false, false, 2 * MINUTE + 30 * SECOND) == POWER_DIM, "a 30 second idle dims after 30 s");
    check(power_set_idle(&p, 30 * SECOND, 3 * MINUTE) == POWER_LIGHT, "setting the idle lights a dark screen");
}

static row_t row(uint8_t id, row_state_t state, uint16_t minutes, const char *name)
{
    row_t r = {.id = id, .state = state, .minutes = minutes, .count = 1};
    snprintf(r.name, sizeof r.name, "%s", name);
    return r;
}

static void test_what_counts_as_a_change(void)
{
    rows_frame_t before = {.total = 2, .shown = 2,
                           .rows = {row(1, ROW_RUNNING, ROWS_HIDDEN_MINUTES, "alpha"), row(2, ROW_DONE, 5, "beta")}};
    rows_frame_t after = before;
    check(!power_rows_differ(&before, &after), "the same table is no change");
    after.rows[1].minutes = 9;
    check(!power_rows_differ(&before, &after), "more minutes alone are no change");
    after = before;
    after.rows[0].fresh = true;
    check(!power_rows_differ(&before, &after), "the fresh bit alone is no change");
    after = before;
    after.rows[0].state = ROW_DONE;
    check(power_rows_differ(&before, &after), "a new state is a change");
    after = before;
    snprintf(after.rows[1].name, sizeof after.rows[1].name, "gamma");
    check(power_rows_differ(&before, &after), "a new name is a change");
    after = before;
    after.rows[1].id = 3;
    check(power_rows_differ(&before, &after), "a new row is a change");
    after = before;
    after.rows[1].project = true;
    check(power_rows_differ(&before, &after), "a project thread mark is a change");
    after = before;
    after.total = 3;
    check(power_rows_differ(&before, &after), "a new total is a change");
    after = before;
    after.total = 1;
    after.shown = 1;
    check(power_rows_differ(&before, &after), "a row going away is a change");
}

int main(void)
{
    test_usb_keeps_the_screen_on();
    test_battery_dims_after_five_quiet_minutes();
    test_changes_restart_the_count();
    test_waking();
    test_unplugging();
    test_shorter_idle();
    test_what_counts_as_a_change();
    if (failures == 0) {
        printf("ALL PASSED\n");
    }
    return failures == 0 ? 0 : 1;
}
