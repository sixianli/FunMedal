#include "chime.h"

#include <math.h>

#define SINE_BITS 10
#define SINE_SIZE (1 << SINE_BITS)
#define SINE_PEAK 32767
#define FRACTION_BITS 16
#define ENVELOPE_BITS 30
#define ENVELOPE_ONE (1u << ENVELOPE_BITS)
#define ATTACK_SAMPLES 80
#define FUNDAMENTAL_WEIGHT 10
#define OVERTONE_WEIGHT 3
#define MIX_DIVISOR 52
#define NOTE_G5 784.0
#define NOTE_C6 1046.5
#define SHORT_NOTE 3520
#define SHORT_DECAY 1600
#define LONG_NOTE 9600
#define LONG_DECAY 2880

typedef struct {
    double hz;
    uint32_t samples;
    uint32_t decay;
} note_t;

typedef struct {
    const char *name;
    const note_t *notes;
    int count;
} chime_t;

static const note_t RISING[] = {{NOTE_G5, SHORT_NOTE, SHORT_DECAY}, {NOTE_C6, LONG_NOTE, LONG_DECAY}};
static const note_t FALLING[] = {{NOTE_C6, SHORT_NOTE, SHORT_DECAY}, {NOTE_G5, LONG_NOTE, LONG_DECAY}};

static const chime_t CHIMES[CHIME_KINDS] = {
    [CHIME_WAIT] = {"wait", RISING, 2},
    [CHIME_ERROR] = {"error", FALLING, 2},
};

static int16_t sine[SINE_SIZE + 1];

void chime_init(void)
{
    double turn = 2.0 * acos(-1.0);
    for (int i = 0; i <= SINE_SIZE; i++) {
        sine[i] = (int16_t)lround(SINE_PEAK * sin(turn * (double)i / (double)SINE_SIZE));
    }
}

const char *chime_name(chime_kind_t kind)
{
    return CHIMES[kind].name;
}

uint32_t chime_samples(chime_kind_t kind)
{
    uint32_t total = 0;
    for (int i = 0; i < CHIMES[kind].count; i++) {
        total += CHIMES[kind].notes[i].samples;
    }
    return total;
}

static void start_note(chime_player_t *player)
{
    player->at = 0;
    player->phase = 0;
    player->envelope = ENVELOPE_ONE;
    if (player->note >= CHIMES[player->kind].count) {
        return;
    }
    const note_t *note = &CHIMES[player->kind].notes[player->note];
    player->step = (uint32_t)llround(note->hz * 4294967296.0 / CHIME_RATE);
    player->fade = (uint32_t)llround(exp(-1.0 / (double)note->decay) * ENVELOPE_ONE);
}

void chime_begin(chime_player_t *player, chime_kind_t kind)
{
    player->kind = kind;
    player->note = 0;
    start_note(player);
}

static int32_t sine_at(uint32_t phase)
{
    uint32_t index = phase >> (32 - SINE_BITS);
    int32_t fraction = (int32_t)((phase >> (32 - SINE_BITS - FRACTION_BITS)) & ((1u << FRACTION_BITS) - 1));
    int32_t low = sine[index];
    int32_t high = sine[index + 1];
    return low + (((high - low) * fraction) >> FRACTION_BITS);
}

static int16_t scaled(int32_t value)
{
    return (int16_t)(value >= 0 ? (value + MIX_DIVISOR / 2) / MIX_DIVISOR : -((-value + MIX_DIVISOR / 2) / MIX_DIVISOR));
}

uint32_t chime_render(chime_player_t *player, int16_t *out, uint32_t count)
{
    const chime_t *chime = &CHIMES[player->kind];
    uint32_t made = 0;
    while (made < count && player->note < chime->count) {
        if (player->at == chime->notes[player->note].samples) {
            player->note++;
            start_note(player);
            continue;
        }
        int32_t mix = FUNDAMENTAL_WEIGHT * sine_at(player->phase) + OVERTONE_WEIGHT * sine_at(player->phase * 2);
        int32_t value = (int32_t)(((int64_t)mix * player->envelope) >> ENVELOPE_BITS);
        if (player->at < ATTACK_SAMPLES) {
            value = value * (int32_t)player->at / ATTACK_SAMPLES;
        }
        out[made++] = scaled(value);
        player->phase += player->step;
        player->envelope = (uint32_t)(((uint64_t)player->envelope * player->fade) >> ENVELOPE_BITS);
        player->at++;
    }
    return made;
}
