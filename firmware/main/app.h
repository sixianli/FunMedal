#pragma once

#include <stdint.h>

#include "rows.h"
#include "scene.h"

void app_show_board(void);
void app_set_pattern(void);
void app_set_fill(uint32_t rgb);
void app_get_scene(scene_t *scene);
const char *app_top_state(void);
void app_get_draw_stats(uint32_t *draws, uint32_t *errors, uint32_t *shown_tick);
void app_set_pairing(scene_pairing_t pairing, uint32_t code);
scene_link_t app_set_link(uint16_t peer);
void app_link_in_use(void);
void app_rows_link(void);
void app_rows_heard(rows_kind_t kind, const rows_frame_t *frame);
void app_rows_lose(void);
void app_get_rows(rows_table_t *table, int64_t *now_us);
