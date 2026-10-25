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

static void test_art_rejects_bad_blobs(void)
{
    static const uint8_t junk[] = "FMA1";
    check(!character_art_load(junk, sizeof junk - 1), "a header without a table is rejected");
    uint8_t blob[8 + 8 * CHARACTER_SIZES + 4] = {'F', 'M', 'A', '1', 1, CHARACTER_SIZES, 0, 0};
    for (int s = 0; s < CHARACTER_SIZES; s++) {
        uint8_t *entry = blob + 8 + 8 * s;
        entry[0] = 1;
        entry[2] = 1;
        entry[4] = 8 + 8 * CHARACTER_SIZES;
    }
    blob[8] = 2;
    check(!character_art_load(blob, sizeof blob), "a table pointing past the end is rejected");
    check(character_art_image(CHARACTER_LYING, CHARACTER_SMALL) == NULL, "a rejected blob leaves no image");
    blob[8] = 1;
    check(character_art_load(blob, sizeof blob), "a well formed blob loads");
    check(character_art_image(CHARACTER_LYING, CHARACTER_LARGE) != NULL, "its images are found");
    check(character_art_image(CHARACTER_TILT, CHARACTER_SMALL) == NULL, "poses it does not have are missing");
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
