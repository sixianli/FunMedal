#include <stdio.h>

#include "screen.h"

#define TOWER_BACKGROUND 0x061423
#define TOWER_MODULE 0x0a1f36
#define TOWER_LINE 0x1c3a5c
#define TOWER_TITLE 0x6fb7ff
#define TOWER_NAME 0xcfe8ff
#define TOWER_FAINT 0x2b4f75
#define TOWER_RADAR 0x3fa0ff
#define TOWER_AMBER 0xffb020
#define TOWER_RED 0xff3b3b
#define TOWER_RED_TEXT 0xff6b6b
#define TOWER_GREEN 0x2ad67a
#define MARGIN 8
#define TEXT_TOP 3
#define RULE_Y 23
#define MODULE_RADIUS 6
#define NAME_TOP 3
#define LAMP 36
#define LAMP_TOP 21
#define LAMP_RING 2
#define GLOW 4
#define STATE_TOP 60
#define RADAR_TRAIL 4
#define RADAR_FILL_ALPHA 3
#define BREATH_TICKS 14
#define BREATH_BASE 9
#define BLINK_NUM 2
#define BLINK_DEN 5
#define OFF_ALPHA 4
#define EMPTY_DASH 4
#define FOOTER_RULE 5
#define FOOTER_TEXT_TOP 16
#define MESSAGE_TITLE_TOP 76
#define HINT_FONT font_pixel
#define MESSAGE_HINT_TOP 128
#define BADGE_SIZE 22
#define BADGE_CENTRE_Y 13

static const char *const TITLE = "CLAUDE · CONTROL";
static const char *const FOOTER = "CONTROL · ONLINE";
static const char *const EMPTY = "— 空 —";
static const char *const HOME = "CREW";
static const char *const STATE_WORDS[] = {
    [ROW_WAITING] = "等你",
    [ROW_ERROR] = "出错",
    [ROW_RUNNING] = "在跑",
    [ROW_DONE] = "答完了",
};
static const uint8_t TRAIL_ALPHA[RADAR_TRAIL] = {13, 9, 5, 2};

static uint32_t slot_phase(row_state_t state, uint32_t tick)
{
    switch (state) {
    case ROW_RUNNING:
        return tick % DRAW_WEDGE_STEPS;
    case ROW_WAITING: {
        uint32_t t = tick % BREATH_TICKS;
        return t <= BREATH_TICKS / 2 ? t : BREATH_TICKS - t;
    }
    case ROW_ERROR:
        return tick * BLINK_NUM / BLINK_DEN % 2;
    default:
        return 0;
    }
}

static uint32_t footer_phase(uint32_t tick)
{
    (void)tick;
    return 0;
}

static void draw_background(const band_t *band)
{
    draw_fill(band, (rect_t){0, band->y0, DRAW_W, band->rows}, TOWER_BACKGROUND);
}

static void draw_header(const band_t *band, const screen_t *screen, rect_t area)
{
    draw_text(band, &font_pixel, TITLE, area.x + MARGIN, area.y + TEXT_TOP, area.x + area.w - MARGIN, TOWER_TITLE);
    draw_fill(band, (rect_t){area.x, area.y + RULE_Y, area.w, 1}, TOWER_LINE);
    if (screen->mode != SCREEN_ROWS || screen->total <= screen->shown) {
        return;
    }
    char more[8];
    snprintf(more, sizeof more, "+%u", (unsigned)(screen->total - screen->shown));
    int width = draw_text_width(&font_pixel, more, area.w);
    draw_text(band, &font_pixel, more, area.x + area.w - MARGIN - width, area.y + TEXT_TOP, area.x + area.w,
              TOWER_TITLE);
}

static uint32_t lamp_colour(row_state_t state)
{
    switch (state) {
    case ROW_WAITING:
        return TOWER_AMBER;
    case ROW_ERROR:
        return TOWER_RED;
    case ROW_RUNNING:
        return TOWER_RADAR;
    default:
        return TOWER_GREEN;
    }
}

static void draw_radar(const band_t *band, rect_t lamp, uint32_t phase)
{
    draw_round(band, lamp, LAMP / 2, TOWER_RADAR);
    rect_t inner = {lamp.x + LAMP_RING, lamp.y + LAMP_RING, lamp.w - 2 * LAMP_RING, lamp.h - 2 * LAMP_RING};
    draw_round(band, inner, inner.w / 2, TOWER_MODULE);
    draw_tint(band, inner, inner.w / 2, TOWER_RADAR, RADAR_FILL_ALPHA);
    for (int k = 0; k < RADAR_TRAIL; k++) {
        draw_wedge(band, inner, (int)phase - k, TOWER_RADAR, TRAIL_ALPHA[k]);
    }
}

static void draw_glowing(const band_t *band, rect_t lamp, uint32_t rgb, uint8_t lamp_alpha, uint8_t glow_alpha)
{
    rect_t glow = {lamp.x - GLOW, lamp.y - GLOW, lamp.w + 2 * GLOW, lamp.h + 2 * GLOW};
    if (glow_alpha) {
        draw_tint(band, glow, glow.w / 2, rgb, glow_alpha);
    }
    draw_round(band, lamp, LAMP / 2, draw_blend(TOWER_MODULE, rgb, lamp_alpha));
}

