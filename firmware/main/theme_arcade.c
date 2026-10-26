#include <stdio.h>

#include "screen.h"

#define ARCADE_BACKGROUND 0x0b0f0a
#define ARCADE_GREEN 0x7dff7d
#define ARCADE_YELLOW 0xffe95c
#define ARCADE_RED 0xff5c5c
#define ARCADE_DONE 0x3f9f4f
#define ARCADE_RULE 0x2d5a2d
#define ARCADE_SCANLINE 0x000000
#define HEADER_H 24
#define SLOT_TOP 24
#define SLOT_H 36
#define FOOTER_TOP 240
#define FOOTER_H 80
#define MARGIN 8
#define TEXT_TOP 3
#define RULE_Y 21
#define RULE_H 2
#define RULE_DASH 4
#define BAR_X 6
#define BAR_W 4
#define BAR_INSET 4
#define NAME_X 16
#define RIGHT_EDGE 232
#define DOT_SIZE 3
#define DOT_GAP 3
#define DOT_LIFT 3
#define DOTS_W (3 * DOT_SIZE + 2 * DOT_GAP)
#define DOTS_GAP 4
#define BLINK_TICKS 5
#define DOT_TICKS 2
#define SHAKE_TICKS 4
#define COIN_TICKS 6
#define COIN_BOTTOM 10
#define DIM_ALPHA 4
#define SCANLINE_PERIOD 3
#define SCANLINE_ALPHA 7
#define MESSAGE_TITLE_TOP 64
#define HINT_FONT font_pixel
#define MESSAGE_HINT_TOP 116
#define BADGE_SIZE 24
#define ARCADE_WHITE 0xffffff
#define ALERT_DIM 9
#define FRAME_OUTER 2
#define FRAME_GAP 2
#define FRAME_INNER 1
#define ALERT_TEXT_X 98
#define ALERT_NAME_TOP 30
#define ALERT_LINE_TOP 50
#define TAG_PAD 4
#define TAG_LIFT 8
#define TAG_RIGHT 10
#define CHEER_NAME_TOP 10
#define CHEER_LINE_TOP 28
#define CHEER_TEXT_RIGHT 160
#define HELLO_TOP 196

static const char *const TITLE = "CLAUDE CODE";
static const char *const COIN = "INSERT COIN";
static const char *const STATE_WORDS[] = {
    [ROW_WAITING] = "等你！",
    [ROW_ERROR] = "出错",
    [ROW_RUNNING] = "在跑",
    [ROW_DONE] = "答完了",
};
static const int SHAKE[SHAKE_TICKS] = {0, -2, 0, 2};

static uint32_t slot_phase(row_state_t state, uint32_t tick)
{
    if (state == ROW_WAITING) {
        return tick / BLINK_TICKS % 2;
    }
    if (state == ROW_RUNNING) {
        return tick / DOT_TICKS % 3;
    }
    if (state == ROW_ERROR) {
        return tick % SHAKE_TICKS;
    }
    return 0;
}

static uint32_t footer_phase(uint32_t tick)
{
    return tick / COIN_TICKS % 2;
}

static void draw_background(const band_t *band)
{
    draw_fill(band, (rect_t){0, band->y0, DRAW_W, band->rows}, ARCADE_BACKGROUND);
}

static void scanlines(const band_t *band, rect_t area)
{
    draw_scanlines(band, area, SCANLINE_PERIOD, ARCADE_SCANLINE, SCANLINE_ALPHA);
}

static void draw_header(const band_t *band, const screen_t *screen, rect_t area)
{
    draw_text(band, &font_pixel, TITLE, area.x + MARGIN, area.y + TEXT_TOP, area.x + area.w - MARGIN, ARCADE_YELLOW);
    if (screen->mode == SCREEN_ROWS && screen->total > screen->shown) {
        char more[8];
        snprintf(more, sizeof more, "+%u", (unsigned)(screen->total - screen->shown));
        int width = draw_text_width(&font_pixel, more, area.w);
        draw_text(band, &font_pixel, more, area.x + area.w - MARGIN - width, area.y + TEXT_TOP, area.x + area.w,
                  ARCADE_YELLOW);
    }
    for (int x = area.x; x < area.x + area.w; x += 2 * RULE_DASH) {
        draw_fill(band, (rect_t){x, area.y + RULE_Y, RULE_DASH, RULE_H}, ARCADE_RULE);
    }
    scanlines(band, area);
}

