#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define FONT_ELLIPSIS 0x2026
#define FONT_MISSING 0x25a1

typedef struct {
    uint32_t codepoint;
    uint16_t width;
    uint32_t offset;
} font_glyph_t;

typedef struct {
    uint8_t line_height;
    uint8_t bits;
    uint32_t glyph_count;
    const uint8_t *index;
    const uint8_t *bitmaps;
} font_t;

extern font_t font_large;
extern font_t font_text;
extern font_t font_pixel;

bool font_init(void);
bool font_load(font_t *font, const uint8_t *data, size_t size);
bool font_has(const font_t *font, uint32_t codepoint);
font_glyph_t font_glyph(const font_t *font, uint32_t codepoint);
uint8_t font_alpha(const font_t *font, const font_glyph_t *glyph, int x, int y);
uint32_t font_next_utf8(const char **cursor, const char *end);
int font_decode_utf8(const char *text, size_t len, uint32_t *codepoints, int max);
int font_width(const font_t *font, const char *text, size_t len);
