#pragma once

#include "scene.h"

#define APP_FRAME_AUTO (-1)

void app_set_state(pet_state_t state);
void app_set_pattern(void);
void app_set_fill(uint32_t rgb);
void app_set_frame_mode(int frame);
int app_frame_mode(void);
void app_get_scene(scene_t *scene);
void app_get_draw_stats(uint32_t *draws, uint32_t *errors, int *shown_frame);
