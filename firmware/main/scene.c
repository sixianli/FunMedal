#include "scene.h"

#include <stdio.h>
#include <string.h>

#include "font.h"

#define MAX_RUN_GLYPHS 64
#define MAX_RUNS 16
#define PROMPT_BACKGROUND 0xff9f1c
#define PROMPT_TEXT 0x1a1c2c
#define PAIRING_BACKGROUND 0x1d4e89
#define PAIRING_TEXT 0xffffff
#define HEADER_TOP 6
#define TOOL_TOP 54
#define BODY_TOP 82
#define FOOTER_TOP 262
#define PAIRING_TITLE_TOP 30
#define PAIRING_CODE_TOP 110
#define PAIRING_HINT_TOP 190
#define VOICE_BACKGROUND 0x5a189a
#define VOICE_TEXT 0xffffff
#define VOICE_TITLE_TOP 110
#define VOICE_HINT_TOP 170
#define VOICE_IMAGE_TOP 60
#define VOICE_TEXT_LINES 2
#define VOICE_NOT_SENT "没送进去"

typedef struct {
    const char *name;
    const char *label;
    uint32_t background[PET_FRAMES];
    uint32_t text;
} state_style_t;

typedef struct {
    const char *title;
    const char *lines[VOICE_TEXT_LINES];
} voice_text_t;

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

