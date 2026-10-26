#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "host_character.h"
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
    scene_t scene = {0};
    scene.screen.mode = mode;
    scene.screen.total = total;
    scene.screen.tick = tick;
    scene.screen.daily = tick;
    return scene;
}

static void add(scene_t *scene, row_state_t state, uint16_t minutes, const char *name)
{
    row_t *row = &scene->screen.rows[scene->screen.shown++];
    row->id = (uint8_t)scene->screen.shown;
    row->count = 1;
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
    scene_t pattern = {.test_pattern = true};
    scene_t pairing = {.pairing = SCENE_PAIRING_NUMCMP, .pair_code = 123456};
    check_tiles("pattern", &pattern);
    check_tiles("pairing", &pairing);
    check(SCREEN_THEME_COUNT == 4, "four themes");
    for (uint8_t theme = 0; theme < SCREEN_THEME_COUNT; theme++) {
        scene_t unlinked = sessions(SCREEN_UNLINKED, 0, 0);
        scene_t empty = sessions(SCREEN_EMPTY, 0, 0);
        scene_t rows = six_rows(0);
        unlinked.theme = empty.theme = rows.theme = theme;
        char what[48];
        snprintf(what, sizeof(what), "theme %u unlinked", theme);
        check_tiles(what, &unlinked);
        snprintf(what, sizeof(what), "theme %u empty", theme);
        check_tiles(what, &empty);
        snprintf(what, sizeof(what), "theme %u six rows", theme);
        check_tiles(what, &rows);
    }
}

static int sessions_of(const theme_t *theme)
{
    return theme->draw_home != NULL ? THEME_GRID_SESSIONS : THEME_SLOTS;
}

static rect_t slot_band(const theme_t *theme, int slot)
{
    int top = theme->header.y + theme->header.h;
    int bottom = theme->footer.y;
    for (int i = 0; i < THEME_SLOTS; i++) {
        const rect_t *other = &theme->slots[i];
        if (other->y < theme->slots[slot].y && other->y + other->h > top) {
            top = other->y + other->h;
        }
        if (other->y > theme->slots[slot].y && other->y < bottom) {
            bottom = other->y;
        }
    }
    if (bottom < theme->footer.y) {
        bottom = theme->slots[slot].y + theme->slots[slot].h;
    }
    return (rect_t){0, top, SCENE_W, bottom - top};
}

static void test_theme_slots(void)
{
    for (uint8_t theme_index = 0; theme_index < SCREEN_THEME_COUNT; theme_index++) {
        const theme_t *theme = screen_theme(theme_index);
        scene_t rows = six_rows(0);
        rows.theme = theme_index;
        char what[64];
        for (int slot = 0; slot < sessions_of(theme); slot++) {
            scene_t state = rows;
            state.screen.rows[slot].state = rows.screen.rows[slot].state == ROW_ERROR ? ROW_DONE : ROW_ERROR;
            state.screen.rows[slot].minutes = state.screen.rows[slot].state == ROW_DONE ? 9 : ROWS_HIDDEN_MINUTES;
            snprintf(what, sizeof(what), "theme %u slot %d state change redraws its band only", theme_index, slot);
            check_only(what, &rows, &state, slot_band(theme, slot));
        }
        scene_t more = rows;
        more.screen.total = 7;
        snprintf(what, sizeof(what), "theme %u seventh session changes the header only", theme_index);
        check_only(what, &rows, &more, theme->header);
    }
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

    for (uint8_t theme = 0; theme < SCREEN_THEME_COUNT; theme++) {
        scene_t plain = rows;
        plain.theme = theme;
        scene_t project = plain;
        project.screen.rows[2].project = true;
        char what[64];
        snprintf(what, sizeof(what), "theme %u project mark redraws that slot only", theme);
        rect_t slot = screen_theme(theme)->slots[2];
        span_t spans[DIRTY_MAX_REGIONS];
        int count = check_pair(what, &plain, &project, spans);
        check(count == 1 && spans[0].y <= slot.y && spans[0].y + spans[0].h >= slot.y + slot.h &&
                  spans[0].h < 2 * slot.h,
              what);
    }

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
    scene_t pattern = {.test_pattern = true};
    check_pair("rows to empty", &rows, &empty, spans);
    check_pair("empty to unlinked", &empty, &unlinked, spans);
    check_pair("empty to rows", &empty, &rows, spans);
    check(check_pair("pattern to board", &pattern, &rows, spans) == 1 && spans[0].h == SCENE_H,
          "leaving the test pattern redraws everything");
}

