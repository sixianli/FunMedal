#include "font.h"

#include <stddef.h>

const font_glyph_t *font_find(uint32_t codepoint)
{
    int lo = 0;
    int hi = font_glyph_count - 1;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        if (font_glyphs[mid].codepoint == codepoint) {
            return &font_glyphs[mid];
        }
        if (font_glyphs[mid].codepoint < codepoint) {
            lo = mid + 1;
        } else {
            hi = mid - 1;
        }
    }
    return NULL;
}

uint8_t font_alpha(const font_glyph_t *glyph, int x, int y)
{
    if (x < 0 || y < 0 || x >= glyph->width || y >= font_line_height) {
        return 0;
    }
    int stride = (glyph->width + 1) / 2;
    uint8_t byte = font_bitmaps[glyph->offset + y * stride + x / 2];
    return (x % 2 == 0) ? (byte >> 4) : (byte & 0x0f);
}

int font_decode_utf8(const char *text, uint32_t *codepoints, int max)
{
    const unsigned char *s = (const unsigned char *)text;
    int count = 0;
    while (*s && count < max) {
        uint32_t cp;
        int extra;
        if (*s < 0x80) {
            cp = *s;
            extra = 0;
        } else if ((*s & 0xe0) == 0xc0) {
            cp = *s & 0x1f;
            extra = 1;
        } else if ((*s & 0xf0) == 0xe0) {
            cp = *s & 0x0f;
            extra = 2;
        } else {
            cp = *s & 0x07;
            extra = 3;
        }
        s++;
        for (int i = 0; i < extra && (*s & 0xc0) == 0x80; i++, s++) {
            cp = (cp << 6) | (*s & 0x3f);
        }
        codepoints[count++] = cp;
    }
    return count;
}
