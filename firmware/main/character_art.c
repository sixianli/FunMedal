#include "character_art.h"

#include <string.h>

#include "dirty.h"

#define ART_MAGIC "FMA1"
#define ART_MAGIC_LEN 4
#define ART_HEADER 8
#define ART_ENTRY 8

static image_t images[CHARACTER_POSES][CHARACTER_SIZES];
static int loaded_poses;
static uint32_t fingerprint;

static uint32_t read16(const uint8_t *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8;
}

static uint32_t read32(const uint8_t *p)
{
    return read16(p) | read16(p + 2) << 16;
}

bool character_art_load(const uint8_t *data, size_t size)
{
    memset(images, 0, sizeof images);
    loaded_poses = 0;
    fingerprint = 0;
    if (size < ART_HEADER || memcmp(data, ART_MAGIC, ART_MAGIC_LEN) != 0) {
        return false;
    }
    int poses = data[4];
    int sizes = data[5];
    size_t table_end = ART_HEADER + (size_t)poses * (size_t)sizes * ART_ENTRY;
    if (poses == 0 || poses > CHARACTER_POSES || sizes != CHARACTER_SIZES || table_end > size) {
        return false;
    }
    image_t found[CHARACTER_POSES][CHARACTER_SIZES] = {0};
    for (int pose = 0; pose < poses; pose++) {
        for (int s = 0; s < sizes; s++) {
            const uint8_t *entry = data + ART_HEADER + (size_t)(pose * sizes + s) * ART_ENTRY;
            int w = (int)read16(entry);
            int h = (int)read16(entry + 2);
            size_t offset = read32(entry + 4);
            size_t pixels = (size_t)w * (size_t)h;
            size_t need = pixels * 2 + (pixels + 1) / 2;
            if (pixels == 0 || offset < table_end || offset > size || need > size - offset) {
                return false;
            }
            found[pose][s] = (image_t){w, h, data + offset, data + offset + pixels * 2};
        }
    }
    memcpy(images, found, sizeof images);
    loaded_poses = poses;
    fingerprint = dirty_hash(DIRTY_HASH_START, data, size);
    return true;
}

const image_t *character_art_image(character_pose_t pose, character_size_t size)
{
    if ((int)pose >= loaded_poses || size >= CHARACTER_SIZES) {
        return NULL;
    }
    return &images[pose][size];
}

uint32_t character_art_fingerprint(void)
{
    return fingerprint;
}
