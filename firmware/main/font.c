#include "font.h"

const font_glyph_t *font_find(const font_t *font, uint32_t codepoint)
{
    int lo = 0;
    int hi = font->glyph_count - 1;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        if (font->glyphs[mid].codepoint == codepoint) {
            return &font->glyphs[mid];
        }
        if (font->glyphs[mid].codepoint < codepoint) {
            lo = mid + 1;
        } else {
            hi = mid - 1;
        }
    }
    return NULL;
}

uint8_t font_alpha(const font_t *font, const font_glyph_t *glyph, int x, int y)
{
    if (x < 0 || y < 0 || x >= glyph->width || y >= font->line_height) {
        return 0;
    }
    int stride = (glyph->width + 1) / 2;
    uint8_t byte = font->bitmaps[glyph->offset + y * stride + x / 2];
    return (x % 2 == 0) ? (byte >> 4) : (byte & 0x0f);
}

uint32_t font_next_utf8(const char **cursor, const char *end)
{
    const unsigned char *s = (const unsigned char *)*cursor;
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
    for (int i = 0; i < extra && s < (const unsigned char *)end && (*s & 0xc0) == 0x80; i++, s++) {
        cp = (cp << 6) | (*s & 0x3f);
    }
    *cursor = (const char *)s;
    return cp;
}

int font_decode_utf8(const char *text, size_t len, uint32_t *codepoints, int max)
{
    const char *end = text + len;
    int count = 0;
    while (text < end && count < max) {
        codepoints[count++] = font_next_utf8(&text, end);
    }
    return count;
}

int font_width(const font_t *font, const char *text, size_t len)
{
    const char *end = text + len;
    int width = 0;
    while (text < end) {
        const font_glyph_t *glyph = font_find(font, font_next_utf8(&text, end));
        width += glyph != NULL ? glyph->width : 0;
    }
    return width;
}
