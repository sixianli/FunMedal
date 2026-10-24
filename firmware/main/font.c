#include "font.h"

#include <string.h>

#define HEADER_BYTES 12
#define ENTRY_BYTES 12

static const uint8_t MAGIC[4] = {'F', 'M', 'F', '1'};

static uint32_t le16(const uint8_t *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8;
}

static uint32_t le32(const uint8_t *p)
{
    return le16(p) | le16(p + 2) << 16;
}

static size_t stride(const font_t *font, uint32_t width)
{
    return font->bits == 1 ? (width + 7) / 8 : (width + 1) / 2;
}

static font_glyph_t entry(const font_t *font, uint32_t i)
{
    const uint8_t *p = font->index + (size_t)i * ENTRY_BYTES;
    return (font_glyph_t){le32(p), (uint16_t)le16(p + 8), le32(p + 4)};
}

bool font_load(font_t *font, const uint8_t *data, size_t size)
{
    if (size < HEADER_BYTES || memcmp(data, MAGIC, sizeof(MAGIC)) != 0) {
        return false;
    }
    font_t loaded = {
        .line_height = data[4],
        .bits = data[5],
        .glyph_count = le32(data + 8),
        .index = data + HEADER_BYTES,
    };
    if (loaded.line_height == 0 || (loaded.bits != 1 && loaded.bits != 4)
        || loaded.glyph_count > (size - HEADER_BYTES) / ENTRY_BYTES) {
        return false;
    }
    loaded.bitmaps = loaded.index + (size_t)loaded.glyph_count * ENTRY_BYTES;
    size_t bitmap_bytes = size - HEADER_BYTES - (size_t)loaded.glyph_count * ENTRY_BYTES;
    size_t expected = 0;
    for (uint32_t i = 0; i < loaded.glyph_count; i++) {
        font_glyph_t glyph = entry(&loaded, i);
        if ((i > 0 && glyph.codepoint <= entry(&loaded, i - 1).codepoint) || glyph.offset != expected) {
            return false;
        }
        expected += stride(&loaded, glyph.width) * loaded.line_height;
    }
    if (expected != bitmap_bytes) {
        return false;
    }
    *font = loaded;
    return true;
}

static bool find(const font_t *font, uint32_t codepoint, font_glyph_t *found)
{
    uint32_t lo = 0;
    uint32_t hi = font->glyph_count;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        font_glyph_t glyph = entry(font, mid);
        if (glyph.codepoint == codepoint) {
            *found = glyph;
            return true;
        }
        if (glyph.codepoint < codepoint) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    return false;
}

bool font_has(const font_t *font, uint32_t codepoint)
{
    font_glyph_t glyph;
    return find(font, codepoint, &glyph);
}

font_glyph_t font_glyph(const font_t *font, uint32_t codepoint)
{
    font_glyph_t glyph = {0};
    if (!find(font, codepoint, &glyph)) {
        find(font, FONT_MISSING, &glyph);
    }
    return glyph;
}

uint8_t font_alpha(const font_t *font, const font_glyph_t *glyph, int x, int y)
{
    if (x < 0 || y < 0 || x >= glyph->width || y >= font->line_height) {
        return 0;
    }
    const uint8_t *row = font->bitmaps + glyph->offset + (size_t)y * stride(font, glyph->width);
    if (font->bits == 1) {
        return (row[x / 8] >> (7 - x % 8)) & 1 ? 0x0f : 0;
    }
    uint8_t byte = row[x / 2];
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
        width += font_glyph(font, font_next_utf8(&text, end)).width;
    }
    return width;
}
