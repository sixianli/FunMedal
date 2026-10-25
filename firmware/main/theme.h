#pragma once

#include <stdint.h>

#include "draw.h"
#include "rows.h"

#define THEME_SLOTS ROWS_MAX
#define THEME_TICK_MS 100

typedef enum {
    SCREEN_UNLINKED,
    SCREEN_EMPTY,
    SCREEN_ROWS,
} screen_mode_t;

typedef struct {
    screen_mode_t mode;
    uint8_t total;
    uint8_t shown;
    row_t rows[ROWS_MAX];
    uint32_t tick;
} screen_t;

typedef struct {
    rect_t header;
    rect_t slots[THEME_SLOTS];
    rect_t body;
    rect_t footer;
    void (*draw_background)(const band_t *band);
    void (*draw_header)(const band_t *band, const screen_t *screen, rect_t area);
    void (*draw_slot)(const band_t *band, const row_t *row, rect_t area, uint32_t phase);
    void (*draw_message)(const band_t *band, screen_mode_t mode, rect_t area);
    void (*draw_footer)(const band_t *band, const screen_t *screen, rect_t area);
    uint32_t (*slot_phase)(row_state_t state, uint32_t tick);
} theme_t;

extern const theme_t theme_board;
