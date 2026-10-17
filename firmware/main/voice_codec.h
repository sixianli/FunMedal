#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define VOICE_SAMPLE_RATE 16000
#define VOICE_SAMPLES_PER_PACKET 320
#define VOICE_AUDIO_HEADER 7
#define VOICE_AUDIO_PACKET (VOICE_AUDIO_HEADER + VOICE_SAMPLES_PER_PACKET / 2)
#define VOICE_START_PACKET 2
#define VOICE_END_PACKET 5
#define VOICE_IMAGE_W 220
#define VOICE_IMAGE_H 100
#define VOICE_IMAGE_STRIDE ((VOICE_IMAGE_W + 7) / 8)
#define VOICE_IMAGE_BYTES (VOICE_IMAGE_STRIDE * VOICE_IMAGE_H)
#define VOICE_IMAGE_HEADER 3
#define VOICE_RESULT_VALUE_MAX 16

typedef enum {
    VOICE_PACKET_START = 1,
    VOICE_PACKET_AUDIO = 2,
    VOICE_PACKET_END = 3,
} voice_packet_type_t;

typedef enum {
    VOICE_END_RELEASE = 0,
    VOICE_END_LIMIT = 1,
} voice_end_reason_t;

typedef enum {
    VOICE_RESULT_SENT,
    VOICE_RESULT_EMPTY,
    VOICE_RESULT_NO_SESSION,
    VOICE_RESULT_DENIED,
    VOICE_RESULT_OFFLINE,
    VOICE_RESULT_TIMEOUT,
} voice_result_t;

typedef struct {
    int16_t predictor;
    uint8_t index;
} adpcm_state_t;

typedef struct {
    bool open;
    uint8_t clip;
    uint16_t next;
    uint8_t pixels[VOICE_IMAGE_BYTES];
} voice_image_t;

size_t voice_start_packet(uint8_t clip, uint8_t *out);
size_t voice_audio_packet(uint8_t clip, uint16_t sequence, adpcm_state_t *state, const int16_t *samples,
                          uint8_t *out);
size_t voice_end_packet(uint8_t clip, uint16_t packets, voice_end_reason_t reason, uint8_t *out);
bool voice_result_parse(const char *data, size_t len, uint8_t *clip, voice_result_t *result);
const char *voice_result_name(voice_result_t result);
void voice_image_reset(voice_image_t *image);
bool voice_image_write(voice_image_t *image, const uint8_t *data, size_t len);
bool voice_image_complete(const voice_image_t *image, uint8_t clip);
bool voice_image_pixel(const uint8_t *pixels, int x, int y);