static void draw_lamp(const band_t *band, row_state_t state, rect_t lamp, uint32_t phase)
{
    switch (state) {
    case ROW_RUNNING:
        draw_radar(band, lamp, phase);
        break;
    case ROW_WAITING:
        draw_glowing(band, lamp, TOWER_AMBER, (uint8_t)(BREATH_BASE + phase * (15 - BREATH_BASE) / (BREATH_TICKS / 2)), (uint8_t)(1 + phase / 2));
        break;
    case ROW_ERROR:
        if (phase == 0) {
            draw_glowing(band, lamp, TOWER_RED, 15, 4);
        } else {
            draw_glowing(band, lamp, TOWER_RED, OFF_ALPHA, 0);
        }
        break;
    default:
        draw_glowing(band, lamp, TOWER_GREEN, 15, 3);
        break;
    }
}

static void draw_centered(const band_t *band, const font_t *font, const char *text, rect_t area, int top,
                          uint32_t rgb)
{
    int width = draw_text_width(font, text, area.w - 2 * MARGIN);
    int x = area.x + (area.w - width) / 2;
    draw_text(band, font, text, x, top, x + width, rgb);
}

static void draw_slot(const band_t *band, const row_t *row, rect_t area, uint32_t phase)
{
    if (band->y0 >= area.y + area.h || band->y0 + band->rows <= area.y) {
        return;
    }
    bool alarm = row->state == ROW_WAITING || row->state == ROW_ERROR;
    draw_panel(band, area, MODULE_RADIUS, 1, alarm ? lamp_colour(row->state) : TOWER_LINE, TOWER_MODULE);
    int name_x = area.x + MARGIN;
    if (row->project) {
        name_x = draw_badge(band, area.x + MARGIN - 3, area.y + BADGE_CENTRE_Y, BADGE_SIZE);
    }
    draw_text(band, &font_pixel, row->name, name_x, area.y + NAME_TOP, area.x + area.w - MARGIN, TOWER_NAME);
    rect_t lamp = {area.x + (area.w - LAMP) / 2, area.y + LAMP_TOP, LAMP, LAMP};
    draw_lamp(band, row->state, lamp, phase);
    char age[SCREEN_AGE_MAX];
    const char *word = row->state == ROW_DONE && row->minutes != ROWS_HIDDEN_MINUTES
                           ? screen_age(row->minutes, age, sizeof age)
                           : STATE_WORDS[row->state];
    uint32_t text_rgb = row->state == ROW_ERROR ? TOWER_RED_TEXT : lamp_colour(row->state);
    draw_centered(band, &font_pixel, word, area, area.y + STATE_TOP, text_rgb);
}

static void draw_empty(const band_t *band, rect_t area)
{
    draw_fill(band, area, TOWER_MODULE);
    draw_dashed(band, area, 1, EMPTY_DASH, TOWER_LINE);
    draw_centered(band, &font_pixel, EMPTY, area, area.y + (area.h - font_pixel.line_height) / 2, TOWER_FAINT);
}

static void draw_home(const band_t *band, rect_t area)
{
    draw_panel(band, area, MODULE_RADIUS, 1, TOWER_FAINT, TOWER_MODULE);
    draw_text(band, &font_pixel, HOME, area.x + MARGIN, area.y + NAME_TOP, area.x + area.w - MARGIN, TOWER_TITLE);
}

static void draw_message(const band_t *band, screen_mode_t mode, rect_t area)
{
    rect_t frame = {THEME_GRID_LEFT, area.y, DRAW_W - 2 * THEME_GRID_LEFT, area.h - THEME_GRID_GAP};
    bool empty = mode == SCREEN_EMPTY;
    draw_panel(band, frame, MODULE_RADIUS, 1, empty ? TOWER_LINE : TOWER_AMBER, TOWER_MODULE);
    draw_centered(band, &font_large, empty ? "没有会话" : "未连接", area, area.y + MESSAGE_TITLE_TOP,
                  empty ? TOWER_TITLE : TOWER_AMBER);
    draw_centered(band, &HINT_FONT, empty ? "开一个会话就会出现在这里" : "等 Mac 上的桥接程序连上", area,
                  area.y + MESSAGE_HINT_TOP, TOWER_NAME);
}

static void draw_footer(const band_t *band, const screen_t *screen, rect_t area)
{
    (void)screen;
    draw_fill(band, (rect_t){area.x, area.y + FOOTER_RULE, area.w, 1}, TOWER_LINE);
    draw_text(band, &font_pixel, FOOTER, area.x + MARGIN, area.y + FOOTER_TEXT_TOP, area.x + area.w - MARGIN,
              TOWER_FAINT);
}

const theme_t theme_tower = {
    .header = {0, 0, DRAW_W, THEME_HEADER_H},
    .slots = {THEME_GRID_SLOT(0), THEME_GRID_SLOT(1), THEME_GRID_SLOT(2), THEME_GRID_SLOT(3), THEME_GRID_SLOT(4),
              THEME_GRID_SLOT(5)},
    .body = {0, THEME_GRID_TOP, DRAW_W, THEME_GRID_FOOTER - THEME_GRID_TOP},
    .footer = {0, THEME_GRID_FOOTER, DRAW_W, THEME_GRID_FOOTER_H},
    .home = {{169, 200, 64, 74}, {121, 197, 112, 78}, {124, 214, 46, 40}, CHARACTER_SMALL},
    .message = {{168, 193, 64, 74}, {4, 25, 232, 244}, {121, 207, 46, 40}, CHARACTER_SMALL},
    .hint_font = &HINT_FONT,
    .hint_top = MESSAGE_HINT_TOP,
    .character = {TOWER_NAME, TOWER_MODULE},
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
