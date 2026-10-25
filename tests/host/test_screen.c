#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "host_fonts.h"
#include "rows.h"
#include "scene.h"
#include "screen.h"

#define MINUTE_US (60 * 1000000LL)

static int failures;
static uint16_t first[SCENE_W * SCENE_H];
static uint16_t second[SCENE_W * SCENE_H];

static void check(int ok, const char *what)
{
    if (!ok) {
        printf("FAIL %s\n", what);
        failures++;
    }
}

static scene_t sessions(screen_mode_t mode, uint8_t total, uint32_t tick)
{
    scene_t scene = {.state = PET_IDLE, .sessions = true};
    scene.screen.mode = mode;
    scene.screen.total = total;
    scene.screen.tick = tick;
    return scene;
}

static void add(scene_t *scene, row_state_t state, uint16_t minutes, const char *name)
{
    row_t *row = &scene->screen.rows[scene->screen.shown++];
    row->id = (uint8_t)scene->screen.shown;
    row->state = state;
    row->minutes = state == ROW_DONE ? minutes : ROWS_HIDDEN_MINUTES;
    snprintf(row->name, sizeof(row->name), "%s", name);
    if (scene->screen.total < scene->screen.shown) {
        scene->screen.total = scene->screen.shown;
    }
}

static scene_t six_rows(uint32_t tick)
{
    scene_t scene = sessions(SCREEN_ROWS, 6, tick);
    scene.screen.total = 0;
    add(&scene, ROW_WAITING, 0, "FunMedal");
    add(&scene, ROW_ERROR, 0, "SiBlog");
    add(&scene, ROW_RUNNING, 0, "himawari-agent");
    add(&scene, ROW_RUNNING, 0, "绘本工坊");
    add(&scene, ROW_DONE, 5, "agent-skills");
    add(&scene, ROW_DONE, 135, "一个名字特别特别长的项目会被截短");
    return scene;
}

static int differing_rows(const uint16_t *a, const uint16_t *b, int *rows)
{
    int count = 0;
    for (int y = 0; y < SCENE_H; y++) {
        if (memcmp(a + y * SCENE_W, b + y * SCENE_W, SCENE_W * sizeof(uint16_t)) != 0) {
            rows[count++] = y;
        }
    }
    return count;
}

static void check_tiles(const char *what, const scene_t *scene)
{
    region_t regions[DIRTY_MAX_REGIONS];
    int count = scene_regions(scene, regions);
    int y = 0;
    for (int i = 0; i < count; i++) {
        if (regions[i].y != y || regions[i].h <= 0) {
            printf("FAIL %s: region %d starts at %d, expected %d\n", what, i, regions[i].y, y);
            failures++;
            return;
        }
        y += regions[i].h;
    }
    if (y != SCENE_H) {
        printf("FAIL %s: regions end at %d\n", what, y);
        failures++;
    }
}

static int redraw(const scene_t *before, const scene_t *after, span_t *spans)
{
    region_t a[DIRTY_MAX_REGIONS];
    region_t b[DIRTY_MAX_REGIONS];
    int ac = scene_regions(before, a);
    int bc = scene_regions(after, b);
    return dirty_spans(a, ac, b, bc, spans);
}

static int check_pair(const char *what, const scene_t *before, const scene_t *after, span_t *spans)
{
    scene_render_rows(before, 0, SCENE_H, first);
    scene_render_rows(after, 0, SCENE_H, second);
    int changed[SCENE_H];
    int count = differing_rows(first, second, changed);
    int span_count = redraw(before, after, spans);
    for (int i = 0; i < count; i++) {
        int y = changed[i];
        bool covered = false;
        for (int s = 0; s < span_count; s++) {
            covered |= y >= spans[s].y && y < spans[s].y + spans[s].h;
        }
        if (!covered) {
            printf("FAIL %s: row %d changed but is not redrawn\n", what, y);
            failures++;
            break;
        }
    }
    return span_count;
}