static bool rect_inside(rect_t inner, rect_t outer)
{
    return inner.w > 0 && inner.h > 0 && inner.x >= outer.x && inner.y >= outer.y &&
           inner.x + inner.w <= outer.x + outer.w && inner.y + inner.h <= outer.y + outer.h;
}

static bool point_inside(int x, int y, rect_t r)
{
    return x >= r.x && y >= r.y && x < r.x + r.w && y < r.y + r.h;
}

static void test_character_stages(void)
{
    for (uint8_t index = 0; index < SCREEN_THEME_COUNT; index++) {
        const theme_t *theme = screen_theme(index);
        bool grid = index >= 2;
        char what[80];
        snprintf(what, sizeof(what), "theme %u gives the character a home cell only in grid themes", index);
        check((theme->draw_home != NULL) == grid, what);
        snprintf(what, sizeof(what), "theme %u home lies in the sixth cell or the footer", index);
        check(rect_inside(character_bounds(&theme->home), grid ? theme->slots[THEME_SLOTS - 1] : theme->footer), what);
        character_box_t small = character_art_box(CHARACTER_SMALL);
        const character_stage_t *stages[] = {&theme->home, &theme->message};
        for (int k = 0; k < 2; k++) {
            snprintf(what, sizeof(what), "theme %u stage %d shows the whole box inside its clip", index, k);
            check(rect_inside(stages[k]->box, stages[k]->clip), what);
            snprintf(what, sizeof(what), "theme %u stage %d box holds the largest small image", index, k);
            check(stages[k]->box.w >= small.w && stages[k]->box.h >= small.h, what);
        }
        snprintf(what, sizeof(what), "theme %u message stage lies in the body or the footer", index);
        check(rect_inside(character_bounds(&theme->message), grid ? theme->body : theme->footer), what);
        rect_t home = character_bounds(&theme->home);
        for (int slot = 0; slot < sessions_of(theme); slot++) {
            rect_t cell = theme->slots[slot];
            bool apart = home.x >= cell.x + cell.w || cell.x >= home.x + home.w || home.y >= cell.y + cell.h ||
                         cell.y >= home.y + home.h;
            snprintf(what, sizeof(what), "theme %u character never covers session slot %d", index, slot);
            check(apart, what);
        }
    }
}


static const int GROWN[SCREEN_THEME_COUNT][THEME_SLOTS + 1] = {
    {145, 200, 200, 145, 145, 74, 74},
    {145, 200, 200, 145, 145, 74, 74},
    {74, 145, 145, 74, 74, 74, 74},
    {74, 145, 145, 74, 74, 74, 74},
};

static scene_t with_sessions(uint8_t theme, int count, screen_mode_t empty_mode)
{
    scene_t scene = count == 0 ? sessions(empty_mode, 0, 0) : six_rows(0);
    scene.theme = theme;
    if (count > 0) {
        scene.screen.shown = scene.screen.total = (uint8_t)count;
    }
    return scene;
}

static bool apart(rect_t a, rect_t b)
{
    return a.x >= b.x + b.w || b.x >= a.x + a.w || a.y >= b.y + b.h || b.y >= a.y + a.h;
}