static uint32_t state_colour(row_state_t state)
{
    switch (state) {
    case ROW_WAITING:
        return ARCADE_YELLOW;
    case ROW_ERROR:
        return ARCADE_RED;
    case ROW_RUNNING:
        return ARCADE_GREEN;
    default:
        return ARCADE_DONE;
    }
}

static void upper_name(const char *name, char *out, size_t size)
{
    size_t i = 0;
    for (; name[i] != '\0' && i + 1 < size; i++) {
        char c = name[i];
        out[i] = c >= 'a' && c <= 'z' ? (char)(c - 'a' + 'A') : c;
    }
    out[i] = '\0';
}

static void draw_dots_bounce(const band_t *band, int x, int y, uint32_t phase, uint32_t rgb)
{
    for (uint32_t i = 0; i < 3; i++) {
        int lift = i == phase ? DOT_LIFT : 0;
        draw_fill(band, (rect_t){x + (int)i * (DOT_SIZE + DOT_GAP), y - lift, DOT_SIZE, DOT_SIZE}, rgb);
    }
}

static void draw_slot(const band_t *band, const row_t *row, rect_t area, uint32_t phase)
{
    if (band->y0 >= area.y + area.h || band->y0 + band->rows <= area.y) {
        return;
    }
    uint32_t rgb = state_colour(row->state);
    if (row->state == ROW_WAITING && phase == 1) {
        rgb = draw_blend(ARCADE_BACKGROUND, rgb, DIM_ALPHA);
    }
    int dx = row->state == ROW_ERROR ? SHAKE[phase] : 0;
    draw_fill(band, (rect_t){BAR_X + dx, area.y + BAR_INSET, BAR_W, area.h - 2 * BAR_INSET}, rgb);
    char age[SCREEN_AGE_MAX];
    char word[SCREEN_AGE_MAX + 16];
    if (row->state == ROW_DONE && row->minutes != ROWS_HIDDEN_MINUTES) {
        snprintf(word, sizeof word, "%s %s", STATE_WORDS[ROW_DONE], screen_age(row->minutes, age, sizeof age));
    } else {
        snprintf(word, sizeof word, "%s", STATE_WORDS[row->state]);
    }
    int top = area.y + (area.h - font_pixel.line_height) / 2;
    int right = (row->state == ROW_RUNNING ? RIGHT_EDGE - DOTS_W - DOTS_GAP : RIGHT_EDGE) + dx;
    int width = draw_text_width(&font_pixel, word, area.w / 2);
    draw_text(band, &font_pixel, word, right - width, top, right, rgb);
    if (row->state == ROW_RUNNING) {
        int dots_y = top + font_pixel.line_height / 2;
        draw_dots_bounce(band, RIGHT_EDGE - DOTS_W, dots_y, phase, rgb);
    }
    char name[ROWS_NAME_MAX + 1];
    upper_name(row->name, name, sizeof name);
    int name_x = NAME_X + dx;
    if (row->project) {
        name_x = draw_badge(band, NAME_X + dx - 2, top + font_pixel.line_height / 2, BADGE_SIZE);
    }
    draw_text(band, &font_pixel, name, name_x, top, right - width - MARGIN, rgb);
    scanlines(band, area);
}

static void draw_empty(const band_t *band, rect_t area)
{
    scanlines(band, area);
}

static void draw_centered(const band_t *band, const font_t *font, const char *text, rect_t area, int top,
                          uint32_t rgb)
{
    int width = draw_text_width(font, text, area.w - 2 * MARGIN);
    int x = area.x + (area.w - width) / 2;
    draw_text(band, font, text, x, top, x + width, rgb);
}

static void draw_message(const band_t *band, screen_mode_t mode, rect_t area)
{
    bool empty = mode == SCREEN_EMPTY;
    draw_centered(band, &font_large, empty ? "没有会话" : "未连接", area, area.y + MESSAGE_TITLE_TOP, ARCADE_YELLOW);
    draw_centered(band, &HINT_FONT, empty ? "开一个会话就会出现在这里" : "等 Mac 上的桥接程序连上", area,
                  area.y + MESSAGE_HINT_TOP, ARCADE_GREEN);
    scanlines(band, area);
}

static void draw_footer(const band_t *band, const screen_t *screen, rect_t area)
{
    uint32_t rgb = footer_phase(screen->tick) == 0 ? ARCADE_YELLOW : draw_blend(ARCADE_BACKGROUND, ARCADE_YELLOW, DIM_ALPHA);
    int top = area.y + area.h - COIN_BOTTOM - font_pixel.line_height;
    draw_text(band, &font_pixel, COIN, area.x + MARGIN, top, area.x + area.w / 2, rgb);
    scanlines(band, area);
}

