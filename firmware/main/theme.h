#pragma once

#include <stdint.h>

#include "character.h"
#include "draw.h"
#include "events.h"
#include "rows.h"

#define THEME_SLOTS ROWS_MAX
#define THEME_TICK_MS 100
#define THEME_HEADER_H 24
#define THEME_GRID_LEFT 3
#define THEME_GRID_TOP 24
#define THEME_GRID_W 114
#define THEME_GRID_H 80
#define THEME_GRID_GAP 6
#define THEME_GRID_FOOTER 276
#define THEME_GRID_FOOTER_H 44
#define THEME_GRID_SESSIONS (THEME_SLOTS - 1)
#define THEME_GRID_SLOT(i)                                                                                        \
    {THEME_GRID_LEFT + (i) % 2 * (THEME_GRID_W + THEME_GRID_GAP), THEME_GRID_TOP + (i) / 2 * (THEME_GRID_H + THEME_GRID_GAP), \
     THEME_GRID_W, THEME_GRID_H}

typedef enum {
    SCREEN_UNLINKED,
    SCREEN_EMPTY,
    SCREEN_ROWS,
} screen_mode_t;

typedef struct {
    screen_mode_t mode;
    uint8_t total;
    uint8_t shown;
    uint8_t urgent;
    row_t rows[ROWS_MAX];
    uint32_t tick;
    uint32_t daily;
    event_view_t event;
} screen_t;

typedef struct {
    rect_t area;
    character_stage_t stage;
} theme_event_t;

typedef struct {
    rect_t header;
    rect_t slots[THEME_SLOTS];
    rect_t body;
    rect_t footer;
    character_stage_t home;
    character_stage_t message;
    const font_t *hint_font;
    int hint_top;
    character_colors_t character;
    theme_event_t alert;
    rect_t cheer;
    character_stage_t hello;
    void (*draw_background)(const band_t *band);
    void (*draw_header)(const band_t *band, const screen_t *screen, rect_t area);
    void (*draw_slot)(const band_t *band, const row_t *row, rect_t area, uint32_t phase);
    void (*draw_empty)(const band_t *band, rect_t area);
    void (*draw_home)(const band_t *band, rect_t area);
    void (*draw_message)(const band_t *band, screen_mode_t mode, rect_t area);
    void (*draw_footer)(const band_t *band, const screen_t *screen, rect_t area);
    void (*draw_alert)(const band_t *band, event_kind_t kind, const char *name, rect_t area);
    void (*draw_cheer)(const band_t *band, const char *name, rect_t area);
    void (*draw_hello)(const band_t *band, rect_t area);
    uint32_t (*slot_phase)(row_state_t state, uint32_t tick);
    uint32_t (*footer_phase)(uint32_t tick);
} theme_t;

extern const theme_t theme_board;
extern const theme_t theme_arcade;
extern const theme_t theme_comic;
extern const theme_t theme_tower;
