#include <stdio.h>

#include "screen.h"

#define BOARD_BACKGROUND 0x1e8a7a
#define BOARD_INK 0x1b1b1b
#define BOARD_WHITE 0xffffff
#define BOARD_WAITING 0xffd23f
#define BOARD_WAITING_GLOW 0xfff3b0
#define BOARD_ERROR 0xff6b6b
#define BOARD_RUNNING 0x7cc6ff
#define BOARD_DONE 0xc4c9cf
#define HEADER_H 24
#define SLOT_TOP 24
#define SLOT_H 36
#define FOOTER_TOP 240
#define FOOTER_H 80
#define MARGIN 8
#define BAR_X 6
#define BAR_INSET 3
#define BAR_W 228
#define BAR_H 28
#define BAR_RADIUS 10
#define BAR_BORDER 2
#define SHADOW 3
#define PILL_H 18
#define PILL_PAD 6
#define STRIPE_PERIOD 16
#define STRIPE_STEPS 8
#define BREATH_STEPS 10
#define MESSAGE_TITLE_TOP 64
#define MESSAGE_HINT_TOP 116

static const char *const TITLE = "今天的任务";
static const char *const STATE_WORDS[] = {
    [ROW_WAITING] = "等你",
    [ROW_ERROR] = "出错",
    [ROW_RUNNING] = "在跑",
    [ROW_DONE] = "答完了",
};

static uint32_t breath_level(uint32_t phase)
{
    return phase <= BREATH_STEPS / 2 ? phase : BREATH_STEPS - phase;
}

static uint32_t slot_phase(row_state_t state, uint32_t tick)
{
    if (state == ROW_RUNNING) {
        return tick % STRIPE_STEPS;
    }
    if (state == ROW_WAITING) {
        return breath_level(tick % BREATH_STEPS);
    }
    return 0;
}

static void draw_background(const band_t *band)
{
    draw_fill(band, (rect_t){0, band->y0, DRAW_W, band->rows}, BOARD_BACKGROUND);
}

static void draw_header(const band_t *band, const screen_t *screen, rect_t area)
{
    draw_text(band, &font_text, TITLE, area.x + MARGIN, area.y, area.x + area.w - MARGIN, BOARD_WHITE);
    if (screen->mode != SCREEN_ROWS || screen->total <= screen->shown) {
        return;
    }
    char more[8];
    snprintf(more, sizeof more, "+%u", (unsigned)(screen->total - screen->shown));
    int width = draw_text_width(&font_text, more, area.w);
    draw_text(band, &font_text, more, area.x + area.w - MARGIN - width, area.y, area.x + area.w, BOARD_WHITE);
}

static uint32_t bar_fill(row_state_t state, uint32_t phase)
{
    switch (state) {
    case ROW_WAITING:
        return draw_blend(BOARD_WAITING, BOARD_WAITING_GLOW, (uint8_t)(phase * 15 / (BREATH_STEPS / 2)));
    case ROW_ERROR:
        return BOARD_ERROR;
    case ROW_RUNNING:
        return BOARD_RUNNING;
    default:
        return BOARD_DONE;
    }
}

static void draw_pill(const band_t *band, const row_t *row, rect_t bar, int *left)
{
    char age[SCREEN_AGE_MAX];
    const char *word = row->state == ROW_DONE && row->minutes != ROWS_HIDDEN_MINUTES
                           ? screen_age(row->minutes, age, sizeof age)
                           : STATE_WORDS[row->state];
    bool dark = row->state == ROW_WAITING || row->state == ROW_ERROR;
    uint32_t text_rgb = row->state == ROW_WAITING ? BOARD_WAITING : row->state == ROW_ERROR ? BOARD_ERROR : BOARD_INK;
    int width = draw_text_width(&font_pixel, word, bar.w / 2) + 2 * PILL_PAD;
    rect_t pill = {bar.x + bar.w - BAR_BORDER - 3 - width, bar.y + (bar.h - PILL_H) / 2, width, PILL_H};
    draw_panel(band, pill, PILL_H / 2, BAR_BORDER, BOARD_INK, dark ? BOARD_INK : BOARD_WHITE);
    int top = pill.y + (PILL_H - font_pixel.line_height) / 2;
    draw_text(band, &font_pixel, word, pill.x + PILL_PAD, top, pill.x + pill.w, text_rgb);
    *left = pill.x;
}

