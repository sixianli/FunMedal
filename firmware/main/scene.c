#include "scene.h"

#include <string.h>

#include "font.h"

#define MAX_LABEL_GLYPHS 8

typedef struct {
    const char *name;
    const char *label;
    uint32_t background[PET_FRAMES];
    uint32_t text;
} state_style_t;

static const state_style_t STYLES[PET_STATE_COUNT] = {
    [PET_IDLE] = {"idle", "空闲", {0x2b2d42, 0x2b2d42}, 0xedf2f4},
    [PET_WORKING] = {"working", "干活中", {0x1d4e89, 0x1d4e89}, 0xffffff},
    [PET_WAITING] = {"waiting", "等你批准", {0xff9f1c, 0xffbf69}, 0x1a1c2c},
    [PET_DONE] = {"done", "完成了", {0x2a9d8f, 0x2a9d8f}, 0xffffff},
    [PET_ERROR] = {"error", "出错了", {0x9b2226, 0x9b2226}, 0xffffff},
};

uint16_t scene_rgb565_wire(uint32_t rgb)
{
    uint16_t c = (uint16_t)(((rgb >> 8) & 0xf800) | ((rgb >> 5) & 0x07e0) | ((rgb >> 3) & 0x001f));
    return (uint16_t)((c >> 8) | (c << 8));
}

static uint32_t blend(uint32_t bg, uint32_t fg, uint8_t alpha)
{
    uint32_t out = 0;
    for (int shift = 0; shift <= 16; shift += 8) {
        int b = (bg >> shift) & 0xff;
        int f = (fg >> shift) & 0xff;
        out |= (uint32_t)(b + (f - b) * alpha / 15) << shift;
    }
    return out;
}

uint32_t scene_background_rgb(pet_state_t state, uint8_t frame)
{
    return STYLES[state].background[frame % PET_FRAMES];
}

uint32_t scene_text_rgb(pet_state_t state)
{
    return STYLES[state].text;
}

const char *scene_state_name(pet_state_t state)
{
    return STYLES[state].name;
}

const char *scene_state_label(pet_state_t state)
{
    return STYLES[state].label;
}

bool scene_state_from_bytes(const char *data, size_t len, pet_state_t *state)
{
    for (int i = 0; i < PET_STATE_COUNT; i++) {
        if (strlen(STYLES[i].name) == len && memcmp(data, STYLES[i].name, len) == 0) {
            *state = (pet_state_t)i;
            return true;
        }
    }
    return false;
}

bool scene_state_from_name(const char *name, pet_state_t *state)
{
    return scene_state_from_bytes(name, strlen(name), state);
}

static uint32_t pattern_pixel(int x, int y)
{
    const int box = 40;
    if (x == 0 || y == 0 || x == SCENE_W - 1 || y == SCENE_H - 1) {
        return 0xffffff;
    }
    bool left = x < box + 1;
    bool right = x >= SCENE_W - 1 - box;
    bool top = y < box + 1;
    bool bottom = y >= SCENE_H - 1 - box;
    if (top && left) {
        return 0xff0000;
    }
    if (top && right) {
        return 0x00ff00;
    }
    if (bottom && left) {
        return 0x0000ff;
    }
    if (bottom && right) {
        return 0xffffff;
    }
    int cx = SCENE_W / 2;
    if (y >= 60 && y < 120 && x >= cx - (y - 60) / 2 && x <= cx + (y - 60) / 2) {
        return 0xffffff;
    }
    return 0x000000;
}

void scene_render_rows(const scene_t *scene, int y0, int rows, uint16_t *out)
{
    if (scene->fill) {
        uint16_t color = scene_rgb565_wire(scene->fill_rgb);
        for (int i = 0; i < rows * SCENE_W; i++) {
            out[i] = color;
        }
        return;
    }
    if (scene->test_pattern) {
        for (int y = y0; y < y0 + rows; y++) {
            for (int x = 0; x < SCENE_W; x++) {
                *out++ = scene_rgb565_wire(pattern_pixel(x, y));
            }
        }
        return;
    }
    const state_style_t *style = &STYLES[scene->state];
    uint8_t frame = scene->frame % PET_FRAMES;
    uint32_t bg = style->background[frame];
    const uint8_t (*sprite)[PET_SIZE] = pet_sprites[scene->state][frame];

    uint32_t codepoints[MAX_LABEL_GLYPHS];
    const font_glyph_t *glyphs[MAX_LABEL_GLYPHS];
    int starts[MAX_LABEL_GLYPHS];
    int count = font_decode_utf8(style->label, codepoints, MAX_LABEL_GLYPHS);
    int width = 0;
    int shown = 0;
    for (int i = 0; i < count; i++) {
        const font_glyph_t *g = font_find(codepoints[i]);
        if (g == NULL) {
            continue;
        }
        glyphs[shown] = g;
        starts[shown] = width;
        width += g->width;
        shown++;
    }
    int label_x = (SCENE_W - width) / 2;

    for (int y = y0; y < y0 + rows; y++) {
        int sy = (y - SCENE_PET_Y) / SCENE_PET_SCALE;
        bool in_pet_rows = y >= SCENE_PET_Y && sy < PET_SIZE;
        int ly = y - SCENE_LABEL_Y;
        bool in_label_rows = ly >= 0 && ly < font_line_height;
        int glyph = 0;
        for (int x = 0; x < SCENE_W; x++) {
            uint32_t color = bg;
            if (in_pet_rows && x >= SCENE_PET_X) {
                int sx = (x - SCENE_PET_X) / SCENE_PET_SCALE;
                if (sx < PET_SIZE) {
                    uint8_t index = sprite[sy][sx];
                    if (index != 0) {
                        color = pet_palette[index];
                    }
                }
            }
            if (in_label_rows && shown > 0) {
                int lx = x - label_x;
                while (glyph < shown - 1 && lx >= starts[glyph + 1]) {
                    glyph++;
                }
                if (lx >= 0 && lx < width) {
                    uint8_t alpha = font_alpha(glyphs[glyph], lx - starts[glyph], ly);
                    if (alpha) {
                        color = blend(color, style->text, alpha);
                    }
                }
            }
            *out++ = scene_rgb565_wire(color);
        }
    }
}
