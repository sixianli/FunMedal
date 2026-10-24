#include "scene.h"

#include <stdio.h>
#include <string.h>

#include "font.h"
#include "link_version.h"

#define MAX_RUN_GLYPHS 64
#define MAX_RUNS 16
#define PAIRING_BACKGROUND 0x1d4e89
#define PAIRING_TEXT 0xffffff
#define PAIRING_TITLE_TOP 30
#define PAIRING_CODE_TOP 110
#define PAIRING_HINT_TOP 190
#define LINK_BACKGROUND 0x9d0208
#define LINK_TEXT 0xffffff
#define LINK_TITLE_TOP 60
#define LINK_VERSIONS_TOP 120
#define LINK_HINT_TOP 170

typedef struct {
    const char *name;
    const char *label;
    uint32_t background[PET_FRAMES];
    uint32_t text;
} state_style_t;

typedef struct {
    const font_t *font;
    const char *text;
    size_t len;
    int x;
    int top;
} text_run_t;

typedef struct {
    text_run_t items[MAX_RUNS];
    int count;
} runs_t;

static const state_style_t STYLES[PET_STATE_COUNT] = {
    [PET_IDLE] = {"idle", "空闲", {0x2b2d42, 0x2b2d42}, 0xedf2f4},
    [PET_WORKING] = {"working", "干活中", {0x1d4e89, 0x1d4e89}, 0xffffff},
    [PET_WAITING] = {"waiting", "等你批准", {0xff9f1c, 0xffbf69}, 0x1a1c2c},
    [PET_DONE] = {"done", "完成了", {0x2a9d8f, 0x2a9d8f}, 0xffffff},
    [PET_ERROR] = {"error", "出错了", {0x9b2226, 0x9b2226}, 0xffffff},
};

