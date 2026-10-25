#pragma once

#include <stddef.h>
#include <stdint.h>

#define DIRTY_MAX_REGIONS 12
#define DIRTY_HASH_START 2166136261u

typedef struct {
    int16_t y;
    int16_t h;
    uint32_t key;
} region_t;

typedef struct {
    int16_t y;
    int16_t h;
} span_t;

uint32_t dirty_hash(uint32_t hash, const void *data, size_t len);
uint32_t dirty_hash_text(uint32_t hash, const char *text);
int dirty_spans(const region_t *before, int before_count, const region_t *after, int after_count, span_t *spans);
