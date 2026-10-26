#pragma once

#include <stdint.h>

#include "font.h"

#define DRAW_W 240
#define DRAW_WEDGE_STEPS 16

typedef struct {
    uint16_t *pixels;
    int y0;
    int rows;
} band_t;

typedef struct {
    int x;
    int y;
    int w;
    int h;
} rect_t;

typedef struct {
    int16_t x;
    int16_t y;
} point_t;

typedef struct {
    int w;
    int h;
    const uint8_t *colors;
    const uint8_t *alphas;
} image_t;

uint16_t draw_wire(uint32_t rgb);
uint32_t draw_rgb(uint16_t wire);
uint32_t draw_blend(uint32_t bg, uint32_t fg, uint8_t alpha);
void draw_fill(const band_t *band, rect_t rect, uint32_t rgb);
void draw_round(const band_t *band, rect_t rect, int radius, uint32_t rgb);
void draw_tint(const band_t *band, rect_t rect, int radius, uint32_t rgb, uint8_t alpha);
void draw_dashed(const band_t *band, rect_t rect, int width, int dash, uint32_t rgb);
void draw_dots(const band_t *band, rect_t rect, int period, uint32_t rgb, uint8_t alpha);
void draw_wedge(const band_t *band, rect_t circle, int step, uint32_t rgb, uint8_t alpha);
void draw_panel(const band_t *band, rect_t rect, int radius, int border, uint32_t border_rgb, uint32_t fill_rgb);
void draw_stripes(const band_t *band, rect_t rect, int radius, int period, int offset, uint32_t rgb, uint8_t alpha);
void draw_scanlines(const band_t *band, rect_t rect, int period, uint32_t rgb, uint8_t alpha);
void draw_sprite(const band_t *band, const uint8_t *indices, const uint32_t *palette, int w, int h, int x, int y,
                 int scale);
void draw_image(const band_t *band, const image_t *image, int x, int y, rect_t limit);
int draw_text(const band_t *band, const font_t *font, const char *text, int x, int top, int max_x, uint32_t rgb);
int draw_text_width(const font_t *font, const char *text, int max_w);
int draw_badge(const band_t *band, int x, int cy, int size);
void draw_polygon(const band_t *band, const point_t *points, int count, uint32_t rgb);