static void test_character_grows(void)
{
    rect_t screen = {0, 0, SCENE_W, SCENE_H};
    for (uint8_t index = 0; index < SCREEN_THEME_COUNT; index++) {
        const theme_t *theme = screen_theme(index);
        for (int count = 0; count <= THEME_SLOTS; count++) {
            for (int k = 0; k < (count == 0 ? 2 : 1); k++) {
                scene_t scene = with_sessions(index, count, k == 0 ? SCREEN_EMPTY : SCREEN_UNLINKED);
                character_stage_t stage = screen_character_stage(theme, &scene.screen);
                character_box_t size = character_art_box((character_size_t)stage.size);
                char what[96];
                snprintf(what, sizeof(what), "theme %u with %d sessions (screen %d) the character is %d px tall", index,
                         count, k, GROWN[index][count]);
                check(stage.box.h == GROWN[index][count], what);
                snprintf(what, sizeof(what), "theme %u with %d sessions the box is the size of its images", index, count);
                check(size.w == stage.box.w && size.h == stage.box.h, what);
                snprintf(what, sizeof(what), "theme %u with %d sessions the whole box is inside its clip", index, count);
                check(rect_inside(stage.box, stage.clip) && rect_inside(stage.clip, screen), what);
                snprintf(what, sizeof(what), "theme %u with %d sessions the question and bubble stay on screen", index,
                         count);
                check(rect_inside(stage.extra, screen), what);
                int shown = count < sessions_of(theme) ? count : sessions_of(theme);
                for (int slot = 0; slot < shown; slot++) {
                    snprintf(what, sizeof(what), "theme %u with %d sessions the character keeps off session slot %d",
                             index, count, slot);
                    check(apart(stage.box, theme->slots[slot]) && apart(stage.extra, theme->slots[slot]) &&
                              apart(stage.clip, theme->slots[slot]),
                          what);
                }
            }
        }
    }
}

static void check_character_step(const char *what, const scene_t *before, const scene_t *after)
{
    span_t spans[DIRTY_MAX_REGIONS];
    check_pair(what, before, after, spans);
    const theme_t *theme = screen_theme(before->theme);
    character_stage_t stage = screen_character_stage(theme, &before->screen);
    rect_t bounds = character_bounds(&stage);
    bool moving[THEME_SLOTS] = {false};
    for (int i = 0; i < before->screen.shown && i < sessions_of(theme); i++) {
        row_state_t state = before->screen.rows[i].state;
        moving[i] = theme->slot_phase(state, before->screen.tick) != theme->slot_phase(state, after->screen.tick);
    }
    int inside = 0;
    int outside = 0;
    for (int y = 0; y < SCENE_H; y++) {
        for (int x = 0; x < SCENE_W; x++) {
            if (first[y * SCENE_W + x] == second[y * SCENE_W + x]) {
                continue;
            }
            bool allowed = point_inside(x, y, bounds);
            inside += allowed;
            for (int i = 0; i < THEME_SLOTS && !allowed; i++) {
                allowed = moving[i] && point_inside(x, y, slot_band(theme, i));
            }
            outside += !allowed;
        }
    }
    if (inside == 0 || outside != 0) {
        printf("FAIL %s: %d pixels changed in the character area, %d elsewhere\n", what, inside, outside);
        failures++;
    }
}

static void test_character_moves(void)
{
    static const uint32_t steps[][2] = {{49, 50}, {52, 53}, {67, 68}, {97, 98}, {127, 128}, {132, 133}, {177, 178}};
    static const uint8_t rows_counts[] = {5, 6, 0, 0, 1, 2, 4};
    for (uint8_t index = 0; index < SCREEN_THEME_COUNT; index++) {
        for (int kind = 0; kind < 7; kind++) {
            for (size_t s = 0; s < sizeof steps / sizeof steps[0]; s++) {
                bool message = kind == 2 || kind == 3;
                scene_t before = message ? sessions(kind == 2 ? SCREEN_EMPTY : SCREEN_UNLINKED, 0, steps[s][0])
                                         : six_rows(steps[s][0]);
                if (!message) {
                    before.screen.shown = before.screen.total = rows_counts[kind];
                }
                before.theme = index;
                scene_t after = before;
                after.screen.tick = steps[s][1];
                after.screen.daily = steps[s][1];
                char what[80];
                snprintf(what, sizeof(what), "theme %u scene %d tick %u to %u", index, kind, (unsigned)steps[s][0],
                         (unsigned)steps[s][1]);
                check_character_step(what, &before, &after);
            }
        }
    }
}

static void test_home_cell(void)
{
    for (uint8_t index = 2; index < SCREEN_THEME_COUNT; index++) {
        scene_t five = six_rows(0);
        five.theme = index;
        five.screen.shown = five.screen.total = 5;
        scene_t full = six_rows(0);
        full.theme = index;
        char what[80];
        snprintf(what, sizeof(what), "theme %u sixth session keeps the home cell and only adds +1", index);
        check_only(what, &five, &full, screen_theme(index)->header);
    }
}

static uint32_t next_random(uint32_t *seed)
{
    *seed = *seed * 1103515245u + 12345u;
    return *seed >> 8;
}

