#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "chime.h"

static int failures;

static void check(int ok, const char *what)
{
    if (!ok) {
        printf("FAIL %s\n", what);
        failures++;
    }
}

static int16_t *render_in_blocks(chime_kind_t kind, uint32_t block, uint32_t *total)
{
    uint32_t room = chime_samples(kind) + block;
    int16_t *out = calloc(room, sizeof(int16_t));
    chime_player_t player;
    chime_begin(&player, kind);
    *total = 0;
    uint32_t got;
    while ((got = chime_render(&player, out + *total, block)) > 0) {
        *total += got;
        if (*total > room - block) {
            break;
        }
    }
    return out;
}

static void check_kind(chime_kind_t kind, const char *folder)
{
    char what[128];
    uint32_t total = 0;
    uint32_t odd_total = 0;
    int16_t *samples = render_in_blocks(kind, 256, &total);
    int16_t *odd = render_in_blocks(kind, 37, &odd_total);
    snprintf(what, sizeof(what), "%s renders %u samples", chime_name(kind), (unsigned)chime_samples(kind));
    check(total == chime_samples(kind) && odd_total == total, what);
    snprintf(what, sizeof(what), "%s comes out the same in any block size", chime_name(kind));
    check(memcmp(samples, odd, total * sizeof(int16_t)) == 0, what);
    chime_player_t player;
    chime_begin(&player, kind);
    int16_t scratch[16];
    while (chime_render(&player, scratch, 16) > 0) {
    }
    snprintf(what, sizeof(what), "%s stays finished", chime_name(kind));
    check(chime_render(&player, scratch, 16) == 0, what);
    char path[512];
    snprintf(path, sizeof(path), "%s/%s.pcm", folder, chime_name(kind));
    FILE *file = fopen(path, "wb");
    check(file != NULL && fwrite(samples, sizeof(int16_t), total, file) == total, path);
    if (file != NULL) {
        fclose(file);
    }
    free(samples);
    free(odd);
}

int main(int argc, char **argv)
{
    if (argc != 2) {
        printf("usage: test_chime <output folder>\n");
        return 2;
    }
    chime_init();
    check(strcmp(chime_name(CHIME_WAIT), "wait") == 0 && strcmp(chime_name(CHIME_ERROR), "error") == 0, "names");
    check_kind(CHIME_WAIT, argv[1]);
    check_kind(CHIME_ERROR, argv[1]);
    if (failures == 0) {
        printf("ALL PASSED\n");
    }
    return failures == 0 ? 0 : 1;
}
