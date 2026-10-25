#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "dirty.h"
#include "pet_art.h"
#include "theme.h"

#define SCENE_W 240
#define SCENE_H 320
#define SCENE_PET_SCALE 6
#define SCENE_PET_X 24
#define SCENE_PET_Y 36
#define SCENE_LABEL_Y 250

typedef enum {
    SCENE_PAIRING_NONE,
    SCENE_PAIRING_NUMCMP,
    SCENE_PAIRING_PASSKEY,
} scene_pairing_t;

typedef enum {
    SCENE_LINK_NONE,
    SCENE_LINK_MATCH,
    SCENE_LINK_UPDATE_FIRMWARE,
    SCENE_LINK_UPDATE_BRIDGE,
} scene_link_t;

typedef struct {
    pet_state_t state;
    uint8_t frame;
    bool test_pattern;
    bool fill;
    uint32_t fill_rgb;
    scene_pairing_t pairing;
    uint32_t pair_code;
    scene_link_t link;
    uint16_t link_peer;
    bool sessions;
    uint8_t theme;
    screen_t screen;
} scene_t;

void scene_render_rows(const scene_t *scene, int y0, int rows, uint16_t *out);
int scene_regions(const scene_t *scene, region_t *out);
uint32_t scene_background_rgb(pet_state_t state, uint8_t frame);
uint32_t scene_text_rgb(pet_state_t state);
const char *scene_state_name(pet_state_t state);
const char *scene_state_label(pet_state_t state);
const char *scene_link_name(scene_link_t link);
scene_link_t scene_link_for(uint16_t own, uint16_t peer);
bool scene_state_from_name(const char *name, pet_state_t *state);
