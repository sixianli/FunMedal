#include <stdio.h>

#include "screen.h"

#define COMIC_PAPER 0xffffff
#define COMIC_INK 0x111111
#define COMIC_WAITING 0xfff176
#define COMIC_RUNNING 0xb3e5fc
#define COMIC_ERROR 0xff8a80
#define COMIC_DONE 0xc8e6c9
#define COMIC_SHOUT 0xff3d3d
#define COMIC_WHITE 0xffffff
#define COMIC_HOME 0xfffbe6
#define MARGIN 8
#define STRIP_H 21
#define TITLE_TOP (-2)
#define BORDER 3
#define DOT_PERIOD 6
#define DOT_ALPHA 10
#define NAME_INSET 3
#define NAME_BORDER 2
#define NAME_PAD 4
#define SFX_TOP 36
#define DONE_SFX_TOP 26
#define DONE_AGE_TOP 58
#define SHADOW 2
#define EMPTY_DASH 6
#define BOUNCE_TICKS 8
#define SHAKE_TICKS 4
#define DRIFT_TICKS 4
#define DRIFT_STEPS 4
#define FOOTER_TOP 10
#define MESSAGE_TITLE_TOP 76
#define MESSAGE_HINT_TOP 128

static const char *const TITLE = "Claude 日常";
static const char *const FOOTER = "未完待续";
static const char *const SOUNDS[] = {
    [ROW_WAITING] = "！！",
    [ROW_ERROR] = "啊！",
    [ROW_RUNNING] = "哒哒哒",
    [ROW_DONE] = "呼～",
};
static const int SHAKE[SHAKE_TICKS] = {0, -2, 0, 2};

static uint32_t slot_phase(row_state_t state, uint32_t tick)
{
    switch (state) {
    case ROW_WAITING: {
        uint32_t t = tick % BOUNCE_TICKS;
        return t <= BOUNCE_TICKS / 2 ? t : BOUNCE_TICKS - t;
    }
    case ROW_ERROR:
    case ROW_RUNNING:
        return tick % SHAKE_TICKS;
    default:
        return tick / DRIFT_TICKS % DRIFT_STEPS;
    }
}

static uint32_t footer_phase(uint32_t tick)
{
    (void)tick;
    return 0;
}

static void draw_background(const band_t *band)
{
    draw_fill(band, (rect_t){0, band->y0, DRAW_W, band->rows}, COMIC_PAPER);
}

static void draw_header(const band_t *band, const screen_t *screen, rect_t area)
{
    draw_fill(band, (rect_t){area.x, area.y, area.w, STRIP_H}, COMIC_INK);
    draw_text(band, &font_text, TITLE, area.x + MARGIN, area.y + TITLE_TOP, area.x + area.w - MARGIN, COMIC_WHITE);
    if (screen->mode != SCREEN_ROWS || screen->total <= screen->shown) {
        return;
    }
    char more[8];
    snprintf(more, sizeof more, "+%u", (unsigned)(screen->total - screen->shown));
    int width = draw_text_width(&font_text, more, area.w);
    draw_text(band, &font_text, more, area.x + area.w - MARGIN - width, area.y + TITLE_TOP, area.x + area.w,
              COMIC_WHITE);
}

static uint32_t panel_fill(row_state_t state)
{
    switch (state) {
    case ROW_WAITING:
        return COMIC_WAITING;
    case ROW_ERROR:
        return COMIC_ERROR;
    case ROW_RUNNING:
        return COMIC_RUNNING;
    default:
        return COMIC_DONE;
    }
}

static void draw_inked(const band_t *band, const font_t *font, const char *text, int x, int top, uint32_t rgb)
{
    int right = x + draw_text_width(font, text, DRAW_W) + SHADOW + 1;
    draw_text(band, font, text, x + SHADOW, top + SHADOW, right + SHADOW, COMIC_INK);
    draw_text(band, font, text, x - 1, top, right, COMIC_INK);
    draw_text(band, font, text, x + 1, top, right, COMIC_INK);
    draw_text(band, font, text, x, top - 1, right, COMIC_INK);
    draw_text(band, font, text, x, top + 1, right, COMIC_INK);
    draw_text(band, font, text, x, top, right, rgb);
}

static void draw_name(const band_t *band, const char *name, rect_t inner)
{
    int max_w = inner.w - 2 * NAME_INSET - 2 * NAME_BORDER - 2 * NAME_PAD;
    int width = draw_text_width(&font_pixel, name, max_w);
    rect_t box = {inner.x + NAME_INSET, inner.y + NAME_INSET, width + 2 * (NAME_BORDER + NAME_PAD),
                  font_pixel.line_height + 2 * NAME_BORDER};
    draw_panel(band, box, 0, NAME_BORDER, COMIC_INK, COMIC_PAPER);
    int x = box.x + NAME_BORDER + NAME_PAD;
    draw_text(band, &font_pixel, name, x, box.y + NAME_BORDER, x + width, COMIC_INK);
}