static const char *const PAIRING_TITLE = "蓝牙配对";
static const char *const NUMCMP_HINTS[] = {"数字和 Mac 上的相同时", "按右侧最上面的键配对", "按右侧最下面的键取消"};
static const char *const PASSKEY_HINT = "在 Mac 上输入这串数字";
static const char *const LINK_TITLE = "版本不配";
static const char *const LINK_VERSIONS = "卡片 %u · Mac %u";
static const char *const LINK_HINTS[] = {
    [SCENE_LINK_UPDATE_FIRMWARE] = "请更新卡片固件",
    [SCENE_LINK_UPDATE_BRIDGE] = "请更新 Mac 上的桥接程序",
};
static const char *const LINK_RESTART = "然后重启桥接程序";
static const char *const LINK_NAMES[] = {
    [SCENE_LINK_NONE] = "none",
    [SCENE_LINK_MATCH] = "match",
    [SCENE_LINK_UPDATE_FIRMWARE] = "update_firmware",
    [SCENE_LINK_UPDATE_BRIDGE] = "update_bridge",
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

const char *scene_link_name(scene_link_t link)
{
    return LINK_NAMES[link];
}

scene_link_t scene_link_for(uint16_t own, uint16_t peer)
{
    if (peer == own) {
        return SCENE_LINK_MATCH;
    }
    return peer > own ? SCENE_LINK_UPDATE_FIRMWARE : SCENE_LINK_UPDATE_BRIDGE;
}

static bool link_mismatch(const scene_t *scene)
{
    return scene->link == SCENE_LINK_UPDATE_FIRMWARE || scene->link == SCENE_LINK_UPDATE_BRIDGE;
}

bool scene_state_from_name(const char *name, pet_state_t *state)
{
    for (int i = 0; i < PET_STATE_COUNT; i++) {
        if (strcmp(name, STYLES[i].name) == 0) {
            *state = (pet_state_t)i;
            return true;
        }
    }
    return false;
}

static void add_run(runs_t *runs, const font_t *font, const char *text, size_t len, int x, int top)
{
    if (runs->count < MAX_RUNS) {
        runs->items[runs->count++] = (text_run_t){font, text, len, x, top};
    }
}

static void add_centered(runs_t *runs, const font_t *font, const char *text, int top)
{
    size_t len = strlen(text);
    add_run(runs, font, text, len, (SCENE_W - font_width(font, text, len)) / 2, top);
}

static void draw_run_row(const text_run_t *run, int y, uint32_t bg, uint32_t fg, uint16_t *row)
{
    int ly = y - run->top;
    if (ly < 0 || ly >= run->font->line_height) {
        return;
    }
    uint32_t codepoints[MAX_RUN_GLYPHS];
    int count = font_decode_utf8(run->text, run->len, codepoints, MAX_RUN_GLYPHS);
    int x = run->x;
    for (int i = 0; i < count && x < SCENE_W; i++) {
        const font_glyph_t *glyph = font_find(run->font, codepoints[i]);
        if (glyph == NULL) {
            continue;
        }
        for (int gx = 0; gx < glyph->width; gx++) {
            int px = x + gx;
            if (px < 0 || px >= SCENE_W) {
                continue;
            }
            uint8_t alpha = font_alpha(run->font, glyph, gx, ly);
            if (alpha) {
                row[px] = scene_rgb565_wire(blend(bg, fg, alpha));
            }
        }
        x += glyph->width;
    }
}

static void render_runs(const runs_t *runs, uint32_t bg, uint32_t fg, int y0, int rows, uint16_t *out)
{
    uint16_t background = scene_rgb565_wire(bg);
    for (int y = y0; y < y0 + rows; y++) {
        uint16_t *row = out + (y - y0) * SCENE_W;
        for (int x = 0; x < SCENE_W; x++) {
            row[x] = background;
        }
        for (int i = 0; i < runs->count; i++) {
            draw_run_row(&runs->items[i], y, bg, fg, row);
        }
    }
}

static void render_pairing(const scene_t *scene, int y0, int rows, uint16_t *out)
{
    char code[8];
    snprintf(code, sizeof(code), "%06lu", (unsigned long)(scene->pair_code % 1000000));
    runs_t runs = {.count = 0};
    add_centered(&runs, &font_large, PAIRING_TITLE, PAIRING_TITLE_TOP);
    add_centered(&runs, &font_large, code, PAIRING_CODE_TOP);
    if (scene->pairing == SCENE_PAIRING_NUMCMP) {
        for (int i = 0; i < 3; i++) {
            add_centered(&runs, &font_text, NUMCMP_HINTS[i], PAIRING_HINT_TOP + i * (font_text.line_height + 4));
        }
    } else {
        add_centered(&runs, &font_text, PASSKEY_HINT, PAIRING_HINT_TOP);
    }
    render_runs(&runs, PAIRING_BACKGROUND, PAIRING_TEXT, y0, rows, out);
}

static void render_link(const scene_t *scene, int y0, int rows, uint16_t *out)
{
    char versions[40];
    snprintf(versions, sizeof(versions), LINK_VERSIONS, (unsigned)LINK_VERSION, (unsigned)scene->link_peer);
    runs_t runs = {.count = 0};
    add_centered(&runs, &font_large, LINK_TITLE, LINK_TITLE_TOP);
    add_centered(&runs, &font_text, versions, LINK_VERSIONS_TOP);
    add_centered(&runs, &font_text, LINK_HINTS[scene->link], LINK_HINT_TOP);
    add_centered(&runs, &font_text, LINK_RESTART, LINK_HINT_TOP + font_text.line_height + 4);
    render_runs(&runs, LINK_BACKGROUND, LINK_TEXT, y0, rows, out);
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

static void render_pet(const scene_t *scene, int y0, int rows, uint16_t *out)
{
    const state_style_t *style = &STYLES[scene->state];
    uint8_t frame = scene->frame % PET_FRAMES;
    uint32_t bg = style->background[frame];
    const uint8_t (*sprite)[PET_SIZE] = pet_sprites[scene->state][frame];
    runs_t label = {.count = 0};
    add_centered(&label, &font_large, style->label, SCENE_LABEL_Y);
    for (int y = y0; y < y0 + rows; y++) {
        uint16_t *row = out + (y - y0) * SCENE_W;
        int sy = (y - SCENE_PET_Y) / SCENE_PET_SCALE;
        bool in_pet_rows = y >= SCENE_PET_Y && sy < PET_SIZE;
        for (int x = 0; x < SCENE_W; x++) {
            uint32_t color = bg;
            if (in_pet_rows && x >= SCENE_PET_X) {
                int sx = (x - SCENE_PET_X) / SCENE_PET_SCALE;
                if (sx < PET_SIZE && sprite[sy][sx] != 0) {
                    color = pet_palette[sprite[sy][sx]];
                }
            }
            row[x] = scene_rgb565_wire(color);
        }
        draw_run_row(&label.items[0], y, bg, style->text, row);
    }
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
    if (scene->pairing != SCENE_PAIRING_NONE) {
        render_pairing(scene, y0, rows, out);
    } else if (link_mismatch(scene)) {
        render_link(scene, y0, rows, out);
    } else {
        render_pet(scene, y0, rows, out);
    }
}
