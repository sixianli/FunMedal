#pragma once

#include <stdint.h>

#define PET_SIZE 32
#define PET_FRAMES 2
#define PET_PALETTE_SIZE 13

typedef enum {
    PET_IDLE,
    PET_WORKING,
    PET_WAITING,
    PET_DONE,
    PET_ERROR,
    PET_STATE_COUNT,
} pet_state_t;

extern const uint32_t pet_palette[PET_PALETTE_SIZE];
extern const uint8_t pet_sprites[PET_STATE_COUNT][PET_FRAMES][PET_SIZE][PET_SIZE];
