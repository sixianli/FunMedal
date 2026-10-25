#include <stdio.h>
#include <string.h>

#include "character.h"
#include "character_art.h"

static int failures;

static void check(int ok, const char *what)
{
    if (!ok) {
        printf("FAIL %s\n", what);
        failures++;
    }
}

typedef struct {
    uint32_t start;
    uint32_t end;
    uint8_t pose;
    uint8_t extra;
} expected_t;

static const expected_t DAILY[] = {
    {0, 50, CHARACTER_LYING, CHARACTER_EXTRA_NONE},
    {50, 53, CHARACTER_TAP_UP, CHARACTER_EXTRA_NONE},
    {53, 56, CHARACTER_TAP_DOWN, CHARACTER_EXTRA_NONE},
    {56, 59, CHARACTER_TAP_UP, CHARACTER_EXTRA_NONE},
    {59, 62, CHARACTER_TAP_DOWN, CHARACTER_EXTRA_NONE},
    {62, 65, CHARACTER_TAP_UP, CHARACTER_EXTRA_NONE},
    {65, 68, CHARACTER_TAP_DOWN, CHARACTER_EXTRA_NONE},
    {68, 98, CHARACTER_TILT, CHARACTER_EXTRA_QUESTION},
    {98, 128, CHARACTER_MUMBLE, CHARACTER_EXTRA_BUBBLE},
    {128, 178, CHARACTER_SLEEPY, CHARACTER_EXTRA_SLEEP},
};

static void test_daily_segments(void)
{
    char what[96];
    int count = (int)(sizeof DAILY / sizeof DAILY[0]);
    check(character_daily_ticks() == 178, "the daily loop lasts 17.8 seconds");
    for (uint32_t loop = 0; loop < 3; loop++) {
        uint32_t base = loop * character_daily_ticks() + (loop == 2 ? 1000 * character_daily_ticks() : 0);
        for (int i = 0; i < count; i++) {
            for (uint32_t t = DAILY[i].start; t < DAILY[i].end; t++) {
                character_frame_t frame = character_frame(base + t);
                snprintf(what, sizeof what, "loop %u tick %u is segment %d", (unsigned)loop, (unsigned)t, i);
                check(frame.pose == DAILY[i].pose && frame.extra == DAILY[i].extra, what);
            }
        }
    }
}

static void test_sleep_drifts(void)
{
    uint8_t steps[50];
    for (uint32_t t = 0; t < 50; t++) {
        steps[t] = character_frame(128 + t).step;
    }
    check(steps[0] == 0 && steps[4] == 0 && steps[5] == 1 && steps[10] == 2 && steps[15] == 3 && steps[20] == 0,
          "zZ rises one step every half second over four steps");
    check(character_frame(0).step == 0 && character_frame(70).step == 0, "only zZ moves inside a segment");
}

static void test_frame_keys(void)
{
    check(character_frame_key(character_frame(0)) == character_frame_key(character_frame(49)),
          "the same pose keeps the same key");
    check(character_frame_key(character_frame(49)) != character_frame_key(character_frame(50)),
          "a new pose changes the key");
    check(character_frame_key(character_frame(128)) != character_frame_key(character_frame(133)),
          "a zZ step changes the key");
}

enum {
    ENTRIES = CHARACTER_POSES * CHARACTER_SIZES,
    TABLE_END = 8 + 8 * ENTRIES,
    BLOB = TABLE_END + 400,
};

static uint8_t blob[BLOB];

static void set_entry(int i, int w, int h, int offset)
{
    uint8_t *entry = blob + 8 + 8 * i;
    entry[0] = (uint8_t)w;
    entry[1] = (uint8_t)(w >> 8);
    entry[2] = (uint8_t)h;
    entry[3] = (uint8_t)(h >> 8);
    entry[4] = (uint8_t)offset;
    entry[5] = (uint8_t)(offset >> 8);
}

static void make_blob(int poses)
{
    memset(blob, 0, sizeof blob);
    memcpy(blob, "FMA1", 4);
    blob[4] = (uint8_t)poses;
    blob[5] = CHARACTER_SIZES;
    for (int i = 0; i < poses * CHARACTER_SIZES; i++) {
        set_entry(i, 1, 1, TABLE_END);
    }
}

static void test_art_rejects_bad_blobs(void)
{
    static const uint8_t junk[] = "FMA1";
    check(!character_art_load(junk, sizeof junk - 1), "a header without a table is rejected");
    make_blob(CHARACTER_POSES);
    set_entry(0, 1, 1, BLOB - 1);
    check(!character_art_load(blob, sizeof blob), "a table pointing past the end is rejected");
    check(character_art_image(CHARACTER_LYING, CHARACTER_SMALL) == NULL, "a rejected blob leaves no image");
    make_blob(CHARACTER_POSES - 1);
    check(!character_art_load(blob, sizeof blob), "a pack missing a pose is rejected");
    character_box_t small = character_art_box(CHARACTER_SMALL);
    character_box_t large = character_art_box(CHARACTER_LARGE);
    check(small.w == 64 && small.h == 74 && large.w == 84 && large.h == 108, "the boxes match gen_assets.py");
    make_blob(CHARACTER_POSES);
    set_entry(0, small.w + 1, 1, TABLE_END);
    check(!character_art_load(blob, sizeof blob), "an image wider than its box is rejected");
    make_blob(CHARACTER_POSES);
    set_entry(1, 1, large.h + 1, TABLE_END);
    check(!character_art_load(blob, sizeof blob), "an image taller than its box is rejected");
    make_blob(CHARACTER_POSES);
    set_entry(0, 26420, 65026, TABLE_END);
    check(!character_art_load(blob, sizeof blob), "an image whose size would overflow is rejected");
    make_blob(CHARACTER_POSES);
    set_entry(0, small.w, 1, TABLE_END);
    check(character_art_load(blob, sizeof blob), "a well formed blob loads");
    check(character_art_image(CHARACTER_LYING, CHARACTER_LARGE) != NULL, "its images are found");
    check(character_art_image(CHARACTER_CHEER, CHARACTER_SMALL) != NULL, "the last pose is found");
}

int main(void)
{
    test_daily_segments();
    test_sleep_drifts();
    test_frame_keys();
    test_art_rejects_bad_blobs();
    if (failures == 0) {
        printf("ALL PASSED\n");
    }
    return failures == 0 ? 0 : 1;
}
