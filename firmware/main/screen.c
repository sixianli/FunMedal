#include "screen.h"

#include <stdio.h>
#include <string.h>

#define HOUR_MINUTES 60
#define DAY_MINUTES (24 * HOUR_MINUTES)
#define ROWS_ROOM_GAP 3
#define HINT_ROOM_GAP 6

static const character_size_t GROWING[] = {CHARACTER_HOME_200, CHARACTER_HOME_145, CHARACTER_HOME_112};

static const theme_t *const THEMES[SCREEN_THEME_COUNT] = {&theme_board, &theme_arcade, &theme_comic, &theme_tower};

const theme_t *screen_theme(uint8_t index)
{
    return THEMES[index < SCREEN_THEME_COUNT ? index : 0];
}

uint8_t screen_next_theme(uint8_t index)
{
    return index < SCREEN_THEME_COUNT ? (uint8_t)((index + 1) % SCREEN_THEME_COUNT) : 1;
}

uint8_t screen_stored_theme(bool found, uint8_t value)
{
    return found && value < SCREEN_THEME_COUNT ? value : 0;
}

const char *screen_age(uint16_t minutes, char *out, size_t size)
{
    if (minutes == 0) {
        snprintf(out, size, "刚刚");
    } else if (minutes < HOUR_MINUTES) {
        snprintf(out, size, "%u 分钟前", (unsigned)minutes);
    } else if (minutes < DAY_MINUTES) {
        snprintf(out, size, "%u 小时前", (unsigned)(minutes / HOUR_MINUTES));
    } else {
        snprintf(out, size, "%u 天前", (unsigned)(minutes / DAY_MINUTES));
    }
    return out;
}

void screen_from_rows(screen_t *screen, const rows_table_t *table, int64_t now_us, uint32_t tick)
{
    memset(screen, 0, sizeof *screen);
    screen->tick = tick;
    if (!table->linked || !table->has_table) {
        screen->mode = SCREEN_UNLINKED;
        return;
    }
    screen->total = table->frame.total;
    screen->shown = table->frame.shown;
    screen->mode = screen->total == 0 ? SCREEN_EMPTY : SCREEN_ROWS;
    for (int i = 0; i < screen->shown; i++) {
        screen->rows[i] = table->frame.rows[i];
        screen->rows[i].minutes = rows_minutes(table, i, now_us);
    }
}

static bool grid_theme(const theme_t *theme)
{
    return theme->draw_home != NULL;
}

static int session_slots(const theme_t *theme)
{
    return grid_theme(theme) ? THEME_GRID_SESSIONS : THEME_SLOTS;
}

static screen_t fit(const theme_t *theme, const screen_t *screen)
{
    screen_t fitted = *screen;
    if (fitted.shown > session_slots(theme)) {
        fitted.shown = (uint8_t)session_slots(theme);
    }
    return fitted;
}

static int home_first_slot(const screen_t *screen)
{
    int first = screen->shown + screen->shown % 2;
    return first < THEME_SLOTS - 1 ? first : THEME_SLOTS - 1;
}

static rect_t home_area(const theme_t *theme, const screen_t *screen)
{
    rect_t first = theme->slots[home_first_slot(screen)];
    rect_t last = theme->slots[THEME_SLOTS - 1];
    return (rect_t){first.x, first.y, last.x + last.w - first.x, last.y + last.h - first.y};
}

static rect_t home_clip(const theme_t *theme, rect_t home)
{
    rect_t cell = theme->slots[THEME_SLOTS - 1];
    rect_t clip = theme->home.clip;
    int left = clip.x - cell.x;
    int top = clip.y - cell.y;
    int right = cell.x + cell.w - clip.x - clip.w;
    int bottom = cell.y + cell.h - clip.y - clip.h;
    return (rect_t){home.x + left, home.y + top, home.w - left - right, home.h - top - bottom};
}

static int room_top(const theme_t *theme, const screen_t *screen, const character_stage_t *stage)
{
    if (screen->mode != SCREEN_ROWS) {
        return theme->body.y + theme->hint_top + theme->hint_font->line_height + HINT_ROOM_GAP;
    }
    if (grid_theme(theme)) {
        return stage->clip.y;
    }
    if (screen->shown == 0) {
        return theme->header.y + theme->header.h + ROWS_ROOM_GAP;
    }
    rect_t last = theme->slots[screen->shown - 1];
    return last.y + last.h + ROWS_ROOM_GAP;
}

static void grow(character_stage_t *stage, int top)
{
    int right = stage->box.x + stage->box.w;
    int bottom = stage->box.y + stage->box.h;
    for (size_t i = 0; i < sizeof GROWING / sizeof GROWING[0]; i++) {
        character_box_t size = character_art_box(GROWING[i]);
        rect_t box = {right - size.w, bottom - size.h, size.w, size.h};
        if (box.y >= top && box.x >= stage->clip.x) {
            stage->extra.x += box.x - stage->box.x;
            stage->extra.y += box.y - stage->box.y;
            stage->box = box;
            stage->size = (uint8_t)GROWING[i];
            return;
        }
    }
}

