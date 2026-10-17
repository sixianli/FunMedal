#include "voice_codec.h"

#include <string.h>

static const int16_t STEP_SIZES[89] = {
    7,     8,     9,     10,    11,    12,    13,    14,    16,    17,    19,    21,    23,    25,    28,
    31,    34,    37,    41,    45,    50,    55,    60,    66,    73,    80,    88,    97,    107,   118,
    130,   143,   157,   173,   190,   209,   230,   253,   279,   307,   337,   371,   408,   449,   494,
    544,   598,   658,   724,   796,   876,   963,   1060,  1166,  1282,  1411,  1552,  1707,  1878,  2066,
    2272,  2499,  2749,  3024,  3327,  3660,  4026,  4428,  4871,  5358,  5894,  6484,  7132,  7845,  8630,
    9493,  10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
};
static const int8_t INDEX_CHANGE[8] = {-1, -1, -1, -1, 2, 4, 6, 8};
static const char *const RESULT_NAMES[] = {
    [VOICE_RESULT_QUEUED] = "queued",
    [VOICE_RESULT_SUBMITTED] = "submitted",
    [VOICE_RESULT_EMPTY] = "empty",
    [VOICE_RESULT_NO_SESSION] = "no_session",
    [VOICE_RESULT_DENIED] = "denied",
    [VOICE_RESULT_CLEARED] = "cleared",
    [VOICE_RESULT_STOPPED] = "stopped",
    [VOICE_RESULT_REFUSED] = "refused",
    [VOICE_RESULT_OFFLINE] = "offline",
    [VOICE_RESULT_TIMEOUT] = "timeout",
};

static int clamp(int value, int low, int high)
{
    return value < low ? low : value > high ? high : value;
}

static uint8_t encode_sample(adpcm_state_t *state, int16_t sample)
{
    int step = STEP_SIZES[state->index];
    int diff = sample - state->predictor;
    uint8_t code = 0;
    if (diff < 0) {
        code = 8;
        diff = -diff;
    }
    int change = step >> 3;
    if (diff >= step) {
        code |= 4;
        diff -= step;
        change += step;
    }
    if (diff >= step >> 1) {
        code |= 2;
        diff -= step >> 1;
        change += step >> 1;
    }
    if (diff >= step >> 2) {
        code |= 1;
        change += step >> 2;
    }
    state->predictor = (int16_t)clamp(state->predictor + ((code & 8) ? -change : change), INT16_MIN, INT16_MAX);
    state->index = (uint8_t)clamp(state->index + INDEX_CHANGE[code & 7], 0, 88);
    return code;
}

size_t voice_start_packet(uint8_t clip, uint8_t *out)
{
    out[0] = VOICE_PACKET_START;
    out[1] = clip;
    return VOICE_START_PACKET;
}

size_t voice_audio_packet(uint8_t clip, uint16_t sequence, adpcm_state_t *state, const int16_t *samples,
                          uint8_t *out)
{
    out[0] = VOICE_PACKET_AUDIO;
    out[1] = clip;
    out[2] = (uint8_t)(sequence & 0xff);
    out[3] = (uint8_t)(sequence >> 8);
    out[4] = (uint8_t)((uint16_t)state->predictor & 0xff);
    out[5] = (uint8_t)((uint16_t)state->predictor >> 8);
    out[6] = state->index;
    for (int i = 0; i < VOICE_SAMPLES_PER_PACKET / 2; i++) {
        uint8_t low = encode_sample(state, samples[2 * i]);
        uint8_t high = encode_sample(state, samples[2 * i + 1]);
        out[VOICE_AUDIO_HEADER + i] = (uint8_t)(low | (high << 4));
    }
    return VOICE_AUDIO_PACKET;
}

size_t voice_end_packet(uint8_t clip, uint16_t packets, voice_end_reason_t reason, uint8_t *out)
{
    out[0] = VOICE_PACKET_END;
    out[1] = clip;
    out[2] = (uint8_t)(packets & 0xff);
    out[3] = (uint8_t)(packets >> 8);
    out[4] = (uint8_t)reason;
    return VOICE_END_PACKET;
}

static bool parse_clip(const char *data, size_t len, uint8_t *clip)
{
    if (len == 0 || len > 3) {
        return false;
    }
    unsigned value = 0;
    for (size_t i = 0; i < len; i++) {
        if (data[i] < '0' || data[i] > '9') {
            return false;
        }
        value = value * 10 + (unsigned)(data[i] - '0');
    }
    if (value > UINT8_MAX) {
        return false;
    }
    *clip = (uint8_t)value;
    return true;
}

bool voice_result_parse(const char *data, size_t len, uint8_t *clip, voice_result_t *result)
{
    const char *tab = memchr(data, '\t', len);
    if (tab == NULL || !parse_clip(data, (size_t)(tab - data), clip)) {
        return false;
    }
    const char *word = tab + 1;
    size_t word_len = len - (size_t)(word - data);
    for (int i = VOICE_RESULT_QUEUED; i <= VOICE_RESULT_REFUSED; i++) {
        if (strlen(RESULT_NAMES[i]) == word_len && memcmp(word, RESULT_NAMES[i], word_len) == 0) {
            *result = (voice_result_t)i;
            return true;
        }
    }
    return false;
}

const char *voice_result_name(voice_result_t result)
{
    return RESULT_NAMES[result];
}

void voice_image_reset(voice_image_t *image)
{
    image->open = false;
    image->next = 0;
}

bool voice_image_write(voice_image_t *image, const uint8_t *data, size_t len)
{
    if (len <= VOICE_IMAGE_HEADER) {
        voice_image_reset(image);
        return false;
    }
    uint8_t clip = data[0];
    uint16_t offset = (uint16_t)(data[1] | (data[2] << 8));
    size_t size = len - VOICE_IMAGE_HEADER;
    if (offset == 0) {
        image->open = true;
        image->clip = clip;
        image->next = 0;
    }
    bool follows = image->open && image->clip == clip && image->next == offset;
    if (!follows || offset + size > VOICE_IMAGE_BYTES) {
        voice_image_reset(image);
        return false;
    }
    memcpy(image->pixels + offset, data + VOICE_IMAGE_HEADER, size);
    image->next = (uint16_t)(offset + size);
    return true;
}

bool voice_image_complete(const voice_image_t *image, uint8_t clip)
{
    return image->open && image->clip == clip && image->next == VOICE_IMAGE_BYTES;
}

bool voice_image_pixel(const uint8_t *pixels, int x, int y)
{
    return (pixels[y * VOICE_IMAGE_STRIDE + x / 8] >> (7 - x % 8)) & 1;
}