static void check_only(const char *what, const scene_t *before, const scene_t *after, rect_t area)
{
    span_t spans[DIRTY_MAX_REGIONS];
    int count = check_pair(what, before, after, spans);
    if (count != 1 || spans[0].y != area.y || spans[0].h != area.h) {
        printf("FAIL %s: expected one span %d+%d, got %d spans", what, area.y, area.h, count);
        for (int i = 0; i < count; i++) {
            printf(" %d+%d", spans[i].y, spans[i].h);
        }
        printf("\n");
        failures++;
    }
}

static void check_nothing(const char *what, const scene_t *before, const scene_t *after)
{
    span_t spans[DIRTY_MAX_REGIONS];
    int count = check_pair(what, before, after, spans);
    check(count == 0, what);
    check(memcmp(first, second, sizeof(first)) == 0, what);
}

static void test_tiles(void)
{
    scene_t pet = {.state = PET_WAITING};
    scene_t pairing = {.state = PET_IDLE, .pairing = SCENE_PAIRING_NUMCMP, .pair_code = 123456};
    scene_t unlinked = sessions(SCREEN_UNLINKED, 0, 0);
    scene_t empty = sessions(SCREEN_EMPTY, 0, 0);
    scene_t rows = six_rows(0);
    check_tiles("pet", &pet);
    check_tiles("pairing", &pairing);
    check_tiles("unlinked", &unlinked);
    check_tiles("empty", &empty);
    check_tiles("six rows", &rows);
}

static void test_first_draw_is_everything(void)
{
    region_t after[DIRTY_MAX_REGIONS];
    span_t spans[DIRTY_MAX_REGIONS];
    scene_t rows = six_rows(0);
    int count = dirty_spans(NULL, 0, after, scene_regions(&rows, after), spans);
    check(count == 1 && spans[0].y == 0 && spans[0].h == SCENE_H, "first draw covers the whole screen in one span");
}

static void test_animation(void)
{
    scene_t still = sessions(SCREEN_ROWS, 0, 0);
    add(&still, ROW_ERROR, 0, "SiBlog");
    add(&still, ROW_DONE, 3, "FunMedal");
    scene_t later = still;
    later.screen.tick = 7;
    check_nothing("error and done rows do not animate", &still, &later);

    scene_t running = sessions(SCREEN_ROWS, 0, 0);
    add(&running, ROW_DONE, 3, "a");
    add(&running, ROW_ERROR, 0, "b");
    add(&running, ROW_RUNNING, 0, "c");
    scene_t next = running;
    next.screen.tick = 1;
    check_only("a running row redraws only its slot on the next tick", &running, &next, theme_board.slots[2]);

    scene_t waiting = sessions(SCREEN_ROWS, 0, 0);
    add(&waiting, ROW_WAITING, 0, "a");
    add(&waiting, ROW_DONE, 3, "b");
    scene_t breathing = waiting;
    breathing.screen.tick = 3;
    check_only("a waiting row breathes in its slot only", &waiting, &breathing, theme_board.slots[0]);
    scene_t round = waiting;
    round.screen.tick = 10;
    check_nothing("breathing comes back after one second", &waiting, &round);
}

static void test_changes(void)
{
    scene_t rows = six_rows(0);
    scene_t state = rows;
    state.screen.rows[4].state = ROW_RUNNING;
    state.screen.rows[4].minutes = ROWS_HIDDEN_MINUTES;
    check_only("a state change redraws that slot only", &rows, &state, theme_board.slots[4]);

    scene_t name = rows;
    snprintf(name.screen.rows[1].name, sizeof(name.screen.rows[1].name), "%s", "SiBlog2");
    check_only("a new name redraws that slot only", &rows, &name, theme_board.slots[1]);

    scene_t more = rows;
    more.screen.total = 7;
    check_only("a seventh session changes the header only", &rows, &more, theme_board.header);

    scene_t minute = rows;
    minute.screen.rows[4].minutes = 6;
    check_only("a done row redraws when its minutes text changes", &rows, &minute, theme_board.slots[4]);

    scene_t same_hour = rows;
    same_hour.screen.rows[5].minutes = 136;
    check_nothing("a done row in the same hour keeps its pixels", &rows, &same_hour);

    span_t spans[DIRTY_MAX_REGIONS];
    scene_t empty = sessions(SCREEN_EMPTY, 0, 0);
    scene_t unlinked = sessions(SCREEN_UNLINKED, 0, 0);
    scene_t pet = {.state = PET_DONE};
    check_pair("rows to empty", &rows, &empty, spans);
    check_pair("empty to unlinked", &empty, &unlinked, spans);
    check_pair("empty to rows", &empty, &rows, spans);
    check(check_pair("pet to sessions", &pet, &rows, spans) == 1 && spans[0].h == SCENE_H,
          "leaving the pet screen redraws everything");
}

