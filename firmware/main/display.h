#pragma once

#include <stdbool.h>
#include <stdint.h>

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
esp_err_t display_draw_spans(const scene_t *scene, const span_t *spans, int count, int64_t *render_us,
                             int64_t *total_us);
uint32_t display_generation(void);
void display_invalidate(void);
void display_backlight(int percent);
int display_backlight_percent(void);
void display_last_timing(int64_t *render_us, int64_t *total_us);
