#include "draw.h"

#include <string.h>

#define TEXT_GLYPHS_MAX 40
#define SUBSAMPLES 4

typedef struct {
    int y0;
    int y1;
    int x0;
    int x1;
} clip_t;

uint16_t draw_wire(uint32_t rgb)
{
    uint16_t c = (uint16_t)(((rgb >> 8) & 0xf800) | ((rgb >> 5) & 0x07e0) | ((rgb >> 3) & 0x001f));
    return (uint16_t)((c >> 8) | (c << 8));
}

uint32_t draw_rgb(uint16_t wire)
{
    uint16_t c = (uint16_t)((wire >> 8) | (wire << 8));
    uint32_t r = (c >> 11) & 0x1f;
    uint32_t g = (c >> 5) & 0x3f;
    uint32_t b = c & 0x1f;
    return (r << 3 | r >> 2) << 16 | (g << 2 | g >> 4) << 8 | (b << 3 | b >> 2);
}

uint32_t draw_blend(uint32_t bg, uint32_t fg, uint8_t alpha)
{
    uint32_t out = 0;
    for (int shift = 0; shift <= 16; shift += 8) {
        int b = (bg >> shift) & 0xff;
        int f = (fg >> shift) & 0xff;
        out |= (uint32_t)(b + (f - b) * alpha / 15) << shift;
    }
    return out;
}

static int max_int(int a, int b)
{
    return a > b ? a : b;
}

static int min_int(int a, int b)
{
    return a < b ? a : b;
}

static clip_t clip(const band_t *band, rect_t rect)
{
    return (clip_t){
        .y0 = max_int(rect.y, band->y0),
        .y1 = min_int(rect.y + rect.h, band->y0 + band->rows),
        .x0 = max_int(rect.x, 0),
        .x1 = min_int(rect.x + rect.w, DRAW_W),
    };
}

static uint16_t *pixel(const band_t *band, int x, int y)
{
    return band->pixels + (y - band->y0) * DRAW_W + x;
}

static void blend_pixel(const band_t *band, int x, int y, uint32_t rgb, uint8_t alpha)
{
    uint16_t *p = pixel(band, x, y);
    *p = alpha >= 15 ? draw_wire(rgb) : draw_wire(draw_blend(draw_rgb(*p), rgb, alpha));
}

static int clamp(int value, int lo, int hi)
{
    return value < lo ? lo : value > hi ? hi : value;
}

static uint8_t coverage(rect_t rect, int radius, int x, int y)
{
    int r = radius * SUBSAMPLES;
    int left = rect.x * SUBSAMPLES + r;
    int right = (rect.x + rect.w) * SUBSAMPLES - r;
    int top = rect.y * SUBSAMPLES + r;
    int bottom = (rect.y + rect.h) * SUBSAMPLES - r;
    int inside = 0;
    for (int sy = 1; sy < SUBSAMPLES; sy += 2) {
        for (int sx = 1; sx < SUBSAMPLES; sx += 2) {
            int px = x * SUBSAMPLES + sx;
            int py = y * SUBSAMPLES + sy;
            int dx = px - clamp(px, left, right);
            int dy = py - clamp(py, top, bottom);
            inside += dx * dx + dy * dy <= r * r;
        }
    }
    return (uint8_t)(inside * 15 / 4);
}

static int corner_inset(rect_t rect, int radius, int y)
{
    int from_top = y - rect.y;
    int from_bottom = rect.y + rect.h - 1 - y;
    int edge = from_top < from_bottom ? from_top : from_bottom;
    return edge < radius ? radius : 0;
}

typedef struct {
    uint16_t from;
    uint16_t to;
} memo_t;

static void blend_run(uint16_t *row, int x0, int x1, uint32_t rgb, uint8_t alpha, memo_t *memo)
{
    for (int x = x0; x < x1; x++) {
        if (row[x] != memo->from) {
            memo->from = row[x];
            memo->to = draw_wire(draw_blend(draw_rgb(row[x]), rgb, alpha));
        }
        row[x] = memo->to;
    }
}

static void fill_run(uint16_t *row, int x0, int x1, uint16_t wire)
{
    for (int x = x0; x < x1; x++) {
        row[x] = wire;
    }
}

static void edge_pixels(const band_t *band, rect_t rect, int radius, int y, int x0, int x1, uint32_t rgb,
                        uint8_t alpha)
{
    for (int x = x0; x < x1; x++) {
        uint8_t a = (uint8_t)(coverage(rect, radius, x, y) * alpha / 15);
        if (a) {
            blend_pixel(band, x, y, rgb, a);
        }
    }
}

static void shape(const band_t *band, rect_t rect, int radius, uint32_t rgb, uint8_t alpha)
{
    clip_t c = clip(band, rect);
    uint16_t wire = draw_wire(rgb);
    memo_t memo = {.from = 0, .to = draw_wire(draw_blend(0, rgb, alpha))};
    for (int y = c.y0; y < c.y1; y++) {
        int inset = radius > 0 ? corner_inset(rect, radius, y) : 0;
        int inner0 = max_int(c.x0, rect.x + inset);
        int inner1 = min_int(c.x1, rect.x + rect.w - inset);
        uint16_t *row = pixel(band, 0, y);
        if (alpha >= 15) {
            fill_run(row, inner0, inner1, wire);
        } else {
            blend_run(row, inner0, inner1, rgb, alpha, &memo);
        }
        if (inset > 0) {
            edge_pixels(band, rect, radius, y, c.x0, min_int(inner0, c.x1), rgb, alpha);
            edge_pixels(band, rect, radius, y, max_int(inner1, c.x0), c.x1, rgb, alpha);
        }
    }
}

