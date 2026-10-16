#pragma once

#include <stddef.h>
#include <stdint.h>

#define FONT_ELLIPSIS 0x2026

typedef struct {
    uint32_t codepoint;
    uint8_t width;
    uint32_t offset;
} font_glyph_t;

typedef struct {
    uint8_t line_height;
    uint16_t glyph_count;
    const font_glyph_t *glyphs;
    const uint8_t *bitmaps;
} font_t;

extern const font_t font_large;
extern const font_t font_text;

const font_glyph_t *font_find(const font_t *font, uint32_t codepoint);
uint8_t font_alpha(const font_t *font, const font_glyph_t *glyph, int x, int y);
uint32_t font_next_utf8(const char **cursor, const char *end);
int font_decode_utf8(const char *text, size_t len, uint32_t *codepoints, int max);
int font_width(const font_t *font, const char *text, size_t len);