static uint32_t next_random(uint32_t *seed)
{
    *seed = *seed * 1103515245u + 12345u;
    return *seed >> 8;
}

static void test_random_sequence(void)
{
    static const char *const names[] = {"FunMedal", "SiBlog", "绘本工坊", "a", "一个名字特别特别长的项目会被截短"};
    uint32_t seed = 7;
    scene_t before = six_rows(0);
    for (int step = 0; step < 300; step++) {
        scene_t after = before;
        uint32_t pick = next_random(&seed) % 6;
        if (pick == 0) {
            after.screen.tick += 1 + next_random(&seed) % 4;
        } else if (pick == 1) {
            after.screen.mode = (screen_mode_t)(next_random(&seed) % 3);
        } else if (pick == 2 && after.screen.shown > 0) {
            after.screen.shown--;
            after.screen.total = after.screen.shown;
        } else if (pick == 3 && after.screen.shown < ROWS_MAX) {
            row_state_t state = (row_state_t)(ROW_WAITING + next_random(&seed) % 4);
            add(&after, state, (uint16_t)(next_random(&seed) % 3000), names[next_random(&seed) % 5]);
        } else if (pick == 4 && after.screen.shown > 0) {
            row_t *row = &after.screen.rows[next_random(&seed) % after.screen.shown];
            row->state = (row_state_t)(ROW_WAITING + next_random(&seed) % 4);
            row->minutes = row->state == ROW_DONE ? (uint16_t)(next_random(&seed) % 3000) : ROWS_HIDDEN_MINUTES;
        } else {
            after.screen.total = (uint8_t)(after.screen.shown + next_random(&seed) % 4);
        }
        span_t spans[DIRTY_MAX_REGIONS];
        char what[48];
        snprintf(what, sizeof(what), "random step %d", step);
        check_pair(what, &before, &after, spans);
        before = after;
    }
}

static void test_age(void)
{
    static const struct {
        uint16_t minutes;
        const char *text;
    } cases[] = {
        {0, "刚刚"}, {1, "1 分钟前"}, {59, "59 分钟前"}, {60, "1 小时前"}, {119, "1 小时前"},
        {1439, "23 小时前"}, {1440, "1 天前"}, {44640, "31 天前"},
    };
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        char text[SCREEN_AGE_MAX];
        screen_age(cases[i].minutes, text, sizeof(text));
        if (strcmp(text, cases[i].text) != 0) {
            printf("FAIL age %u gave %s, expected %s\n", cases[i].minutes, text, cases[i].text);
            failures++;
        }
    }
}

