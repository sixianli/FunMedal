#pragma once

#include <stddef.h>
#include <stdint.h>

#include "dirty.h"
#include "theme.h"

#define SCREEN_THEME_COUNT 1
#define SCREEN_AGE_MAX 24

const theme_t *screen_theme(uint8_t index);
void screen_from_rows(screen_t *screen, const rows_table_t *table, int64_t now_us, uint32_t tick);
void screen_render(const theme_t *theme, const screen_t *screen, const band_t *band);
int screen_regions(uint8_t theme, const screen_t *screen, region_t *out);
const char *screen_age(uint16_t minutes, char *out, size_t size);