static rect_t inset(rect_t area, int by)
{
    return (rect_t){area.x + by, area.y + by, area.w - 2 * by, area.h - 2 * by};
}

static void draw_frame(const band_t *band, rect_t area, uint32_t rgb)
{
    draw_fill(band, area, rgb);
    draw_fill(band, inset(area, FRAME_OUTER), ARCADE_BACKGROUND);
    draw_fill(band, inset(area, FRAME_OUTER + FRAME_GAP), rgb);
    draw_fill(band, inset(area, FRAME_OUTER + FRAME_GAP + FRAME_INNER), ARCADE_BACKGROUND);
}

static void draw_alert(const band_t *band, event_kind_t kind, const char *name, rect_t area)
{
    uint32_t rgb = kind == EVENT_ERROR ? ARCADE_RED : ARCADE_YELLOW;
    draw_tint(band, (rect_t){0, band->y0, DRAW_W, band->rows}, 0, ARCADE_BACKGROUND, ALERT_DIM);
    draw_frame(band, area, rgb);
    const char *tag = "SPEAKER";
    int width = draw_text_width(&font_pixel, tag, area.w) + 2 * TAG_PAD;
    rect_t pill = {area.x + area.w - TAG_RIGHT - width, area.y - TAG_LIFT, width, font_pixel.line_height};
    draw_fill(band, pill, rgb);
    draw_text(band, &font_pixel, tag, pill.x + TAG_PAD, pill.y, pill.x + pill.w, ARCADE_BACKGROUND);
    char upper[ROWS_NAME_MAX + 1];
    upper_name(name, upper, sizeof upper);
    int x = area.x + ALERT_TEXT_X;
    int right = area.x + area.w - MARGIN;
    screen_named(band, &font_pixel, upper, kind == EVENT_ERROR ? " > ERROR" : " > WAITING", x,
                 area.y + ALERT_NAME_TOP, right, rgb);
    for (int line = 0; line < 2; line++) {
        screen_bold(band, &font_text, screen_alert_line(kind, line), x,
                    area.y + ALERT_LINE_TOP + line * font_text.line_height, right, ARCADE_WHITE);
    }
    scanlines(band, area);
}

static void draw_cheer(const band_t *band, const char *name, rect_t area)
{
    draw_frame(band, area, ARCADE_GREEN);
    char upper[ROWS_NAME_MAX + 1];
    upper_name(name, upper, sizeof upper);
    int x = area.x + MARGIN + 4;
    draw_text(band, &font_pixel, upper, x, area.y + CHEER_NAME_TOP, CHEER_TEXT_RIGHT, ARCADE_GREEN);
    screen_bold(band, &font_text, "答完了！", x, area.y + CHEER_LINE_TOP, CHEER_TEXT_RIGHT, ARCADE_WHITE);
    scanlines(band, area);
}

static void draw_hello(const band_t *band, rect_t area)
{
    draw_centered(band, &font_large, "连上了", area, HELLO_TOP, ARCADE_YELLOW);
    scanlines(band, area);
}

const theme_t theme_arcade = {
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
    .home = {{168, 243, 64, 74}, {0, 240, 240, 80}, {120, 246, 46, 44}, CHARACTER_SMALL},
    .message = {{168, 243, 64, 74}, {0, 240, 240, 80}, {120, 246, 46, 44}, CHARACTER_SMALL},
    .hint_font = &HINT_FONT,
    .hint_top = MESSAGE_HINT_TOP,
    .character = {ARCADE_YELLOW, ARCADE_BACKGROUND},
    .draw_background = draw_background,
    .draw_header = draw_header,
    .draw_slot = draw_slot,
    .draw_empty = draw_empty,
    .draw_message = draw_message,
    .draw_footer = draw_footer,
    .slot_phase = slot_phase,
    .footer_phase = footer_phase,
    .alert = {{6, 96, 228, 128}, {{14, 112, 84, 108}, {12, 102, 216, 116}, {0, 0, 0, 0}, CHARACTER_LARGE}},
    .cheer = {6, 248, 228, 64},
    .hello = {{57, 40, 125, 145}, {0, 24, 240, 216}, {0, 0, 0, 0}, CHARACTER_HOME_145},
    .draw_alert = draw_alert,
    .draw_cheer = draw_cheer,
    .draw_hello = draw_hello,
};
