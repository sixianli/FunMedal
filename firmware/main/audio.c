#include "audio.h"

#include <math.h>
#include <stdatomic.h>

#include "board.h"
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "esp_check.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#define CODEC_I2C_HZ 100000
#define CODEC_TIMEOUT_MS 50
#define CODEC_RESET_MS 20
#define SAMPLE_RATE 16000
#define DMA_DESCRIPTORS 8
#define DMA_FRAMES 320
#define TONE_BLOCK_FRAMES 256
#define TONE_WRITE_TIMEOUT_MS 200
#define TONE_AMPLITUDE 12000
#define TONE_TABLE_BITS 8
#define TONE_TABLE_SIZE (1 << TONE_TABLE_BITS)
#define DAC_VOLUME_REG 0x32

static const char *TAG = "audio";
static i2c_master_dev_handle_t codec;
static i2s_chan_handle_t tx;
static SemaphoreHandle_t speaker_lock;
static atomic_bool ready;
static int16_t tone_table[TONE_TABLE_SIZE];

static esp_err_t codec_write(uint8_t reg, uint8_t value)
{
    uint8_t data[2] = {reg, value};
    return i2c_master_transmit(codec, data, sizeof(data), CODEC_TIMEOUT_MS);
}

static esp_err_t codec_update(uint8_t reg, uint8_t keep_mask, uint8_t bits)
{
    uint8_t value;
    ESP_RETURN_ON_ERROR(i2c_master_transmit_receive(codec, &reg, 1, &value, 1, CODEC_TIMEOUT_MS), TAG, "read %02x",
                        reg);
    return codec_write(reg, (uint8_t)((value & keep_mask) | bits));
}

static esp_err_t codec_init(void)
{
    ESP_RETURN_ON_ERROR(codec_write(0x00, 0x1f), TAG, "reset");
    vTaskDelay(pdMS_TO_TICKS(CODEC_RESET_MS));
    ESP_RETURN_ON_ERROR(codec_write(0x00, 0x00), TAG, "reset release");
    ESP_RETURN_ON_ERROR(codec_write(0x00, 0x80), TAG, "power on");
    ESP_RETURN_ON_ERROR(codec_write(0x01, 0x3f), TAG, "clocks");
    ESP_RETURN_ON_ERROR(codec_update(0x02, 0x07, 0x00), TAG, "pre divider");
    ESP_RETURN_ON_ERROR(codec_write(0x04, 0x10), TAG, "dac oversampling");
    ESP_RETURN_ON_ERROR(codec_write(0x05, 0x00), TAG, "dividers");
    ESP_RETURN_ON_ERROR(codec_update(0x06, 0xc0, 0x03), TAG, "bit clock");
    ESP_RETURN_ON_ERROR(codec_update(0x07, 0xc0, 0x00), TAG, "frame clock high");
    ESP_RETURN_ON_ERROR(codec_write(0x08, 0xff), TAG, "frame clock low");
    ESP_RETURN_ON_ERROR(codec_update(0x00, 0xbf, 0x00), TAG, "slave mode");
    ESP_RETURN_ON_ERROR(codec_write(0x09, 0x0c), TAG, "serial in format");
    ESP_RETURN_ON_ERROR(codec_write(0x0a, 0x0c), TAG, "serial out format");
    ESP_RETURN_ON_ERROR(codec_write(0x0d, 0x01), TAG, "analog power");
    ESP_RETURN_ON_ERROR(codec_write(0x12, 0x00), TAG, "dac power");
    ESP_RETURN_ON_ERROR(codec_write(0x13, 0x10), TAG, "output driver");
    ESP_RETURN_ON_ERROR(codec_write(0x37, 0x08), TAG, "dac equalizer");
    return codec_write(DAC_VOLUME_REG, 0x00);
}

static esp_err_t i2s_init(void)
{
    i2s_chan_config_t chan = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan.dma_desc_num = DMA_DESCRIPTORS;
    chan.dma_frame_num = DMA_FRAMES;
    chan.auto_clear_after_cb = true;
    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan, &tx, NULL), TAG, "i2s channel");
    i2s_std_config_t std = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = BOARD_I2S_MCLK_GPIO,
            .bclk = BOARD_I2S_BCLK_GPIO,
            .ws = BOARD_I2S_WS_GPIO,
            .dout = BOARD_I2S_DOUT_GPIO,
            .din = I2S_GPIO_UNUSED,
        },
    };
    std.clk_cfg.mclk_multiple = I2S_MCLK_MULTIPLE_256;
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(tx, &std), TAG, "i2s std mode");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(tx), TAG, "i2s enable");
    return ESP_OK;
}

static void fill_tone_table(void)
{
    for (int i = 0; i < TONE_TABLE_SIZE; i++) {
        tone_table[i] = (int16_t)(TONE_AMPLITUDE * sinf(2.0f * (float)M_PI * (float)i / (float)TONE_TABLE_SIZE));
    }
}

esp_err_t audio_start(void)
{
    speaker_lock = xSemaphoreCreateMutex();
    ESP_RETURN_ON_FALSE(speaker_lock != NULL, ESP_ERR_NO_MEM, TAG, "speaker lock");
    i2c_master_bus_config_t bus_config = {
        .i2c_port = -1,
        .sda_io_num = BOARD_CODEC_I2C_SDA_GPIO,
        .scl_io_num = BOARD_CODEC_I2C_SCL_GPIO,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    i2c_master_bus_handle_t bus;
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&bus_config, &bus), TAG, "i2c bus");
    i2c_device_config_t device = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = BOARD_CODEC_I2C_ADDRESS,
        .scl_speed_hz = CODEC_I2C_HZ,
    };
    ESP_RETURN_ON_ERROR(i2c_master_bus_add_device(bus, &device, &codec), TAG, "codec device");
    ESP_RETURN_ON_ERROR(i2s_init(), TAG, "i2s");
    ESP_RETURN_ON_ERROR(codec_init(), TAG, "codec");
    fill_tone_table();
    atomic_store(&ready, true);
    return ESP_OK;
}

bool audio_ready(void)
{
    return atomic_load(&ready);
}

esp_err_t audio_tone(int hz, int ms, int volume)
{
    ESP_RETURN_ON_FALSE(atomic_load(&ready), ESP_ERR_INVALID_STATE, TAG, "codec not ready");
    static int16_t block[TONE_BLOCK_FRAMES * 2];
    int total = SAMPLE_RATE * ms / 1000;
    uint32_t step = (uint32_t)(((uint64_t)hz << 32) / SAMPLE_RATE);
    uint32_t phase = 0;
    xSemaphoreTake(speaker_lock, portMAX_DELAY);
    esp_err_t err = codec_write(DAC_VOLUME_REG, (uint8_t)volume);
    for (int done = 0; err == ESP_OK && done < total; done += TONE_BLOCK_FRAMES) {
        for (int i = 0; i < TONE_BLOCK_FRAMES; i++) {
            int16_t value = done + i < total ? tone_table[phase >> (32 - TONE_TABLE_BITS)] : 0;
            phase += step;
            block[2 * i] = value;
            block[2 * i + 1] = value;
        }
        size_t written = 0;
        err = i2s_channel_write(tx, block, sizeof(block), &written, TONE_WRITE_TIMEOUT_MS);
    }
    vTaskDelay(pdMS_TO_TICKS(TONE_WRITE_TIMEOUT_MS));
    codec_write(DAC_VOLUME_REG, 0x00);
    xSemaphoreGive(speaker_lock);
    return err;
}
