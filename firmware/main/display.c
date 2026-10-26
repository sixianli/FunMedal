#include "display.h"

#include <stdatomic.h>

#include "board.h"
#include "driver/ledc.h"
#include "driver/spi_master.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#define STRIP_ROWS 40
#define STRIP_COUNT 2

static const char *TAG = "display";
static esp_lcd_panel_io_handle_t io;
static esp_lcd_panel_handle_t panel;
static uint16_t *strips[STRIP_COUNT];
static int64_t last_render_us;
static int64_t last_total_us;
static SemaphoreHandle_t free_strips;
static SemaphoreHandle_t lock;
static display_config_t current;
static uint32_t generation;
static atomic_int backlight_percent;

static bool on_color_done(esp_lcd_panel_io_handle_t handle, esp_lcd_panel_io_event_data_t *data, void *ctx)
{
    BaseType_t woken = pdFALSE;
    xSemaphoreGiveFromISR(free_strips, &woken);
    return woken == pdTRUE;
}

static esp_err_t create_panel(const display_config_t *config)
{
    if (panel != NULL) {
        esp_lcd_panel_del(panel);
        panel = NULL;
    }
    esp_lcd_panel_dev_config_t panel_config = {
        .reset_gpio_num = -1,
        .rgb_ele_order = config->bgr ? LCD_RGB_ELEMENT_ORDER_BGR : LCD_RGB_ELEMENT_ORDER_RGB,
        .bits_per_pixel = 16,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_st7789(io, &panel_config, &panel), TAG, "new panel");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_reset(panel), TAG, "reset");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(panel), TAG, "init");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_invert_color(panel, config->invert), TAG, "invert");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_mirror(panel, config->mirror_x, config->mirror_y), TAG, "mirror");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_set_gap(panel, config->gap_x, config->gap_y), TAG, "gap");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_disp_on_off(panel, true), TAG, "on");
    current = *config;
    return ESP_OK;
}

static void wait_idle(void)
{
    for (int i = 0; i < STRIP_COUNT; i++) {
        xSemaphoreTake(free_strips, portMAX_DELAY);
    }
    for (int i = 0; i < STRIP_COUNT; i++) {
        xSemaphoreGive(free_strips);
    }
}

void display_backlight(int percent)
{
    if (percent < 0) {
        percent = 0;
    }
    if (percent > 100) {
        percent = 100;
    }
    ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, (uint32_t)(1023 * percent / 100));
    ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);
    atomic_store(&backlight_percent, percent);
}

int display_backlight_percent(void)
{
    return atomic_load(&backlight_percent);
}

esp_err_t display_init(const display_config_t *config)
{
    lock = xSemaphoreCreateMutex();
    free_strips = xSemaphoreCreateCounting(STRIP_COUNT, STRIP_COUNT);
    for (int i = 0; i < STRIP_COUNT; i++) {
        strips[i] = heap_caps_malloc(SCENE_W * STRIP_ROWS * sizeof(uint16_t), MALLOC_CAP_DMA);
        ESP_RETURN_ON_FALSE(strips[i] != NULL, ESP_ERR_NO_MEM, TAG, "strip alloc");
    }

    ledc_timer_config_t timer = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = LEDC_TIMER_10_BIT,
        .timer_num = LEDC_TIMER_0,
        .freq_hz = 5000,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ESP_RETURN_ON_ERROR(ledc_timer_config(&timer), TAG, "ledc timer");
    ledc_channel_config_t channel = {
        .gpio_num = BOARD_LCD_BACKLIGHT_GPIO,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel = LEDC_CHANNEL_0,
        .timer_sel = LEDC_TIMER_0,
        .duty = 0,
    };
    ESP_RETURN_ON_ERROR(ledc_channel_config(&channel), TAG, "ledc channel");

    spi_bus_config_t bus = {
        .sclk_io_num = BOARD_LCD_SCLK_GPIO,
        .mosi_io_num = BOARD_LCD_MOSI_GPIO,
        .miso_io_num = -1,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = SCENE_W * STRIP_ROWS * sizeof(uint16_t),
    };
    ESP_RETURN_ON_ERROR(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO), TAG, "spi bus");
    esp_lcd_panel_io_spi_config_t io_config = {
        .dc_gpio_num = BOARD_LCD_DC_GPIO,
        .cs_gpio_num = BOARD_LCD_CS_GPIO,
        .pclk_hz = BOARD_LCD_PCLK_HZ,
        .lcd_cmd_bits = 8,
        .lcd_param_bits = 8,
        .spi_mode = 0,
        .trans_queue_depth = STRIP_COUNT + 2,
        .on_color_trans_done = on_color_done,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST, &io_config, &io), TAG, "io");
    ESP_RETURN_ON_ERROR(create_panel(config), TAG, "panel");
    display_backlight(100);
    return ESP_OK;
}

esp_err_t display_apply(const display_config_t *config)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    wait_idle();
    display_config_t previous = current;
    esp_err_t err = create_panel(config);
    generation++;
    if (err != ESP_OK && create_panel(&previous) != ESP_OK) {
        ESP_LOGE(TAG, "restoring the previous panel settings failed; drawing will fail until the next lcd command succeeds");
    }
    xSemaphoreGive(lock);
    return err;
}

void display_get_config(display_config_t *config)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    *config = current;
    xSemaphoreGive(lock);
}

void display_last_timing(int64_t *render_us, int64_t *total_us)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    *render_us = last_render_us;
    *total_us = last_total_us;
    xSemaphoreGive(lock);
}

uint32_t display_generation(void)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    uint32_t value = generation;
    xSemaphoreGive(lock);
    return value;
}

void display_invalidate(void)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    generation++;
    xSemaphoreGive(lock);
}

esp_err_t display_draw_spans(const scene_t *scene, const span_t *spans, int count, int64_t *render_us,
                             int64_t *total_us)
{
    esp_err_t err = ESP_OK;
    xSemaphoreTake(lock, portMAX_DELAY);
    int64_t started = esp_timer_get_time();
    int64_t rendering = 0;
    int strip = 0;
    for (int s = 0; s < count && err == ESP_OK; s++) {
        int end = spans[s].y + spans[s].h;
        for (int y = spans[s].y; y < end && err == ESP_OK; y += STRIP_ROWS) {
            int rows = end - y < STRIP_ROWS ? end - y : STRIP_ROWS;
            xSemaphoreTake(free_strips, portMAX_DELAY);
            int64_t before = esp_timer_get_time();
            scene_render_rows(scene, y, rows, strips[strip]);
            rendering += esp_timer_get_time() - before;
            err = esp_lcd_panel_draw_bitmap(panel, 0, y, SCENE_W, y + rows, strips[strip]);
            if (err != ESP_OK) {
                xSemaphoreGive(free_strips);
            }
            strip = (strip + 1) % STRIP_COUNT;
        }
    }
    wait_idle();
    last_render_us = rendering;
    last_total_us = esp_timer_get_time() - started;
    if (render_us != NULL) {
        *render_us = last_render_us;
        *total_us = last_total_us;
    }
    xSemaphoreGive(lock);
    return err;
}
