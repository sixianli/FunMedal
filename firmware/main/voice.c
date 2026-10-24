#include "voice.h"

#include <math.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

#include "app.h"
#include "ble_link.h"
#include "board.h"
#include "console.h"
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "esp_check.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#define CODEC_I2C_HZ 100000
#define CODEC_TIMEOUT_MS 50
#define CODEC_RESET_MS 20
#define DEFAULT_GAIN 4
#define DMA_DESCRIPTORS 8
#define SHORT_PACKETS 25
#define LIMIT_PACKETS 750
#define READ_TIMEOUT_MS 100
#define RESULT_WAIT_MS 15000
#define RESULT_SHOW_MS 4000
#define VOICE_TASK_STACK 4096
#define VOICE_TASK_PRIORITY 5
#define NOTIFY_PRESS (1u << 0)
#define NOTIFY_RESULT (1u << 1)
#define NOTIFY_FOLLOW (1u << 2)
#define NO_CLIP (-1)
#define NO_FOLLOW (-1)
#define TONE_BLOCK_FRAMES 256
#define TONE_WRITE_TIMEOUT_MS 200
#define TONE_AMPLITUDE 12000
#define TONE_TABLE_BITS 8
#define TONE_TABLE_SIZE (1 << TONE_TABLE_BITS)
#define DAC_VOLUME_REG 0x32

static const char *TAG = "voice";
static i2c_master_dev_handle_t codec;
static i2s_chan_handle_t rx;
static i2s_chan_handle_t tx;
static SemaphoreHandle_t mic_lock;
static TaskHandle_t voice_task_handle;
static atomic_bool held;
static atomic_bool ready;
static atomic_int awaited_clip = NO_CLIP;
static atomic_int arrived_result;
static atomic_int followed_clip = NO_CLIP;
static atomic_int follow_up = NO_FOLLOW;
static uint8_t next_clip;

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

static esp_err_t codec_set_gain(int gain)
{
    return codec_write(0x16, (uint8_t)gain);
}

static esp_err_t codec_init(void)
{
    ESP_RETURN_ON_ERROR(codec_write(0x00, 0x1f), TAG, "reset");
    vTaskDelay(pdMS_TO_TICKS(CODEC_RESET_MS));
    ESP_RETURN_ON_ERROR(codec_write(0x00, 0x00), TAG, "reset release");
    ESP_RETURN_ON_ERROR(codec_write(0x00, 0x80), TAG, "power on");
    ESP_RETURN_ON_ERROR(codec_write(0x01, 0x3f), TAG, "clocks");
    ESP_RETURN_ON_ERROR(codec_update(0x02, 0x07, 0x00), TAG, "pre divider");
    ESP_RETURN_ON_ERROR(codec_write(0x03, 0x10), TAG, "adc oversampling");
    ESP_RETURN_ON_ERROR(codec_write(0x04, 0x10), TAG, "dac oversampling");
    ESP_RETURN_ON_ERROR(codec_write(0x05, 0x00), TAG, "dividers");
    ESP_RETURN_ON_ERROR(codec_update(0x06, 0xc0, 0x03), TAG, "bit clock");
    ESP_RETURN_ON_ERROR(codec_update(0x07, 0xc0, 0x00), TAG, "frame clock high");
    ESP_RETURN_ON_ERROR(codec_write(0x08, 0xff), TAG, "frame clock low");
    ESP_RETURN_ON_ERROR(codec_update(0x00, 0xbf, 0x00), TAG, "slave mode");
    ESP_RETURN_ON_ERROR(codec_write(0x09, 0x0c), TAG, "serial in format");
    ESP_RETURN_ON_ERROR(codec_write(0x0a, 0x0c), TAG, "serial out format");
    ESP_RETURN_ON_ERROR(codec_write(0x0d, 0x01), TAG, "analog power");
    ESP_RETURN_ON_ERROR(codec_write(0x0e, 0x02), TAG, "adc power");
    ESP_RETURN_ON_ERROR(codec_write(0x12, 0x00), TAG, "dac power");
    ESP_RETURN_ON_ERROR(codec_write(0x13, 0x10), TAG, "output driver");
    ESP_RETURN_ON_ERROR(codec_write(0x1c, 0x6a), TAG, "adc equalizer");
    ESP_RETURN_ON_ERROR(codec_write(0x37, 0x08), TAG, "dac equalizer");
    ESP_RETURN_ON_ERROR(codec_write(0x32, 0x00), TAG, "dac mute");
    ESP_RETURN_ON_ERROR(codec_write(0x17, 0xc8), TAG, "adc volume");
    ESP_RETURN_ON_ERROR(codec_write(0x14, 0x1a), TAG, "analog mic");
    return codec_set_gain(DEFAULT_GAIN);
}

