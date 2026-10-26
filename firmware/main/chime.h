#pragma once

#include <stdint.h>

#define CHIME_RATE 16000

typedef enum {
    CHIME_WAIT,
    CHIME_ERROR,
    CHIME_KINDS,
} chime_kind_t;

typedef struct {
    chime_kind_t kind;
    int note;
    uint32_t at;
    uint32_t phase;
    uint32_t step;
    uint32_t envelope;
    uint32_t fade;
} chime_player_t;

void chime_init(void);
const char *chime_name(chime_kind_t kind);
uint32_t chime_samples(chime_kind_t kind);
void chime_begin(chime_player_t *player, chime_kind_t kind);
uint32_t chime_render(chime_player_t *player, int16_t *out, uint32_t count);