static void test_theme_order(void)
{
    check(screen_next_theme(0) == 1, "board is followed by arcade");
    check(screen_next_theme(1) == 2, "arcade is followed by comic");
    check(screen_next_theme(2) == 3, "comic is followed by tower");
    check(screen_next_theme(3) == 0, "tower wraps round to board");
    check(screen_next_theme(200) == 1, "an unknown theme counts as board");
    check(screen_stored_theme(false, 3) == 0, "nothing stored means board");
    check(screen_stored_theme(true, 2) == 2, "a stored theme is used");
    check(screen_stored_theme(true, SCREEN_THEME_COUNT) == 0, "a stored theme out of range means board");
}

static void test_arcade_error_shakes(void)
{
    scene_t before = sessions(SCREEN_ROWS, 2, 0);
    before.theme = 1;
    add(&before, ROW_ERROR, 0, "SiBlog");
    add(&before, ROW_DONE, 5, "FunMedal");
    scene_t after = before;
    after.screen.tick = 1;
    check_only("arcade error row shakes and redraws its own row only", &before, &after, screen_theme(1)->slots[0]);
    check(memcmp(first, second, sizeof(first)) != 0, "arcade error row moves between ticks");
}

static scene_t with_event(scene_t scene, event_kind_t kind, uint32_t ms, const char *name)
{
    scene.screen.event.kind = kind;
    scene.screen.event.ms = ms;
    snprintf(scene.screen.event.name, sizeof scene.screen.event.name, "%s", name);
    return scene;
}

static void random_sequence(uint8_t theme)
{
    static const char *const names[] = {"FunMedal", "SiBlog", "绘本工坊", "a", "一个名字特别特别长的项目会被截短"};
    uint32_t seed = 7 + theme;
    scene_t before = six_rows(0);
    before.theme = theme;
    for (int step = 0; step < 300; step++) {
        scene_t after = before;
        uint32_t pick = next_random(&seed) % 7;
        if (pick == 0) {
            uint32_t ticks = 1 + next_random(&seed) % 4;
            after.screen.tick += ticks;
            after.screen.daily += ticks;
            after.screen.event.ms += ticks * THEME_TICK_MS;
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
            row->project = next_random(&seed) % 2 == 0;
        } else if (pick == 5) {
            after.screen.total = (uint8_t)(after.screen.shown + next_random(&seed) % 4);
        } else {
            event_kind_t kind = (event_kind_t)(next_random(&seed) % EVENT_KINDS);
            after = with_event(after, kind, next_random(&seed) % 7000, names[next_random(&seed) % 5]);
            after.screen.daily = next_random(&seed) % 400;
        }
        span_t spans[DIRTY_MAX_REGIONS];
        char what[48];
        snprintf(what, sizeof(what), "theme %u random step %d", theme, step);
        check_pair(what, &before, &after, spans);
        before = after;
    }
}

static void check_inside(const char *what, const scene_t *before, const scene_t *after, rect_t area)
{
    span_t spans[DIRTY_MAX_REGIONS];
    int count = check_pair(what, before, after, spans);
    check(count > 0, what);
    for (int i = 0; i < count; i++) {
        if (spans[i].y + spans[i].h <= area.y || spans[i].y >= area.y + area.h) {
            printf("FAIL %s: span %d+%d lies outside %d+%d\n", what, spans[i].y, spans[i].h, area.y, area.h);
            failures++;
        }
    }
}