void draw_fill(const band_t *band, rect_t rect, uint32_t rgb)
{
    shape(band, rect, 0, rgb, 15);
}

void draw_round(const band_t *band, rect_t rect, int radius, uint32_t rgb)
{
    shape(band, rect, radius, rgb, 15);
}

void draw_panel(const band_t *band, rect_t rect, int radius, int border, uint32_t border_rgb, uint32_t fill_rgb)
{
    draw_round(band, rect, radius, border_rgb);
    rect_t inner = {rect.x + border, rect.y + border, rect.w - 2 * border, rect.h - 2 * border};
    draw_round(band, inner, max_int(radius - border, 0), fill_rgb);
}

void draw_stripes(const band_t *band, rect_t rect, int radius, int period, int offset, uint32_t rgb, uint8_t alpha)
{
    clip_t c = clip(band, rect);
    memo_t memo = {.from = 0, .to = draw_wire(draw_blend(0, rgb, alpha))};
    int half = period / 2;
    for (int y = c.y0; y < c.y1; y++) {
        int inset = radius > 0 ? corner_inset(rect, radius, y) : 0;
        int inner0 = max_int(c.x0, rect.x + inset);
        int inner1 = min_int(c.x1, rect.x + rect.w - inset);
        uint16_t *row = pixel(band, 0, y);
        for (int x = c.x0; x < c.x1;) {
            int at = ((x - y - offset) % period + period) % period;
            int run_end = min_int(c.x1, x + (at < half ? half - at : period - at));
            if (at < half) {
                int a0 = max_int(x, inner0);
                int a1 = min_int(run_end, inner1);
                if (a0 < a1) {
                    blend_run(row, a0, a1, rgb, alpha, &memo);
                }
                if (inset > 0) {
                    edge_pixels(band, rect, radius, y, x, min_int(run_end, inner0), rgb, alpha);
                    edge_pixels(band, rect, radius, y, max_int(x, inner1), run_end, rgb, alpha);
                }
            }
            x = run_end;
        }
    }
}

void draw_scanlines(const band_t *band, rect_t rect, int period, uint32_t rgb, uint8_t alpha)
{
    clip_t c = clip(band, rect);
    for (int y = c.y0; y < c.y1; y++) {
        if ((y - rect.y) % period != 0) {
            continue;
        }
        for (int x = c.x0; x < c.x1; x++) {
            blend_pixel(band, x, y, rgb, alpha);
        }
    }
}

void draw_sprite(const band_t *band, const uint8_t *indices, const uint32_t *palette, int w, int h, int x, int y,
                 int scale)
{
    clip_t c = clip(band, (rect_t){x, y, w * scale, h * scale});
    for (int py = c.y0; py < c.y1; py++) {
        const uint8_t *line = indices + (py - y) / scale * w;
        uint16_t *row = pixel(band, 0, py);
        for (int px = c.x0; px < c.x1; px++) {
            uint8_t index = line[(px - x) / scale];
            if (index != 0) {
                row[px] = draw_wire(palette[index]);
            }
        }
    }
}

typedef struct {
    font_glyph_t glyphs[TEXT_GLYPHS_MAX + 1];
    int count;
    int width;
} line_t;

static void fit(const font_t *font, const char *text, int max_w, line_t *line)
{
    uint32_t codepoints[TEXT_GLYPHS_MAX];
    int count = font_decode_utf8(text, strlen(text), codepoints, TEXT_GLYPHS_MAX);
    line->width = 0;
    for (int i = 0; i < count; i++) {
        line->glyphs[i] = font_glyph(font, codepoints[i]);
        line->width += line->glyphs[i].width;
    }
    line->count = count;
    if (line->width <= max_w) {
        return;
    }
    font_glyph_t ellipsis = font_glyph(font, FONT_ELLIPSIS);
    line->width = 0;
    line->count = 0;
    while (line->count < count && line->width + line->glyphs[line->count].width + ellipsis.width <= max_w) {
        line->width += line->glyphs[line->count++].width;
    }
    line->glyphs[line->count++] = ellipsis;
    line->width += ellipsis.width;
}

int draw_text_width(const font_t *font, const char *text, int max_w)
{
    line_t line;
    fit(font, text, max_w, &line);
    return line.width;
}

int draw_text(const band_t *band, const font_t *font, const char *text, int x, int top, int max_x, uint32_t rgb)
{
    if (top >= band->y0 + band->rows || top + font->line_height <= band->y0) {
        return x + draw_text_width(font, text, max_x - x);
    }
    line_t line;
    fit(font, text, max_x - x, &line);
    clip_t c = clip(band, (rect_t){x, top, line.width, font->line_height});
    for (int i = 0; i < line.count; i++) {
        const font_glyph_t *glyph = &line.glyphs[i];
        int gx0 = max_int(c.x0 - x, 0);
        int gx1 = min_int(c.x1 - x, glyph->width);
        for (int y = c.y0; y < c.y1; y++) {
            for (int gx = gx0; gx < gx1; gx++) {
                uint8_t alpha = font_alpha(font, glyph, gx, y - top);
                if (alpha) {
                    blend_pixel(band, x + gx, y, rgb, alpha);
                }
            }
        }
        x += glyph->width;
    }
    return x;
}