static esp_err_t i2s_init(void)
{
    i2s_chan_config_t chan = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan.dma_desc_num = DMA_DESCRIPTORS;
    chan.dma_frame_num = VOICE_SAMPLES_PER_PACKET;
    chan.auto_clear_after_cb = true;
    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan, &tx, &rx), TAG, "i2s channel");
    i2s_std_config_t std = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(VOICE_SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = BOARD_I2S_MCLK_GPIO,
            .bclk = BOARD_I2S_BCLK_GPIO,
            .ws = BOARD_I2S_WS_GPIO,
            .dout = BOARD_I2S_DOUT_GPIO,
            .din = BOARD_I2S_DIN_GPIO,
        },
    };
    std.clk_cfg.mclk_multiple = I2S_MCLK_MULTIPLE_256;
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(tx, &std), TAG, "i2s tx std mode");
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(rx, &std), TAG, "i2s std mode");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(rx), TAG, "i2s enable");
    return ESP_OK;
}

static int16_t tone_table[TONE_TABLE_SIZE];
static bool tone_table_ready;

static void fill_tone_table(void)
{
    if (tone_table_ready) {
        return;
    }
    for (int i = 0; i < TONE_TABLE_SIZE; i++) {
        tone_table[i] = (int16_t)(TONE_AMPLITUDE * sinf(2.0f * (float)M_PI * (float)i / (float)TONE_TABLE_SIZE));
    }
    tone_table_ready = true;
}

esp_err_t voice_tone(int hz, int ms, int volume)
{
    ESP_RETURN_ON_FALSE(atomic_load(&ready), ESP_ERR_INVALID_STATE, TAG, "codec not ready");
    static int16_t block[TONE_BLOCK_FRAMES * 2];
    int total = VOICE_SAMPLE_RATE * ms / 1000;
    uint32_t step = (uint32_t)(((uint64_t)hz << 32) / VOICE_SAMPLE_RATE);
    uint32_t phase = 0;
    xSemaphoreTake(mic_lock, portMAX_DELAY);
    fill_tone_table();
    esp_err_t err = codec_write(DAC_VOLUME_REG, (uint8_t)volume);
    if (err == ESP_OK) {
        err = i2s_channel_enable(tx);
    }
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
    i2s_channel_disable(tx);
    codec_write(DAC_VOLUME_REG, 0x00);
    xSemaphoreGive(mic_lock);
    return err;
}

static bool read_packet(int16_t *used, voice_capture_stats_t *stats)
{
    static int16_t frames[VOICE_SAMPLES_PER_PACKET * 2];
    size_t got = 0;
    if (i2s_channel_read(rx, frames, sizeof(frames), &got, READ_TIMEOUT_MS) != ESP_OK || got != sizeof(frames)) {
        return false;
    }
    for (int i = 0; i < VOICE_SAMPLES_PER_PACKET; i++) {
        used[i] = frames[2 * i];
        if (stats != NULL) {
            int other = frames[2 * i + 1];
            stats->squares_used += (uint64_t)((int32_t)used[i] * used[i]);
            stats->squares_other += (uint64_t)((int32_t)other * other);
            if (abs(used[i]) > stats->peak_used) {
                stats->peak_used = abs(used[i]);
            }
        }
    }
    if (stats != NULL) {
        stats->samples += VOICE_SAMPLES_PER_PACKET;
    }
    return true;
}

static void drain_stale_audio(void)
{
    static int16_t frames[VOICE_SAMPLES_PER_PACKET * 2];
    size_t got = 0;
    for (int i = 0; i < DMA_DESCRIPTORS; i++) {
        if (i2s_channel_read(rx, frames, sizeof(frames), &got, 0) != ESP_OK || got == 0) {
            return;
        }
    }
}

