#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "pet_art.h"
#include "prompt.h"
#include "voice_codec.h"

#define SCENE_W 240
#define SCENE_H 320
#define SCENE_PET_SCALE 6
#define SCENE_PET_X 24
#define SCENE_PET_Y 36
#define SCENE_LABEL_Y 250
#define SCENE_MARGIN 10
#define SCENE_TEXT_WIDTH (SCENE_W - 2 * SCENE_MARGIN)

typedef enum {
    SCENE_PAIRING_NONE,
    SCENE_PAIRING_NUMCMP,
    SCENE_PAIRING_PASSKEY,
} scene_pairing_t;

typedef enum {
    SCENE_DECISION_NONE,
    SCENE_DECISION_ALLOW,
    SCENE_DECISION_DENY,
} scene_decision_t;

typedef enum {
    SCENE_VOICE_NONE,
    SCENE_VOICE_LISTENING,
    SCENE_VOICE_TRANSCRIBING,
    SCENE_VOICE_RESULT,
} scene_voice_t;

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
    prompt_t prompt;
    scene_decision_t decision;
    scene_pairing_t pairing;
    uint32_t pair_code;
    scene_voice_t voice;
    voice_result_t voice_result;
    const uint8_t *voice_image;
    scene_link_t link;
    uint16_t link_peer;
} scene_t;

void scene_render_rows(const scene_t *scene, int y0, int rows, uint16_t *out);
uint32_t scene_background_rgb(pet_state_t state, uint8_t frame);
uint32_t scene_text_rgb(pet_state_t state);
const char *scene_state_name(pet_state_t state);
const char *scene_state_label(pet_state_t state);
const char *scene_voice_name(scene_voice_t voice);
const char *scene_link_name(scene_link_t link);
scene_link_t scene_link_for(uint16_t own, uint16_t peer);
bool scene_state_from_bytes(const char *data, size_t len, pet_state_t *state);
bool scene_state_from_name(const char *name, pet_state_t *state);
uint16_t scene_rgb565_wire(uint32_t rgb);