static void draw_slot(const band_t *band, const row_t *row, rect_t area, uint32_t phase)
{
    if (band->y0 >= area.y + area.h || band->y0 + band->rows <= area.y) {
        return;
    }
    rect_t inner = {area.x + BORDER, area.y + BORDER, area.w - 2 * BORDER, area.h - 2 * BORDER};
    draw_fill(band, area, COMIC_INK);
    draw_fill(band, inner, panel_fill(row->state));
    draw_dots(band, inner, DOT_PERIOD, COMIC_INK, DOT_ALPHA);
    draw_name(band, row->name, inner);
    const char *sound = SOUNDS[row->state];
    int dx = 0;
    int top = area.y + SFX_TOP;
    if (row->state == ROW_WAITING) {
        top -= (int)phase * 3 / 2;
    } else if (row->state == ROW_DONE) {
        top = area.y + DONE_SFX_TOP - (int)phase;
    } else {
        dx = SHAKE[phase];
    }
    int width = draw_text_width(&font_large, sound, inner.w);
    uint32_t rgb = row->state == ROW_WAITING ? COMIC_SHOUT : COMIC_WHITE;
    draw_inked(band, &font_large, sound, area.x + (area.w - width) / 2 + dx, top, rgb);
    if (row->state == ROW_DONE && row->minutes != ROWS_HIDDEN_MINUTES) {
        char age[SCREEN_AGE_MAX];
        screen_age(row->minutes, age, sizeof age);
        int age_w = draw_text_width(&font_pixel, age, inner.w);
        int x = area.x + (area.w - age_w) / 2;
        draw_text(band, &font_pixel, age, x, area.y + DONE_AGE_TOP, x + age_w, COMIC_INK);
    }
}

static void draw_empty(const band_t *band, rect_t area)
{
    draw_dashed(band, area, BORDER, EMPTY_DASH, COMIC_INK);
}

static void draw_home(const band_t *band, rect_t area)
{
    draw_fill(band, area, COMIC_INK);
    draw_fill(band, (rect_t){area.x + BORDER, area.y + BORDER, area.w - 2 * BORDER, area.h - 2 * BORDER}, COMIC_HOME);
}

static void draw_centered(const band_t *band, const font_t *font, const char *text, rect_t area, int top)
{
    int width = draw_text_width(font, text, area.w - 2 * MARGIN);
    int x = area.x + (area.w - width) / 2;
    draw_text(band, font, text, x, top, x + width, COMIC_INK);
}

static void draw_message(const band_t *band, screen_mode_t mode, rect_t area)
{
    rect_t frame = {THEME_GRID_LEFT, area.y, DRAW_W - 2 * THEME_GRID_LEFT, area.h - THEME_GRID_GAP};
    rect_t inner = {frame.x + BORDER, frame.y + BORDER, frame.w - 2 * BORDER, frame.h - 2 * BORDER};
    draw_fill(band, frame, COMIC_INK);
    draw_fill(band, inner, COMIC_PAPER);
    draw_dots(band, inner, DOT_PERIOD, COMIC_INK, DOT_ALPHA);
    bool empty = mode == SCREEN_EMPTY;
    draw_centered(band, &font_large, empty ? "没有会话" : "未连接", area, area.y + MESSAGE_TITLE_TOP);
    draw_centered(band, &font_text, empty ? "开一个会话就会出现在这里" : "等 Mac 上的桥接程序连上", area,
                  area.y + MESSAGE_HINT_TOP);
}

static void draw_footer(const band_t *band, const screen_t *screen, rect_t area)
{
    (void)screen;
    int width = draw_text_width(&font_text, FOOTER, area.w);
    int x = area.x + area.w - MARGIN - width;
    draw_text(band, &font_text, FOOTER, x, area.y + FOOTER_TOP, x + width, COMIC_INK);
}

const theme_t theme_comic = {
    .header = {0, 0, DRAW_W, THEME_HEADER_H},
    .slots = {THEME_GRID_SLOT(0), THEME_GRID_SLOT(1), THEME_GRID_SLOT(2), THEME_GRID_SLOT(3), THEME_GRID_SLOT(4),
              THEME_GRID_SLOT(5)},
    .body = {0, THEME_GRID_TOP, DRAW_W, THEME_GRID_FOOTER - THEME_GRID_TOP},
    .footer = {0, THEME_GRID_FOOTER, DRAW_W, THEME_GRID_FOOTER_H},
    .home = {{169, 199, 64, 74}, {123, 199, 108, 74}, {124, 212, 46, 40}},
    .message = {{167, 192, 64, 74}, {6, 27, 228, 240}, {120, 205, 46, 40}},
    .character = {COMIC_INK, COMIC_PAPER},
    .draw_background = draw_background,
    .draw_header = draw_header,
    .draw_slot = draw_slot,
    .draw_empty = draw_empty,
    .draw_home = draw_home,
    .draw_message = draw_message,
    .draw_footer = draw_footer,
    .slot_phase = slot_phase,
    .footer_phase = footer_phase,
};
