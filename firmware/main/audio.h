#pragma once

#include <stdbool.h>

#include "esp_err.h"

esp_err_t audio_start(void);
bool audio_ready(void);
esp_err_t audio_tone(int hz, int ms, int volume);
