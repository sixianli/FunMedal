#include "screen.h"

#include <stdio.h>
#include <string.h>

#define HOUR_MINUTES 60
#define DAY_MINUTES (24 * HOUR_MINUTES)

static const theme_t *const THEMES[SCREEN_THEME_COUNT] = {&theme_board};

const theme_t *screen_theme(uint8_t index)
{
    return THEMES[index < SCREEN_THEME_COUNT ? index : 0];
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

void screen_render(const theme_t *theme, const screen_t *screen, const band_t *band)
{
    theme->draw_background(band);
    theme->draw_header(band, screen, theme->header);
    if (screen->mode == SCREEN_ROWS) {
        for (int i = 0; i < screen->shown && i < THEME_SLOTS; i++) {
            const row_t *row = &screen->rows[i];
            theme->draw_slot(band, row, theme->slots[i], theme->slot_phase(row->state, screen->tick));
        }
    } else {
        theme->draw_message(band, screen->mode, theme->body);
    }
    theme->draw_footer(band, screen, theme->footer);
}

static uint32_t hash_int(uint32_t hash, uint32_t value)
{
    return dirty_hash(hash, &value, sizeof value);
}

static region_t region(rect_t area, uint32_t key)
{
    return (region_t){(int16_t)area.y, (int16_t)area.h, key};
}

static uint32_t slot_key(uint32_t base, const theme_t *theme, const screen_t *screen, int i)
{
    uint32_t key = dirty_hash_text(hash_int(base, (uint32_t)i), "slot");
    if (i >= screen->shown) {
        return dirty_hash_text(key, "empty");
    }
    const row_t *row = &screen->rows[i];
    char age[SCREEN_AGE_MAX] = "";
    if (row->state == ROW_DONE && row->minutes != ROWS_HIDDEN_MINUTES) {
        screen_age(row->minutes, age, sizeof age);
    }
    key = hash_int(key, row->state);
    key = dirty_hash_text(key, row->name);
    key = dirty_hash_text(key, age);
    return hash_int(key, theme->slot_phase(row->state, screen->tick));
}

int screen_regions(uint8_t theme_index, const screen_t *screen, region_t *out)
{
    const theme_t *theme = screen_theme(theme_index);
    uint32_t base = hash_int(DIRTY_HASH_START, theme_index < SCREEN_THEME_COUNT ? theme_index : 0);
    int count = 0;
    uint32_t header = dirty_hash_text(base, "header");
    header = hash_int(hash_int(hash_int(header, screen->mode), screen->total), screen->shown);
    out[count++] = region(theme->header, header);
    if (screen->mode == SCREEN_ROWS) {
        for (int i = 0; i < THEME_SLOTS; i++) {
            out[count++] = region(theme->slots[i], slot_key(base, theme, screen, i));
        }
    } else {
        out[count++] = region(theme->body, hash_int(dirty_hash_text(base, "body"), screen->mode));
    }
    out[count++] = region(theme->footer, dirty_hash_text(base, "footer"));
    return count;
}
