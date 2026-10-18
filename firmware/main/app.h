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
void app_set_prompt(const prompt_t *prompt);
void app_set_pairing(scene_pairing_t pairing, uint32_t code);
void app_set_voice(scene_voice_t voice, voice_result_t result);
void app_set_voice_image(const uint8_t *pixels);
scene_link_t app_set_link(uint16_t peer);
void app_link_in_use(void);