static const char *const APPROVE_HINT = "长按右侧最上面的键 批准";
static const char *const DENY_HINT = "按右侧最下面的键 拒绝";
static const char *const APPROVED = "已批准";
static const char *const DENIED = "已拒绝";
static const char *const PAIRING_TITLE = "蓝牙配对";
static const char *const NUMCMP_HINTS[] = {"数字和 Mac 上的相同时", "按右侧最上面的键配对", "按右侧最下面的键取消"};
static const char *const PASSKEY_HINT = "在 Mac 上输入这串数字";
static const char *const LISTENING = "在听";
static const char *const LISTENING_HINT = "松开就发送";
static const char *const TRANSCRIBING = "转文字中";
static const voice_text_t VOICE_TEXTS[] = {
    [VOICE_RESULT_QUEUED] = {"已排队", {NULL, NULL}},
    [VOICE_RESULT_SUBMITTED] = {"已送进去", {NULL, NULL}},
    [VOICE_RESULT_EMPTY] = {VOICE_NOT_SENT, {"没听清", NULL}},
    [VOICE_RESULT_NO_SESSION] = {"未连接", {"没有会话在收语音", "在电脑上发一句话再试"}},
    [VOICE_RESULT_DENIED] = {VOICE_NOT_SENT, {"Mac 没有语音识别权限", NULL}},
    [VOICE_RESULT_CLEARED] = {VOICE_NOT_SENT, {"刚才那句被停止清掉了", "请再说一遍"}},
    [VOICE_RESULT_STOPPED] = {VOICE_NOT_SENT, {"你按过停止", "请先在电脑上打一句话"}},
    [VOICE_RESULT_REFUSED] = {VOICE_NOT_SENT, {"Claude Code 没收下这句", NULL}},
    [VOICE_RESULT_OFFLINE] = {VOICE_NOT_SENT, {"没连上 Mac", NULL}},
    [VOICE_RESULT_TIMEOUT] = {VOICE_NOT_SENT, {"Mac 没有回音", NULL}},
};
static const char *const VOICE_NAMES[] = {
    [SCENE_VOICE_NONE] = "none",
    [SCENE_VOICE_LISTENING] = "listening",
    [SCENE_VOICE_TRANSCRIBING] = "transcribing",
    [SCENE_VOICE_RESULT] = "result",
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

const char *scene_voice_name(scene_voice_t voice)
{
    return VOICE_NAMES[voice];
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

static void add_run(runs_t *runs, const font_t *font, const char *text, size_t len, int x, int top)
{
    if (runs->count < MAX_RUNS) {
        runs->items[runs->count++] = (text_run_t){font, text, len, x, top};
    }
}

static void add_text(runs_t *runs, const font_t *font, const char *text, int x, int top)
{
    add_run(runs, font, text, strlen(text), x, top);
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

static const char ELLIPSIS[] = "\xe2\x80\xa6";

static void add_ellipsis(runs_t *runs, const font_t *font, const char *kept, size_t len, int x, int top)
{
    add_run(runs, font, ELLIPSIS, strlen(ELLIPSIS), x + font_width(font, kept, len), top);
}

static void add_fitted(runs_t *runs, const font_t *font, const char *text, int x, int top)
{
    bool truncated = false;
    size_t kept = prompt_fit(font, text, strlen(text), SCENE_TEXT_WIDTH, &truncated);
    add_run(runs, font, text, kept, x, top);
    if (truncated) {
        add_ellipsis(runs, font, text, kept, x, top);
    }
}

static void render_prompt(const scene_t *scene, int y0, int rows, uint16_t *out)
{
    const prompt_t *prompt = &scene->prompt;
    char position[16];
    snprintf(position, sizeof(position), "%u/%u", (unsigned)prompt->index, (unsigned)prompt->count);
    runs_t runs = {.count = 0};
    add_text(&runs, &font_large, STYLES[PET_WAITING].label, SCENE_MARGIN, HEADER_TOP);
    int position_x = SCENE_W - SCENE_MARGIN - font_width(&font_text, position, strlen(position));
    add_run(&runs, &font_text, position, strlen(position), position_x,
            HEADER_TOP + (font_large.line_height - font_text.line_height) / 2);
    add_fitted(&runs, &font_text, prompt->tool, SCENE_MARGIN, TOOL_TOP);
    prompt_layout_t layout;
    prompt_layout(&font_text, prompt->text, SCENE_TEXT_WIDTH, &layout);
    for (int i = 0; i < layout.count; i++) {
        const prompt_line_t *line = &layout.lines[i];
        int top = BODY_TOP + i * font_text.line_height;
        add_run(&runs, &font_text, prompt->text + line->start, line->len, SCENE_MARGIN, top);
        if (layout.truncated && i == layout.count - 1) {
            add_ellipsis(&runs, &font_text, prompt->text + line->start, line->len, SCENE_MARGIN, top);
        }
    }
    if (scene->decision == SCENE_DECISION_NONE) {
        add_text(&runs, &font_text, APPROVE_HINT, SCENE_MARGIN, FOOTER_TOP);
        add_text(&runs, &font_text, DENY_HINT, SCENE_MARGIN, FOOTER_TOP + font_text.line_height + 4);
    } else {
        add_centered(&runs, &font_large, scene->decision == SCENE_DECISION_ALLOW ? APPROVED : DENIED, FOOTER_TOP);
    }
    render_runs(&runs, PROMPT_BACKGROUND, PROMPT_TEXT, y0, rows, out);
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

static void draw_voice_image(const uint8_t *pixels, int y0, int rows, uint16_t *out)
{
    uint16_t ink = scene_rgb565_wire(VOICE_TEXT);
    for (int y = y0; y < y0 + rows; y++) {
        int iy = y - VOICE_IMAGE_TOP;
        if (iy < 0 || iy >= VOICE_IMAGE_H) {
            continue;
        }
        uint16_t *row = out + (y - y0) * SCENE_W;
        for (int ix = 0; ix < VOICE_IMAGE_W; ix++) {
            if (voice_image_pixel(pixels, ix, iy)) {
                row[SCENE_MARGIN + ix] = ink;
            }
        }
    }
}

static void render_voice(const scene_t *scene, int y0, int rows, uint16_t *out)
{
    runs_t runs = {.count = 0};
    bool image = false;
    if (scene->voice == SCENE_VOICE_LISTENING) {
        add_centered(&runs, &font_large, LISTENING, VOICE_TITLE_TOP);
        add_centered(&runs, &font_text, LISTENING_HINT, VOICE_HINT_TOP);
    } else if (scene->voice == SCENE_VOICE_TRANSCRIBING) {
        add_centered(&runs, &font_large, TRANSCRIBING, VOICE_TITLE_TOP);
    } else if (scene->voice_result == VOICE_RESULT_QUEUED || scene->voice_result == VOICE_RESULT_SUBMITTED) {
        image = scene->voice_image != NULL;
        add_centered(&runs, &font_large, VOICE_TEXTS[scene->voice_result].title, image ? HEADER_TOP : VOICE_TITLE_TOP);
    } else {
        const voice_text_t *text = &VOICE_TEXTS[scene->voice_result];
        add_centered(&runs, &font_large, text->title, VOICE_TITLE_TOP);
        for (int i = 0; i < VOICE_TEXT_LINES && text->lines[i] != NULL; i++) {
            add_centered(&runs, &font_text, text->lines[i], VOICE_HINT_TOP + i * (font_text.line_height + 4));
        }
    }
    render_runs(&runs, VOICE_BACKGROUND, VOICE_TEXT, y0, rows, out);
    if (image) {
        draw_voice_image(scene->voice_image, y0, rows, out);
    }
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
    } else if (scene->voice != SCENE_VOICE_NONE) {
        render_voice(scene, y0, rows, out);
    } else if (scene->prompt.active) {
        render_prompt(scene, y0, rows, out);
    } else {
        render_pet(scene, y0, rows, out);
    }
}
