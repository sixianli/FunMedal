#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "draw.h"

typedef enum {
    CHARACTER_LYING,
    CHARACTER_SLEEPY,
    CHARACTER_TAP_UP,
    CHARACTER_TAP_DOWN,
    CHARACTER_TILT,
    CHARACTER_MUMBLE,
    CHARACTER_CALL,
    CHARACTER_WORRY,
    CHARACTER_WAVE,
    CHARACTER_CHEER,
    CHARACTER_POSES,
} character_pose_t;

typedef enum {
    CHARACTER_SMALL,
    CHARACTER_LARGE,
    CHARACTER_HOME_112,
    CHARACTER_HOME_145,
    CHARACTER_HOME_200,
    CHARACTER_SIZES,
} character_size_t;

typedef struct {
    int w;
    int h;
} character_box_t;

character_box_t character_art_box(character_size_t size);
bool character_art_init(void);
bool character_art_load(const uint8_t *data, size_t size);
const image_t *character_art_image(character_pose_t pose, character_size_t size);
uint32_t character_art_fingerprint(void);
