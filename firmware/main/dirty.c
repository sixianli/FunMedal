#include "dirty.h"

#include <stdbool.h>
#include <string.h>

#define FNV_PRIME 16777619u

uint32_t dirty_hash(uint32_t hash, const void *data, size_t len)
{
    const uint8_t *bytes = data;
    for (size_t i = 0; i < len; i++) {
        hash = (hash ^ bytes[i]) * FNV_PRIME;
    }
    return hash;
}

uint32_t dirty_hash_text(uint32_t hash, const char *text)
{
    return dirty_hash(hash, text, strlen(text) + 1);
}

static bool unchanged(const region_t *region, const region_t *before, int before_count)
{
    for (int i = 0; i < before_count; i++) {
        if (before[i].y == region->y && before[i].h == region->h && before[i].key == region->key) {
            return true;
        }
    }
    return false;
}

int dirty_spans(const region_t *before, int before_count, const region_t *after, int after_count, span_t *spans)
{
    int count = 0;
    for (int i = 0; i < after_count; i++) {
        if (unchanged(&after[i], before, before_count)) {
            continue;
        }
        if (count > 0 && spans[count - 1].y + spans[count - 1].h == after[i].y) {
            spans[count - 1].h = (int16_t)(spans[count - 1].h + after[i].h);
        } else {
            spans[count++] = (span_t){after[i].y, after[i].h};
        }
    }
    return count;
}
