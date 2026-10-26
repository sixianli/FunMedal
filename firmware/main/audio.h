#pragma once

#include <stdbool.h>

#include "esp_err.h"
#include "chime.h"

esp_err_t audio_start(void);
bool audio_ready(void);
esp_err_t audio_tone(int hz, int ms, int volume);
esp_err_t audio_play(chime_kind_t kind);
void audio_mute(bool on);
bool audio_muted(void);