static void test_events(void)
{
    for (uint8_t theme = 0; theme < SCREEN_THEME_COUNT; theme++) {
        char what[96];
        scene_t calm = six_rows(40);
        calm.theme = theme;
        calm.screen.daily = 17;
        rect_t rise = screen_theme(theme)->alert.stage.clip;
        scene_t before = calm;
        for (uint32_t ms = 0; ms <= SCREEN_RISE_FRAMES * SCREEN_RISE_MS; ms += SCREEN_RISE_MS) {
            scene_t after = with_event(calm, EVENT_WAIT, ms, "FunMedal");
            after.screen.daily = 0;
            snprintf(what, sizeof what, "theme %u alert at %u ms", theme, (unsigned)ms);
            if (ms == 0) {
                check_pair(what, &before, &after, (span_t[DIRTY_MAX_REGIONS]){0});
            } else {
                check_inside(what, &before, &after, rise);
            }
            before = after;
        }
        scene_t risen = with_event(calm, EVENT_WAIT, 5900, "FunMedal");
        risen.screen.daily = 0;
        risen.screen.tick = before.screen.tick;
        snprintf(what, sizeof what, "theme %u alert holds still once risen", theme);
        check_nothing(what, &before, &risen);
        scene_t error = with_event(risen, EVENT_ERROR, 900, "SiBlog");
        snprintf(what, sizeof what, "theme %u error alert replaces the waiting one", theme);
        check_pair(what, &risen, &error, (span_t[DIRTY_MAX_REGIONS]){0});
        scene_render_rows(&risen, 0, SCENE_H, first);
        scene_render_rows(&error, 0, SCENE_H, second);
        snprintf(what, sizeof what, "theme %u error alert looks different from the waiting one", theme);
        check(memcmp(first, second, sizeof first) != 0, what);
        scene_t closed = calm;
        snprintf(what, sizeof what, "theme %u screen comes back when the alert closes", theme);
        check_pair(what, &error, &closed, (span_t[DIRTY_MAX_REGIONS]){0});
        scene_t cheer = with_event(calm, EVENT_CHEER, 100, "agent-skills");
        snprintf(what, sizeof what, "theme %u cheer", theme);
        check_pair(what, &calm, &cheer, (span_t[DIRTY_MAX_REGIONS]){0});
        scene_t hello = with_event(calm, EVENT_HELLO, 100, "");
        snprintf(what, sizeof what, "theme %u hello", theme);
        check_pair(what, &cheer, &hello, (span_t[DIRTY_MAX_REGIONS]){0});
        snprintf(what, sizeof what, "theme %u hello back to the table", theme);
        check_pair(what, &hello, &calm, (span_t[DIRTY_MAX_REGIONS]){0});
        scene_t later = hello;
        later.screen.tick += 12;
        later.screen.daily += 12;
        later.screen.event.ms += 1200;
        snprintf(what, sizeof what, "theme %u hello holds still while it shows", theme);
        check_nothing(what, &hello, &later);
    }
}

