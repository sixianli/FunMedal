#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "dirty.h"
#include "theme.h"

#define SCENE_W 240
#define SCENE_H 320

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
    bool test_pattern;
    bool fill;
    uint32_t fill_rgb;
    scene_pairing_t pairing;
    uint32_t pair_code;
    scene_link_t link;
    uint16_t link_peer;
    uint8_t theme;
    screen_t screen;
} scene_t;

void scene_render_rows(const scene_t *scene, int y0, int rows, uint16_t *out);
int scene_regions(const scene_t *scene, region_t *out);
const char *scene_link_name(scene_link_t link);
scene_link_t scene_link_for(uint16_t own, uint16_t peer);
