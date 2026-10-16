#pragma once

#include <stdint.h>

typedef struct {
    uint32_t codepoint;
    uint8_t width;
    uint32_t offset;
} font_glyph_t;

extern const uint8_t font_line_height;
extern const uint16_t font_glyph_count;
extern const font_glyph_t font_glyphs[];
extern const uint8_t font_bitmaps[];

const font_glyph_t *font_find(uint32_t codepoint);
uint8_t font_alpha(const font_glyph_t *glyph, int x, int y);
int font_decode_utf8(const char *text, uint32_t *codepoints, int max);