static void test_from_rows(void)
{
    rows_table_t table;
    rows_init(&table);
    screen_t screen;
    screen_from_rows(&screen, &table, 0, 0);
    check(screen.mode == SCREEN_UNLINKED, "no link shows unlinked");
    rows_link(&table, 0);
    screen_from_rows(&screen, &table, 0, 0);
    check(screen.mode == SCREEN_UNLINKED, "a link without a table yet still shows unlinked");
    rows_frame_t frame = {.total = 0, .shown = 0};
    rows_heard(&table, ROWS_TABLE, &frame, 0);
    screen_from_rows(&screen, &table, 0, 0);
    check(screen.mode == SCREEN_EMPTY, "an empty table shows no sessions");
    frame.total = 7;
    frame.shown = 1;
    frame.rows[0] = (row_t){.id = 1, .state = ROW_DONE, .minutes = 5, .name = "FunMedal"};
    rows_heard(&table, ROWS_TABLE, &frame, 0);
    screen_from_rows(&screen, &table, 2 * MINUTE_US + 1, 9);
    check(screen.mode == SCREEN_ROWS && screen.total == 7 && screen.shown == 1, "a table shows rows and the total");
    check(screen.rows[0].minutes == 7 && screen.tick == 9, "done minutes count on from the table");
    rows_heard(&table, ROWS_HEARTBEAT, &frame, 3 * MINUTE_US);
    rows_lose(&table);
    screen_from_rows(&screen, &table, 0, 0);
    check(screen.mode == SCREEN_UNLINKED, "a lost link shows unlinked");
}

static void test_primitives(void)
{
    static uint16_t band_pixels[DRAW_W * 10];
    band_t band = {band_pixels, 100, 10};
    uint16_t black = draw_wire(0x000000);
    for (int i = 0; i < DRAW_W * 10; i++) {
        band_pixels[i] = black;
    }
    static const uint8_t sprite[2 * 3] = {0, 1, 2, 1, 0, 1};
    static const uint32_t palette[3] = {0, 0xff0000, 0x00ff00};
    draw_sprite(&band, sprite, palette, 3, 2, 10, 98, 3);
    check(band_pixels[0 * DRAW_W + 13] == draw_wire(0xff0000), "a band starting inside the first sprite row draws it");
    check(band_pixels[0 * DRAW_W + 10] == black, "palette index 0 leaves the pixel alone");
    check(band_pixels[1 * DRAW_W + 10] == draw_wire(0xff0000), "the second sprite row starts scale rows lower");
    check(band_pixels[3 * DRAW_W + 18] == draw_wire(0xff0000), "each sprite pixel covers scale by scale pixels");
    check(band_pixels[4 * DRAW_W + 10] == black, "a sprite stops after height times scale rows");
    check(band_pixels[1 * DRAW_W + 19] == black, "a sprite stops after width times scale columns");

    for (int i = 0; i < DRAW_W * 10; i++) {
        band_pixels[i] = black;
    }
    draw_scanlines(&band, (rect_t){0, 90, 20, 100}, 4, 0xffffff, 15);
    int lit = 0;
    for (int y = 0; y < 10; y++) {
        bool on = band_pixels[y * DRAW_W] != black;
        lit += on;
        check(on == ((100 + y - 90) % 4 == 0), "scanlines fall every fourth row counted from the rectangle top");
        check(band_pixels[y * DRAW_W + 20] == black, "scanlines stay inside the rectangle");
    }
    check(lit == 2, "two scanlines cross rows 100 to 109");

    for (int i = 0; i < DRAW_W * 10; i++) {
        band_pixels[i] = black;
    }
    draw_round(&band, (rect_t){0, 95, DRAW_W, 20}, 8, 0xffffff);
    draw_stripes(&band, (rect_t){0, 95, DRAW_W, 20}, 8, 16, 3, 0xff0000, 15);
    draw_text(&band, &font_text, "FunMedal 绘本", 4, 92, DRAW_W, 0x0000ff);
    check(band_pixels[0] != black && band_pixels[9 * DRAW_W] != black, "shapes are drawn on every band row they cross");
    check(band_pixels[9 * DRAW_W + 120] != black, "a rounded shape fills its middle");
}

int main(int argc, char **argv)
{
    if (argc != 2 || !host_fonts_load(argv[1])) {
        printf("usage: test_screen <fonts dir>\n");
        return 2;
    }
    test_primitives();
    test_tiles();
    test_first_draw_is_everything();
    test_animation();
    test_changes();
    test_random_sequence();
    test_age();
    test_from_rows();
    if (failures == 0) {
        printf("ALL PASSED\n");
    }
    return failures == 0 ? 0 : 1;
}