static int record(uint8_t clip, uint32_t *dropped, voice_end_reason_t *reason)
{
    static int16_t samples[VOICE_SAMPLES_PER_PACKET];
    static uint8_t early[SHORT_PACKETS][VOICE_AUDIO_PACKET];
    static uint8_t packet[VOICE_AUDIO_PACKET];
    adpcm_state_t state = {0, 0};
    int sequence = 0;
    *dropped = 0;
    *reason = VOICE_END_RELEASE;
    drain_stale_audio();
    while (atomic_load(&held)) {
        if (sequence == LIMIT_PACKETS) {
            *reason = VOICE_END_LIMIT;
            break;
        }
        if (!read_packet(samples, NULL)) {
            continue;
        }
        uint8_t *out = sequence < SHORT_PACKETS ? early[sequence] : packet;
        voice_audio_packet(clip, (uint16_t)sequence, &state, samples, out);
        sequence++;
        if (sequence == SHORT_PACKETS) {
            uint8_t start[VOICE_START_PACKET];
            *dropped += !ble_link_send_audio(start, voice_start_packet(clip, start));
            console_event("VOICE start %u\n", clip);
            for (int i = 0; i < SHORT_PACKETS; i++) {
                *dropped += !ble_link_send_audio(early[i], VOICE_AUDIO_PACKET);
            }
        } else if (sequence > SHORT_PACKETS) {
            *dropped += !ble_link_send_audio(packet, VOICE_AUDIO_PACKET);
        }
    }
    return sequence;
}

static uint32_t wait_for(uint32_t wanted, uint32_t limit_ms)
{
    TickType_t start = xTaskGetTickCount();
    TickType_t limit = pdMS_TO_TICKS(limit_ms);
    for (;;) {
        TickType_t waited = xTaskGetTickCount() - start;
        uint32_t bits = 0;
        if (waited >= limit || !xTaskNotifyWait(0, UINT32_MAX, &bits, limit - waited)) {
            return 0;
        }
        if (bits & wanted) {
            return bits & wanted;
        }
    }
}

static int take_follow_up(uint8_t clip)
{
    int value = atomic_exchange(&follow_up, NO_FOLLOW);
    return value != NO_FOLLOW && (value >> 8) == clip ? value & 0xff : NO_FOLLOW;
}

static uint32_t show_result(uint8_t clip, voice_result_t result)
{
    for (;;) {
        console_event("VOICE result %u %s\n", clip, voice_result_name(result));
        if (result != VOICE_RESULT_QUEUED) {
            atomic_store(&followed_clip, NO_CLIP);
        }
        app_set_voice(SCENE_VOICE_RESULT, result);
        TickType_t shown = xTaskGetTickCount();
        TickType_t limit = pdMS_TO_TICKS(RESULT_SHOW_MS);
        uint32_t bits = 0;
        int next = take_follow_up(clip);
        while (next == NO_FOLLOW) {
            TickType_t waited = xTaskGetTickCount() - shown;
            if (waited >= limit) {
                break;
            }
            bits = wait_for(NOTIFY_PRESS | NOTIFY_FOLLOW, pdTICKS_TO_MS(limit - waited));
            if (bits == 0 || (bits & NOTIFY_PRESS)) {
                break;
            }
            next = take_follow_up(clip);
        }
        if (next == NO_FOLLOW) {
            app_set_voice(SCENE_VOICE_NONE, result);
            return bits & NOTIFY_PRESS;
        }
        result = (voice_result_t)next;
    }
}

static voice_result_t await_result(void)
{
    bool arrived = wait_for(NOTIFY_RESULT, RESULT_WAIT_MS) != 0;
    atomic_store(&awaited_clip, NO_CLIP);
    return arrived ? (voice_result_t)atomic_load(&arrived_result) : VOICE_RESULT_TIMEOUT;
}

