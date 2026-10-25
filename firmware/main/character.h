#pragma once

#include <stdint.h>

#include "character_art.h"
#include "draw.h"

typedef enum {
    CHARACTER_EXTRA_NONE,
    CHARACTER_EXTRA_QUESTION,
    CHARACTER_EXTRA_BUBBLE,
    CHARACTER_EXTRA_SLEEP,
} character_extra_t;

typedef struct {
    uint8_t pose;
    uint8_t extra;
    uint8_t step;
} character_frame_t;

typedef struct {
    rect_t box;
    rect_t clip;
    rect_t extra;
} character_stage_t;

typedef struct {
    uint32_t ink;
    uint32_t paper;
} character_colors_t;

uint32_t character_daily_ticks(void);
character_frame_t character_frame(uint32_t tick);
uint32_t character_frame_key(character_frame_t frame);
rect_t character_bounds(const character_stage_t *stage);
void character_draw(const band_t *band, const character_stage_t *stage, character_frame_t frame,
                    character_colors_t colors);
