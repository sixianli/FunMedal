#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "dirty.h"
#include "theme.h"

#define SCREEN_THEME_COUNT 4
#define SCREEN_AGE_MAX 24

const theme_t *screen_theme(uint8_t index);
uint8_t screen_next_theme(uint8_t index);
uint8_t screen_stored_theme(bool found, uint8_t value);
void screen_from_rows(screen_t *screen, const rows_table_t *table, int64_t now_us, uint32_t tick);
void screen_render(const theme_t *theme, const screen_t *screen, const band_t *band);
int screen_regions(uint8_t theme, const screen_t *screen, region_t *out);
const char *screen_age(uint16_t minutes, char *out, size_t size);