static void test_random_sequence(void)
{
    for (uint8_t theme = 0; theme < SCREEN_THEME_COUNT; theme++) {
        random_sequence(theme);
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
    draw_dashed(&band, (rect_t){20, 100, 40, 10}, 2, 4, 0xffffff);
    check(band_pixels[0 * DRAW_W + 20] != black && band_pixels[0 * DRAW_W + 23] != black, "a dash starts at the corner");
    check(band_pixels[0 * DRAW_W + 24] == black && band_pixels[0 * DRAW_W + 28] != black, "dashes and gaps alternate");
    check(band_pixels[9 * DRAW_W + 20] != black && band_pixels[5 * DRAW_W + 30] == black, "a dashed border leaves the middle alone");
    check(band_pixels[4 * DRAW_W + 59] == black && band_pixels[8 * DRAW_W + 59] != black, "the right edge is dashed too");

    for (int i = 0; i < DRAW_W * 10; i++) {
        band_pixels[i] = black;
    }
    draw_dots(&band, (rect_t){0, 100, 30, 10}, 6, 0xffffff, 15);
    int dots = 0;
    for (int i = 0; i < DRAW_W * 10; i++) {
        dots += band_pixels[i] != black;
    }
    check(dots == 5 * 2, "dots fall every sixth pixel in both directions inside the rectangle");
    check(band_pixels[3 * DRAW_W + 3] != black && band_pixels[9 * DRAW_W + 27] != black, "dots sit half a period in");

    for (int i = 0; i < DRAW_W * 10; i++) {
        band_pixels[i] = black;
    }
    draw_tint(&band, (rect_t){0, 100, 10, 10}, 0, 0xffffff, 8);
    uint32_t tinted = draw_rgb(band_pixels[5 * DRAW_W + 5]);
    check(tinted != 0 && tinted != 0xffffff, "a tint blends with what is underneath");

    for (int i = 0; i < DRAW_W * 10; i++) {
        band_pixels[i] = black;
    }
    draw_wedge(&band, (rect_t){100, 100, 10, 10}, 0, 0xffffff, 15);
    check(band_pixels[1 * DRAW_W + 105] != black, "wedge step 0 runs clockwise from twelve o'clock");
    check(band_pixels[1 * DRAW_W + 103] == black, "wedge step 0 leaves the left of twelve o'clock alone");
    check(band_pixels[8 * DRAW_W + 105] == black, "wedge step 0 leaves the bottom alone");
    for (int i = 0; i < DRAW_W * 10; i++) {
        band_pixels[i] = black;
    }
    draw_wedge(&band, (rect_t){100, 100, 10, 10}, 8, 0xffffff, 15);
    check(band_pixels[9 * DRAW_W + 104] != black, "wedge step 8 points to six o'clock");
    int wedge_lit = 0;
    for (int step = 0; step < DRAW_WEDGE_STEPS; step++) {
        draw_wedge(&band, (rect_t){100, 100, 10, 10}, step, 0xffffff, 15);
    }
    for (int y = 0; y < 10; y++) {
        for (int x = 100; x < 110; x++) {
            wedge_lit += band_pixels[y * DRAW_W + x] != black;
        }
    }
    check(wedge_lit > 70 && wedge_lit < 100, "all wedges together fill the circle and not its corners");
    check(band_pixels[0 * DRAW_W + 100] == black, "a wedge never reaches the square's corner");

    for (int i = 0; i < DRAW_W * 10; i++) {
        band_pixels[i] = black;
    }
    draw_round(&band, (rect_t){0, 95, DRAW_W, 20}, 8, 0xffffff);
    draw_stripes(&band, (rect_t){0, 95, DRAW_W, 20}, 8, 16, 3, 0xff0000, 15);
    draw_text(&band, &font_text, "FunMedal 绘本", 4, 92, DRAW_W, 0x0000ff);
    check(band_pixels[0] != black && band_pixels[9 * DRAW_W] != black, "shapes are drawn on every band row they cross");
    check(band_pixels[9 * DRAW_W + 120] != black, "a rounded shape fills its middle");
}

static void test_image(void)
{
    static uint16_t band_pixels[DRAW_W * 2];
    band_t band = {band_pixels, 100, 2};
    uint16_t black = draw_wire(0x000000);
    for (int i = 0; i < DRAW_W * 2; i++) {
        band_pixels[i] = black;
    }
    uint16_t red = draw_wire(0xff0000);
    uint16_t white = draw_wire(0xffffff);
    uint8_t colors[16] = {0};
    colors[8] = (uint8_t)red;
    colors[9] = (uint8_t)(red >> 8);
    colors[12] = (uint8_t)white;
    colors[13] = (uint8_t)(white >> 8);
    colors[14] = (uint8_t)white;
    colors[15] = (uint8_t)(white >> 8);
    static const uint8_t alphas[4] = {0xff, 0xff, 0xf0, 0x8f};
    image_t image = {4, 2, colors, alphas};
    draw_image(&band, &image, 10, 99, (rect_t){0, 0, 13, SCENE_H});
    check(band_pixels[10] == red, "an opaque image pixel is copied");
    check(band_pixels[11] == black, "a clear image pixel leaves the background");
    check(band_pixels[12] == draw_wire(draw_blend(0x000000, 0xffffff, 8)), "a half clear pixel blends");
    check(band_pixels[13] == black, "an image stops at its limit");
    for (int x = 0; x < DRAW_W; x++) {
        check(band_pixels[DRAW_W + x] == black, "an image row above the band is not drawn into the next row");
    }
}

int main(int argc, char **argv)
{
    if (argc < 2 || argc > 3 || !host_fonts_load(argv[1]) || !host_character_load(argv[1])) {
        printf("usage: test_screen <fonts dir> [grow]\n");
        return 2;
    }
    if (argc == 3 && strcmp(argv[2], "grow") == 0) {
        test_character_grows();
        test_character_moves();
        if (failures == 0) {
            printf("ALL PASSED\n");
        }
        return failures == 0 ? 0 : 1;
    }
    test_primitives();
    test_image();
    test_character_stages();
    test_character_grows();
    test_character_moves();
    test_home_cell();
    test_tiles();
    test_first_draw_is_everything();
    test_animation();
    test_changes();
    test_theme_slots();
    test_arcade_error_shakes();
    test_theme_order();
    test_events();
    test_random_sequence();
    test_age();
    test_from_rows();
    if (failures == 0) {
        printf("ALL PASSED\n");
    }
    return failures == 0 ? 0 : 1;
}