static uint32_t run_clip(void)
{
    uint8_t clip = next_clip++;
    atomic_store(&followed_clip, NO_CLIP);
    atomic_store(&follow_up, NO_FOLLOW);
    app_set_voice_image(NULL);
    if (!ble_link_ready()) {
        return show_result(clip, VOICE_RESULT_OFFLINE);
    }
    app_set_voice(SCENE_VOICE_LISTENING, VOICE_RESULT_QUEUED);
    xSemaphoreTake(mic_lock, portMAX_DELAY);
    uint32_t dropped;
    voice_end_reason_t reason;
    int packets = record(clip, &dropped, &reason);
    xSemaphoreGive(mic_lock);
    if (packets < SHORT_PACKETS) {
        console_event("VOICE short\n");
        app_set_voice(SCENE_VOICE_NONE, VOICE_RESULT_QUEUED);
        return 0;
    }
    if (!ble_link_ready()) {
        console_event("VOICE end %u packets=%d dropped=%lu reason=offline\n", clip, packets, (unsigned long)dropped);
        return show_result(clip, VOICE_RESULT_OFFLINE);
    }
    xTaskNotifyStateClear(NULL);
    ulTaskNotifyValueClear(NULL, UINT32_MAX);
    atomic_store(&awaited_clip, clip);
    uint8_t end[VOICE_END_PACKET];
    dropped += !ble_link_send_audio(end, voice_end_packet(clip, (uint16_t)packets, reason, end));
    console_event("VOICE end %u packets=%d dropped=%lu reason=%s\n", clip, packets, (unsigned long)dropped,
                  reason == VOICE_END_LIMIT ? "limit" : "release");
    app_set_voice(SCENE_VOICE_TRANSCRIBING, VOICE_RESULT_QUEUED);
    return show_result(clip, await_result());
}

static uint32_t show_follow_up(void)
{
    int clip = atomic_load(&followed_clip);
    if (clip == NO_CLIP) {
        return 0;
    }
    int next = take_follow_up((uint8_t)clip);
    return next == NO_FOLLOW ? 0 : show_result((uint8_t)clip, (voice_result_t)next);
}

static void voice_task(void *arg)
{
    for (;;) {
        uint32_t bits = 0;
        xTaskNotifyWait(0, UINT32_MAX, &bits, portMAX_DELAY);
        for (;;) {
            if ((bits & NOTIFY_PRESS) && atomic_load(&held)) {
                bits = run_clip();
            } else if (bits & NOTIFY_FOLLOW) {
                bits = show_follow_up();
            } else {
                break;
            }
        }
    }
}

esp_err_t voice_start(void)
{
    mic_lock = xSemaphoreCreateMutex();
    ESP_RETURN_ON_FALSE(mic_lock != NULL, ESP_ERR_NO_MEM, TAG, "mic lock");
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
    BaseType_t created =
        xTaskCreate(voice_task, "voice", VOICE_TASK_STACK, NULL, VOICE_TASK_PRIORITY, &voice_task_handle);
    ESP_RETURN_ON_FALSE(created == pdPASS, ESP_ERR_NO_MEM, TAG, "voice task");
    atomic_store(&ready, true);
    return ESP_OK;
}

bool voice_ready(void)
{
    return atomic_load(&ready);
}

void voice_press(void)
{
    if (!atomic_load(&ready)) {
        return;
    }
    atomic_store(&held, true);
    xTaskNotify(voice_task_handle, NOTIFY_PRESS, eSetBits);
}

void voice_release(void)
{
    atomic_store(&held, false);
}

void voice_on_result(uint8_t clip, voice_result_t result, const uint8_t *pixels)
{
    if (!atomic_load(&ready)) {
        return;
    }
    if (atomic_load(&awaited_clip) == clip) {
        if (pixels != NULL) {
            app_set_voice_image(pixels);
        }
        if (result == VOICE_RESULT_QUEUED) {
            atomic_store(&followed_clip, clip);
        }
        atomic_store(&arrived_result, (int)result);
        xTaskNotify(voice_task_handle, NOTIFY_RESULT, eSetBits);
    } else if (result != VOICE_RESULT_QUEUED && atomic_load(&followed_clip) == clip) {
        atomic_store(&follow_up, (int)clip << 8 | (int)result);
        xTaskNotify(voice_task_handle, NOTIFY_FOLLOW, eSetBits);
    }
}

bool voice_capture(int packets, int gain, voice_packet_sink_t sink, void *context, voice_capture_stats_t *stats)
{
    static int16_t samples[VOICE_SAMPLES_PER_PACKET];
    static uint8_t packet[VOICE_AUDIO_PACKET];
    if (!atomic_load(&ready) || xSemaphoreTake(mic_lock, 0) != pdTRUE) {
        return false;
    }
    memset(stats, 0, sizeof(*stats));
    bool ok = codec_set_gain(gain) == ESP_OK;
    drain_stale_audio();
    adpcm_state_t state = {0, 0};
    for (int i = 0; ok && i < packets; i++) {
        ok = read_packet(samples, stats) &&
             sink(packet, voice_audio_packet(0, (uint16_t)i, &state, samples, packet), context);
    }
    codec_set_gain(DEFAULT_GAIN);
    xSemaphoreGive(mic_lock);
    return ok;
}
