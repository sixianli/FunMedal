#pragma once

#include <stdbool.h>

#include "esp_err.h"
#include "scene.h"

typedef struct {
    bool invert;
    bool bgr;
    bool mirror_x;
    bool mirror_y;
    int gap_x;
    int gap_y;
} display_config_t;

esp_err_t display_init(const display_config_t *config);
esp_err_t display_apply(const display_config_t *config);
void display_get_config(display_config_t *config);
esp_err_t display_draw(const scene_t *scene);
void display_backlight(int percent);