character_stage_t screen_character_stage(const theme_t *theme, const screen_t *full)
{
    screen_t screen = fit(theme, full);
    character_stage_t stage = screen.mode == SCREEN_ROWS ? theme->home : theme->message;
    if (screen.mode == SCREEN_ROWS && grid_theme(theme)) {
        stage.clip = home_clip(theme, home_area(theme, &screen));
    }
    int top = room_top(theme, &screen, &stage);
    if (top < stage.clip.y) {
        stage.clip.h += stage.clip.y - top;
        stage.clip.y = top;
    }
    grow(&stage, top);
    return stage;
}

void screen_render(const theme_t *theme, const screen_t *full, const band_t *band)
{
    screen_t fitted = fit(theme, full);
    const screen_t *screen = &fitted;
    theme->draw_background(band);
    theme->draw_header(band, screen, theme->header);
    if (screen->mode == SCREEN_ROWS) {
        int home = grid_theme(theme) ? home_first_slot(screen) : THEME_SLOTS;
        for (int i = 0; i < THEME_SLOTS; i++) {
            const row_t *row = &screen->rows[i];
            if (i < screen->shown) {
                theme->draw_slot(band, row, theme->slots[i], theme->slot_phase(row->state, screen->tick));
            } else if (i == home) {
                theme->draw_home(band, home_area(theme, screen));
            } else if (i < home) {
                theme->draw_empty(band, theme->slots[i]);
            }
        }
    } else {
        theme->draw_message(band, screen->mode, theme->body);
    }
    theme->draw_footer(band, screen, theme->footer);
    character_stage_t stage = screen_character_stage(theme, screen);
    character_draw(band, &stage, character_frame(screen->tick), theme->character);
}

static uint32_t hash_int(uint32_t hash, uint32_t value)
{
    return dirty_hash(hash, &value, sizeof value);
}

static region_t region(rect_t area, uint32_t key)
{
    return (region_t){(int16_t)area.y, (int16_t)area.h, key};
}

static region_t span_region(int top, int bottom, uint32_t key)
{
    return (region_t){(int16_t)top, (int16_t)(bottom - top), key};
}

static uint32_t slot_key(uint32_t base, const theme_t *theme, const screen_t *screen, int i)
{
    uint32_t key = dirty_hash_text(hash_int(base, (uint32_t)i), "slot");
    if (i >= screen->shown) {
        if (grid_theme(theme) && i >= home_first_slot(screen)) {
            return hash_int(dirty_hash_text(key, "home"), (uint32_t)home_area(theme, screen).y);
        }
        return dirty_hash_text(key, "empty");
    }
    const row_t *row = &screen->rows[i];
    char age[SCREEN_AGE_MAX] = "";
    if (row->state == ROW_DONE && row->minutes != ROWS_HIDDEN_MINUTES) {
        screen_age(row->minutes, age, sizeof age);
    }
    key = hash_int(key, row->state);
    key = hash_int(key, row->project);
    key = dirty_hash_text(key, row->name);
    key = dirty_hash_text(key, age);
    return hash_int(key, theme->slot_phase(row->state, screen->tick));
}

int screen_regions(uint8_t theme_index, const screen_t *full, region_t *out)
{
    const theme_t *theme = screen_theme(theme_index);
    screen_t fitted = fit(theme, full);
    const screen_t *screen = &fitted;
    uint32_t base = hash_int(DIRTY_HASH_START, theme_index < SCREEN_THEME_COUNT ? theme_index : 0);
    int count = 0;
    uint32_t header = dirty_hash_text(base, "header");
    header = hash_int(hash_int(hash_int(header, screen->mode), screen->total), screen->shown);
    out[count++] = region(theme->header, header);
    if (screen->mode == SCREEN_ROWS) {
        int top = theme->header.y + theme->header.h;
        for (int i = 0; i < THEME_SLOTS;) {
            int y = theme->slots[i].y;
            int bottom = y + theme->slots[i].h;
            uint32_t key = DIRTY_HASH_START;
            for (; i < THEME_SLOTS && theme->slots[i].y == y; i++) {
                key = hash_int(key, slot_key(base, theme, screen, i));
            }
            if (i == THEME_SLOTS) {
                bottom = theme->footer.y;
            }
            out[count++] = span_region(top, bottom, key);
            top = bottom;
        }
    } else {
        out[count++] = region(theme->body, hash_int(dirty_hash_text(base, "body"), screen->mode));
    }
    uint32_t footer = hash_int(dirty_hash_text(base, "footer"), theme->footer_phase(screen->tick));
    out[count++] = region(theme->footer, footer);
    character_stage_t stage = screen_character_stage(theme, screen);
    rect_t bounds = character_bounds(&stage);
    uint32_t character = hash_int(hash_int(character_frame_key(character_frame(screen->tick)), (uint32_t)bounds.x),
                                  (uint32_t)bounds.y);
    character = hash_int(character, stage.size);
    for (int i = 0; i < count; i++) {
        if (out[i].y < bounds.y + bounds.h && bounds.y < out[i].y + out[i].h) {
            out[i].key = hash_int(out[i].key, character);
        }
    }
    return count;
}