static void draw_slot(const band_t *band, const row_t *row, rect_t area, uint32_t phase)
{
    rect_t bar = {BAR_X, area.y + BAR_INSET, BAR_W, BAR_H};
    if (band->y0 >= area.y + area.h || band->y0 + band->rows <= area.y) {
        return;
    }
    draw_round(band, (rect_t){bar.x, bar.y + SHADOW, bar.w, bar.h}, BAR_RADIUS, BOARD_INK);
    draw_panel(band, bar, BAR_RADIUS, BAR_BORDER, BOARD_INK, bar_fill(row->state, phase));
    if (row->state == ROW_RUNNING) {
        rect_t inner = {bar.x + BAR_BORDER, bar.y + BAR_BORDER, bar.w - 2 * BAR_BORDER, bar.h - 2 * BAR_BORDER};
        int offset = (int)phase * (STRIPE_PERIOD / STRIPE_STEPS);
        draw_stripes(band, inner, BAR_RADIUS - BAR_BORDER, STRIPE_PERIOD, offset, BOARD_WHITE, 6);
    }
    int pill_left;
    draw_pill(band, row, bar, &pill_left);
    int top = bar.y + (bar.h - font_text.line_height) / 2;
    draw_text(band, &font_text, row->name, bar.x + MARGIN + 2, top, pill_left - PILL_PAD, BOARD_INK);
}

static void draw_centered(const band_t *band, const font_t *font, const char *text, rect_t area, int top)
{
    int width = draw_text_width(font, text, area.w - 2 * MARGIN);
    int x = area.x + (area.w - width) / 2;
    draw_text(band, font, text, x, top, x + width, BOARD_WHITE);
}

static void draw_message(const band_t *band, screen_mode_t mode, rect_t area)
{
    bool empty = mode == SCREEN_EMPTY;
    draw_centered(band, &font_large, empty ? "没有会话" : "未连接", area, area.y + MESSAGE_TITLE_TOP);
    draw_centered(band, &font_text, empty ? "开一个会话就会出现在这里" : "等 Mac 上的桥接程序连上", area,
                  area.y + MESSAGE_HINT_TOP);
}

static void draw_empty(const band_t *band, rect_t area)
{
    (void)band;
    (void)area;
}

static void draw_footer(const band_t *band, const screen_t *screen, rect_t area)
{
    (void)band;
    (void)screen;
    (void)area;
}

static uint32_t footer_phase(uint32_t tick)
{
    (void)tick;
    return 0;
}

const theme_t theme_board = {
    .header = {0, 0, DRAW_W, HEADER_H},
    .slots = {
        {0, SLOT_TOP + 0 * SLOT_H, DRAW_W, SLOT_H},
        {0, SLOT_TOP + 1 * SLOT_H, DRAW_W, SLOT_H},
        {0, SLOT_TOP + 2 * SLOT_H, DRAW_W, SLOT_H},
        {0, SLOT_TOP + 3 * SLOT_H, DRAW_W, SLOT_H},
        {0, SLOT_TOP + 4 * SLOT_H, DRAW_W, SLOT_H},
        {0, SLOT_TOP + 5 * SLOT_H, DRAW_W, SLOT_H},
    },
    .body = {0, HEADER_H, DRAW_W, FOOTER_TOP - HEADER_H},
    .footer = {0, FOOTER_TOP, DRAW_W, FOOTER_H},
    .home = {{168, 243, 64, 74}, {0, 240, 240, 80}, {120, 246, 46, 44}},
    .message = {{168, 243, 64, 74}, {0, 240, 240, 80}, {120, 246, 46, 44}},
    .character = {BOARD_INK, BOARD_WHITE},
    .draw_background = draw_background,
    .draw_header = draw_header,
    .draw_slot = draw_slot,
    .draw_empty = draw_empty,
    .draw_message = draw_message,
    .draw_footer = draw_footer,
    .slot_phase = slot_phase,
    .footer_phase = footer_phase,
};
