#include "character.h"

#include "dirty.h"
#include "font.h"

#define SLEEP_STEP_TICKS 5
#define SLEEP_STEPS 4
#define SLEEP_RISE 4
#define BUBBLE_PAD 5
#define BUBBLE_RADIUS 6
#define BUBBLE_BORDER 1

typedef struct {
    uint8_t pose;
    uint8_t extra;
    uint8_t ticks;
} segment_t;

static const segment_t DAILY[] = {
    {CHARACTER_LYING, CHARACTER_EXTRA_NONE, 50},
    {CHARACTER_TAP_UP, CHARACTER_EXTRA_NONE, 3},
    {CHARACTER_TAP_DOWN, CHARACTER_EXTRA_NONE, 3},
    {CHARACTER_TAP_UP, CHARACTER_EXTRA_NONE, 3},
    {CHARACTER_TAP_DOWN, CHARACTER_EXTRA_NONE, 3},
    {CHARACTER_TAP_UP, CHARACTER_EXTRA_NONE, 3},
    {CHARACTER_TAP_DOWN, CHARACTER_EXTRA_NONE, 3},
    {CHARACTER_TILT, CHARACTER_EXTRA_QUESTION, 30},
    {CHARACTER_MUMBLE, CHARACTER_EXTRA_BUBBLE, 30},
    {CHARACTER_SLEEPY, CHARACTER_EXTRA_SLEEP, 50},
};

static const char *const QUESTION = "？";
static const char *const MUMBLE = "嗯…";
static const char *const SLEEP = "zZ";

uint32_t character_daily_ticks(void)
{
    uint32_t total = 0;
    for (size_t i = 0; i < sizeof DAILY / sizeof DAILY[0]; i++) {
        total += DAILY[i].ticks;
    }
    return total;
}

character_frame_t character_frame(uint32_t tick)
{
    uint32_t t = tick % character_daily_ticks();
    size_t i = 0;
    while (t >= DAILY[i].ticks) {
        t -= DAILY[i++].ticks;
    }
    uint8_t step = DAILY[i].extra == CHARACTER_EXTRA_SLEEP ? (uint8_t)(t / SLEEP_STEP_TICKS % SLEEP_STEPS) : 0;
    return (character_frame_t){DAILY[i].pose, DAILY[i].extra, step};
}

uint32_t character_frame_key(character_frame_t frame)
{
    uint8_t bytes[] = {frame.pose, frame.extra, frame.step};
    return dirty_hash(DIRTY_HASH_START, bytes, sizeof bytes);
}

static int min_int(int a, int b)
{
    return a < b ? a : b;
}

static int max_int(int a, int b)
{
    return a > b ? a : b;
}

rect_t character_bounds(const character_stage_t *stage)
{
    rect_t box = stage->box;
    rect_t extra = stage->extra.w > 0 ? stage->extra : box;
    int x0 = max_int(min_int(box.x, extra.x), stage->clip.x);
    int y0 = max_int(min_int(box.y, extra.y), stage->clip.y);
    int x1 = min_int(max_int(box.x + box.w, extra.x + extra.w), stage->clip.x + stage->clip.w);
    int y1 = min_int(max_int(box.y + box.h, extra.y + extra.h), stage->clip.y + stage->clip.h);
    return (rect_t){x0, y0, max_int(x1 - x0, 0), max_int(y1 - y0, 0)};
}

static void draw_extra(const band_t *band, rect_t area, character_frame_t frame, character_colors_t colors)
{
    if (frame.extra == CHARACTER_EXTRA_QUESTION) {
        int width = draw_text_width(&font_large, QUESTION, area.w);
        draw_text(band, &font_large, QUESTION, area.x + (area.w - width) / 2, area.y, area.x + area.w, colors.ink);
    } else if (frame.extra == CHARACTER_EXTRA_BUBBLE) {
        int width = draw_text_width(&font_text, MUMBLE, area.w - 2 * BUBBLE_PAD);
        rect_t bubble = {area.x + area.w - width - 2 * BUBBLE_PAD, area.y, width + 2 * BUBBLE_PAD,
                         font_text.line_height + 2};
        draw_panel(band, bubble, BUBBLE_RADIUS, BUBBLE_BORDER, colors.ink, colors.paper);
        draw_text(band, &font_text, MUMBLE, bubble.x + BUBBLE_PAD, bubble.y + 1, bubble.x + bubble.w - BUBBLE_PAD,
                  colors.ink);
    } else if (frame.extra == CHARACTER_EXTRA_SLEEP) {
        int width = draw_text_width(&font_text, SLEEP, area.w);
        int top = area.y + (SLEEP_STEPS - 1 - frame.step) * SLEEP_RISE;
        draw_text(band, &font_text, SLEEP, area.x + area.w - width, top, area.x + area.w, colors.ink);
    }
}

void character_draw(const band_t *band, const character_stage_t *stage, character_frame_t frame,
                    character_colors_t colors)
{
    const image_t *image = character_art_image((character_pose_t)frame.pose, (character_size_t)stage->size);
    if (image != NULL) {
        draw_image(band, image, stage->box.x + stage->box.w - image->w, stage->box.y + stage->box.h - image->h,
                   stage->clip);
    }
    if (stage->extra.w > 0) {
        draw_extra(band, stage->extra, frame, colors);
    }
}
