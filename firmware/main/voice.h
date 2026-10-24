#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "voice_codec.h"

#define VOICE_GAIN_MAX 7

typedef bool (*voice_packet_sink_t)(const uint8_t *packet, size_t size, void *context);

typedef struct {
    uint32_t samples;
    uint64_t squares_used;
    uint64_t squares_other;
    int peak_used;
} voice_capture_stats_t;

esp_err_t voice_start(void);
bool voice_ready(void);
esp_err_t voice_tone(int hz, int ms, int volume);
void voice_press(void);
void voice_release(void);
void voice_on_result(uint8_t clip, voice_result_t result, const uint8_t *pixels);
bool voice_capture(int packets, int gain, voice_packet_sink_t sink, void *context, voice_capture_stats_t *stats);
