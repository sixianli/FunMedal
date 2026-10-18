#include "console.h"

#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "app.h"
#include "ble_link.h"
#include "display.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_check.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "key_input.h"
#include "voice.h"

#define FIRMWARE_VERSION "funmedal-0.1"
#define LINE_MAX_LEN 96
#define LINE_OUT_MAX 384
#define WRITE_TRY_MS 20
#define WRITE_LIMIT_US (2000 * 1000LL)
#define EVENT_WAIT_MS 5
#define REBOOT_DELAY_MS 100
#define MIC_PACKETS_MAX 500
#define MIC_DEFAULT_GAIN 4

static const char *TAG = "console";
static _Atomic uint32_t events_dropped;
static volatile uint32_t replies_dropped;

static void print_info(void)
{
    scene_t scene;
    display_config_t config;
    app_get_scene(&scene);
    display_get_config(&config);
    int frame = app_frame_mode();
    uint32_t draws;
    uint32_t draw_errors;
    int shown_frame;
    app_get_draw_stats(&draws, &draw_errors, &shown_frame);
    char prompt[12];
    char security[40];
    if (scene.prompt.active) {
        snprintf(prompt, sizeof(prompt), "%lu", (unsigned long)scene.prompt.id);
    } else {
        snprintf(prompt, sizeof(prompt), "none");
    }
    ble_link_security(security, sizeof(security));
    console_printf("OK info fw=%s state=%s pattern=%d frame=%s invert=%d bgr=%d mirror=%d,%d gap=%d,%d shown_frame=%d "
                   "draws=%lu draw_errors=%lu key_gap_ms=%d events_dropped=%lu replies_dropped=%lu adc_errors=%lu "
                   "ble=%s ble_writes=%lu prompt=%s sec=%s gatt_changed=%u voice=%s mic=%s link=%s uptime_s=%lld\n",
                   FIRMWARE_VERSION, scene_state_name(scene.state), scene.test_pattern,
                   frame == APP_FRAME_AUTO ? "auto" : (frame ? "1" : "0"), config.invert, config.bgr, config.mirror_x,
                   config.mirror_y, config.gap_x, config.gap_y, shown_frame, (unsigned long)draws,
                   (unsigned long)draw_errors, key_input_take_max_gap_ms(),
                   (unsigned long)atomic_load(&events_dropped), (unsigned long)replies_dropped,
                   (unsigned long)key_input_adc_errors(), ble_link_connected() ? "connected" : "advertising",
                   (unsigned long)ble_link_writes(), prompt, security, (unsigned)ble_link_changed_handle(),
                   scene_voice_name(scene.voice), voice_ready() ? "ready" : "off", scene_link_name(scene.link),
                   (long long)(esp_timer_get_time() / 1000000));
}

static bool write_all(const char *data, size_t size, int64_t *waited_us, int *timeouts)
{
    int64_t start = esp_timer_get_time();
    while (usb_serial_jtag_write_bytes(data, size, pdMS_TO_TICKS(WRITE_TRY_MS)) != (int)size) {
        (*timeouts)++;
        if (esp_timer_get_time() - start > WRITE_LIMIT_US) {
            return false;
        }
    }
    *waited_us = esp_timer_get_time() - start;
    return true;
}

static int format_line(char *line, size_t size, const char *fmt, va_list args)
{
    int n = vsnprintf(line, size, fmt, args);
    if (n >= (int)size) {
        n = (int)size - 1;
        line[n - 1] = '\n';
    }
    return n;
}

void console_printf(const char *fmt, ...)
{
    char line[LINE_OUT_MAX];
    va_list args;
    va_start(args, fmt);
    int n = format_line(line, sizeof(line), fmt, args);
    va_end(args);
    if (n <= 0) {
        return;
    }
    int64_t waited_us;
    int timeouts = 0;
    if (!write_all(line, (size_t)n, &waited_us, &timeouts)) {
        replies_dropped++;
    }
}

void console_event(const char *fmt, ...)
{
    char line[LINE_OUT_MAX];
    va_list args;
    va_start(args, fmt);
    int n = format_line(line, sizeof(line), fmt, args);
    va_end(args);
    if (n <= 0) {
        return;
    }
    if (!usb_serial_jtag_is_connected() ||
        usb_serial_jtag_write_bytes(line, (size_t)n, pdMS_TO_TICKS(EVENT_WAIT_MS)) != n) {
        atomic_fetch_add(&events_dropped, 1);
    }
}

static void snap(void)
{
    static uint16_t row[SCENE_W];
    static char line[16 + SCENE_W * 4 + 2];
    static const char digits[] = "0123456789abcdef";
    scene_t scene;
    app_get_scene(&scene);
    int64_t waited_us = 0;
    int64_t max_wait_us = 0;
    int timeouts = 0;
    int len = snprintf(line, sizeof(line), "SNAP BEGIN %d %d state=%s frame=%d pattern=%d\n", SCENE_W, SCENE_H,
                       scene_state_name(scene.state), scene.frame, scene.test_pattern);
    bool ok = write_all(line, (size_t)len, &waited_us, &timeouts);
    max_wait_us = waited_us;
    for (int y = 0; ok && y < SCENE_H; y++) {
        scene_render_rows(&scene, y, 1, row);
        len = snprintf(line, sizeof(line), "SNAP %d ", y);
        const uint8_t *bytes = (const uint8_t *)row;
        for (int i = 0; i < SCENE_W * 2; i++) {
            line[len++] = digits[bytes[i] >> 4];
            line[len++] = digits[bytes[i] & 0x0f];
        }
        line[len++] = '\n';
        ok = write_all(line, (size_t)len, &waited_us, &timeouts);
        if (waited_us > max_wait_us) {
            max_wait_us = waited_us;
        }
    }
    if (!ok) {
        console_printf("ERR snap write timed out\n");
        return;
    }
    console_printf("SNAP END max_wait_ms=%d write_timeouts=%d\n", (int)(max_wait_us / 1000), timeouts);
}

static void print_stacks(void)
{
    static const char *const names[] = {"keys", "display", "console", "nimble_host", "voice"};
    char line[LINE_OUT_MAX];
    int len = snprintf(line, sizeof(line), "OK stacks");
    for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
        TaskHandle_t task = xTaskGetHandle(names[i]);
        len += snprintf(line + len, sizeof(line) - (size_t)len, " %s=%ld", names[i],
                        task != NULL ? (long)uxTaskGetStackHighWaterMark(task) : -1L);
    }
    console_printf("%s\n", line);
}

static bool mic_line(const uint8_t *packet, size_t size, void *context)
{
    static char line[16 + VOICE_AUDIO_PACKET * 2 + 2];
    static const char digits[] = "0123456789abcdef";
    int *sequence = context;
    int len = snprintf(line, sizeof(line), "MIC %d ", (*sequence)++);
    for (size_t i = 0; i < size; i++) {
        line[len++] = digits[packet[i] >> 4];
        line[len++] = digits[packet[i] & 0x0f];
    }
    line[len++] = '\n';
    int64_t waited_us;
    int timeouts = 0;
    if (!write_all(line, (size_t)len, &waited_us, &timeouts)) {
        replies_dropped++;
        return false;
    }
    return true;
}

static uint32_t root_mean_square(uint64_t squares, uint32_t samples)
{
    uint64_t mean = samples > 0 ? squares / samples : 0;
    uint32_t root = 0;
    for (uint32_t bit = 1u << 15; bit != 0; bit >>= 1) {
        uint32_t next = root | bit;
        if ((uint64_t)next * next <= mean) {
            root = next;
        }
    }
    return root;
}

static void mic_command(char *args)
{
    char *count = strtok(args, " ");
    char *gain_text = strtok(NULL, " ");
    char *end = NULL;
    long packets = count != NULL ? strtol(count, &end, 10) : 0;
    bool valid = count != NULL && *end == '\0' && packets >= 1 && packets <= MIC_PACKETS_MAX;
    long gain = MIC_DEFAULT_GAIN;
    if (valid && gain_text != NULL) {
        gain = strtol(gain_text, &end, 10);
        valid = *end == '\0' && gain >= 0 && gain <= VOICE_GAIN_MAX;
    }
    if (!valid || strtok(NULL, " ") != NULL) {
        console_printf("ERR mic usage: mic <1-%d> [0-%d]\n", MIC_PACKETS_MAX, VOICE_GAIN_MAX);
        return;
    }
    if (!voice_ready()) {
        console_printf("ERR mic unavailable\n");
        return;
    }
    int sequence = 0;
    voice_capture_stats_t stats;
    if (!voice_capture((int)packets, (int)gain, mic_line, &sequence, &stats)) {
        console_printf("ERR mic failed after %d packets\n", sequence);
        return;
    }
    console_printf("OK mic packets=%ld gain=%ld rms=%lu other_rms=%lu peak=%d\n", packets, gain,
                   (unsigned long)root_mean_square(stats.squares_used, stats.samples),
                   (unsigned long)root_mean_square(stats.squares_other, stats.samples), stats.peak_used);
}

static bool parse_flag(const char *text, bool *value)
{
    if (text != NULL && (strcmp(text, "0") == 0 || strcmp(text, "1") == 0)) {
        *value = text[0] == '1';
        return true;
    }
    return false;
}

static void lcd_command(char *args)
{
    display_config_t config;
    display_get_config(&config);
    char *what = strtok(args, " ");
    char *a = strtok(NULL, " ");
    char *b = strtok(NULL, " ");
    bool ok = false;
    if (what != NULL && strcmp(what, "invert") == 0) {
        ok = parse_flag(a, &config.invert);
    } else if (what != NULL && strcmp(what, "bgr") == 0) {
        ok = parse_flag(a, &config.bgr);
    } else if (what != NULL && strcmp(what, "mirror") == 0) {
        ok = parse_flag(a, &config.mirror_x) && parse_flag(b, &config.mirror_y);
    } else if (what != NULL && strcmp(what, "gap") == 0 && a != NULL && b != NULL) {
        config.gap_x = atoi(a);
        config.gap_y = atoi(b);
        ok = true;
    }
    if (!ok) {
        console_printf("ERR lcd usage: lcd invert|bgr <0|1> | lcd mirror <0|1> <0|1> | lcd gap <x> <y>\n");
        return;
    }
    esp_err_t err = display_apply(&config);
    if (err != ESP_OK) {
        console_printf("ERR lcd %s\n", esp_err_to_name(err));
        return;
    }
    app_set_frame_mode(app_frame_mode());
    display_get_config(&config);
    console_printf("OK lcd invert=%d bgr=%d mirror=%d,%d gap=%d,%d\n", config.invert, config.bgr, config.mirror_x,
                   config.mirror_y, config.gap_x, config.gap_y);
}

static void key_command(char *args)
{
    char *which = strtok(args, " ");
    char *how = strtok(NULL, " ");
    key_id_t key = KEY_UNKNOWN;
    if (which != NULL) {
        for (key_id_t k = KEY_OK; k < KEY_COUNT; k++) {
            if (strcmp(which, keys_name(k)) == 0) {
                key = k;
            }
        }
    }
    char *ms = strtok(NULL, " ");
    key_inject_kind_t kind = KEY_INJECT_TAP;
    unsigned long hold_ms = 0;
    bool valid_how = false;
    if (how != NULL && strcmp(how, "press") == 0 && ms == NULL) {
        valid_how = true;
    } else if (how != NULL && strcmp(how, "long") == 0 && ms == NULL) {
        kind = KEY_INJECT_LONG;
        valid_how = true;
    } else if (how != NULL && strcmp(how, "hold") == 0 && ms != NULL) {
        char *end = NULL;
        hold_ms = strtoul(ms, &end, 10);
        kind = KEY_INJECT_HOLD;
        valid_how = end != ms && *end == '\0' && hold_ms >= 1 && hold_ms <= KEY_INJECT_HOLD_MAX_MS;
    }
    if (key == KEY_UNKNOWN || !valid_how || strtok(NULL, " ") != NULL) {
        console_printf("ERR key usage: key ok|up|down press|long|hold <1-%d>\n", KEY_INJECT_HOLD_MAX_MS);
        return;
    }
    if (!key_input_inject(key, kind, (uint32_t)hold_ms)) {
        console_printf("ERR key queue full\n");
        return;
    }
    if (kind == KEY_INJECT_HOLD) {
        console_printf("OK key %s hold %lu\n", which, hold_ms);
    } else {
        console_printf("OK key %s %s\n", which, how);
    }
}

static void print_ble_usage(void)
{
    console_printf("ERR ble usage: ble unpair | ble disconnect | ble layout forget | ble decision <id> allow|deny | "
                   "ble reject image|voice|version [<1-%d>] | ble reject none\n", BLE_REFUSE_MAX_TIMES);
}

static void ble_decision_command(void)
{
    char *id_text = strtok(NULL, " ");
    char *word = strtok(NULL, " ");
    char *end = NULL;
    unsigned long id = id_text != NULL ? strtoul(id_text, &end, 10) : 0;
    bool allow = word != NULL && strcmp(word, "allow") == 0;
    bool valid = id_text != NULL && *end == '\0' && end != id_text && (allow || (word != NULL && strcmp(word, "deny") == 0));
    if (!valid || strtok(NULL, " ") != NULL) {
        print_ble_usage();
        return;
    }
    if (!ble_link_send_decision((uint32_t)id, allow)) {
        console_printf("ERR ble decision unsent\n");
        return;
    }
    console_printf("OK ble decision %lu %s\n", id, word);
}

static void ble_layout_command(void)
{
    char *word = strtok(NULL, " ");
    if (word == NULL || strcmp(word, "forget") != 0 || strtok(NULL, " ") != NULL) {
        print_ble_usage();
        return;
    }
    esp_err_t err = ble_link_forget_layout();
    if (err != ESP_OK) {
        console_printf("ERR ble layout forget err=0x%x\n", err);
        return;
    }
    console_printf("OK ble layout forget\n");
}

static bool refusal_from_name(const char *name, ble_refusal_t *what)
{
    static const char *const names[] = {
        [BLE_REFUSE_NONE] = "none", [BLE_REFUSE_IMAGE] = "image", [BLE_REFUSE_VOICE] = "voice", [BLE_REFUSE_VERSION] = "version"};
    for (size_t i = 0; name != NULL && i < sizeof(names) / sizeof(names[0]); i++) {
        if (strcmp(name, names[i]) == 0) {
            *what = (ble_refusal_t)i;
            return true;
        }
    }
    return false;
}

static void ble_reject_command(void)
{
    char *name = strtok(NULL, " ");
    char *count = strtok(NULL, " ");
    char *end = NULL;
    long times = count != NULL ? strtol(count, &end, 10) : 1;
    ble_refusal_t what = BLE_REFUSE_NONE;
    bool named = refusal_from_name(name, &what);
    bool counted = count == NULL || (what != BLE_REFUSE_NONE && end != count && *end == '\0' && times >= 1 &&
                                     times <= BLE_REFUSE_MAX_TIMES);
    if (!named || !counted || strtok(NULL, " ") != NULL) {
        print_ble_usage();
        return;
    }
    ble_link_refuse_next(what, (int)times);
    if (what == BLE_REFUSE_NONE) {
        console_printf("OK ble reject none\n");
    } else {
        console_printf("OK ble reject %s %ld\n", name, times);
    }
}

static void ble_command(char *args)
{
    char *what = strtok(args, " ");
    if (what != NULL && strcmp(what, "reject") == 0) {
        ble_reject_command();
        return;
    }
    if (what != NULL && strcmp(what, "decision") == 0) {
        ble_decision_command();
        return;
    }
    if (what != NULL && strcmp(what, "layout") == 0) {
        ble_layout_command();
        return;
    }
    if (what != NULL && strcmp(what, "disconnect") == 0 && strtok(NULL, " ") == NULL) {
        int rc = ble_link_disconnect();
        if (rc != 0) {
            console_printf("ERR ble disconnect rc=%d\n", rc);
            return;
        }
        console_printf("OK ble disconnect\n");
        return;
    }
    if (what == NULL || strcmp(what, "unpair") != 0 || strtok(NULL, " ") != NULL) {
        print_ble_usage();
        return;
    }
    int rc = ble_link_unpair();
    if (rc != 0) {
        console_printf("ERR ble unpair rc=%d\n", rc);
        return;
    }
    console_printf("OK ble unpair\n");
}

static void handle(char *line)
{
    char *space = strchr(line, ' ');
    char *args = space != NULL ? space + 1 : "";
    if (space != NULL) {
        *space = '\0';
    }
    if (strcmp(line, "info") == 0) {
        print_info();
    } else if (strcmp(line, "state") == 0) {
        pet_state_t state;
        if (!scene_state_from_name(args, &state)) {
            console_printf("ERR state unknown name\n");
            return;
        }
        app_set_state(state);
        console_printf("OK state %s\n", scene_state_name(state));
    } else if (strcmp(line, "frame") == 0) {
        if (strcmp(args, "auto") == 0) {
            app_set_frame_mode(APP_FRAME_AUTO);
        } else if (strcmp(args, "0") == 0 || strcmp(args, "1") == 0) {
            app_set_frame_mode(args[0] - '0');
        } else {
            console_printf("ERR frame usage: frame 0|1|auto\n");
            return;
        }
        console_printf("OK frame %s\n", args);
    } else if (strcmp(line, "pattern") == 0) {
        app_set_pattern();
        console_printf("OK pattern\n");
    } else if (strcmp(line, "fill") == 0) {
        char *end = NULL;
        unsigned long rgb = strtoul(args, &end, 16);
        if (end == args || *end != '\0' || rgb > 0xffffff) {
            console_printf("ERR fill usage: fill <rrggbb>\n");
            return;
        }
        app_set_fill((uint32_t)rgb);
        console_printf("OK fill %06lx\n", rgb);
    } else if (strcmp(line, "snap") == 0) {
        snap();
    } else if (strcmp(line, "lcd") == 0) {
        lcd_command(args);
    } else if (strcmp(line, "adc") == 0) {
        int seconds = atoi(args);
        if (seconds <= 0 || seconds > 120) {
            console_printf("ERR adc usage: adc <1-120>\n");
            return;
        }
        console_printf("OK adc %d\n", seconds);
        key_input_stream(seconds);
    } else if (strcmp(line, "key") == 0) {
        key_command(args);
    } else if (strcmp(line, "ble") == 0) {
        ble_command(args);
    } else if (strcmp(line, "mic") == 0) {
        mic_command(args);
    } else if (strcmp(line, "stacks") == 0) {
        print_stacks();
    } else if (strcmp(line, "reboot") == 0) {
        console_printf("OK reboot\n");
        vTaskDelay(pdMS_TO_TICKS(REBOOT_DELAY_MS));
        esp_restart();
    } else if (strcmp(line, "backlight") == 0) {
        display_backlight(atoi(args));
        console_printf("OK backlight %d\n", atoi(args));
    } else if (line[0] != '\0') {
        console_printf("ERR unknown command %s\n", line);
    }
}

static void console_task(void *arg)
{
    char line[LINE_MAX_LEN];
    int len = 0;
    for (;;) {
        uint8_t c;
        if (usb_serial_jtag_read_bytes(&c, 1, portMAX_DELAY) != 1) {
            continue;
        }
        if (c == '\r' || c == '\n') {
            line[len] = '\0';
            if (len > 0) {
                handle(line);
            }
            len = 0;
        } else if (len < LINE_MAX_LEN - 1) {
            line[len++] = (char)c;
        }
    }
}

esp_err_t console_start(void)
{
    usb_serial_jtag_driver_config_t config = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    config.tx_buffer_size = 4096;
    ESP_RETURN_ON_ERROR(usb_serial_jtag_driver_install(&config), TAG, "usb serial driver");
    usb_serial_jtag_vfs_use_driver();
    setvbuf(stdout, NULL, _IOLBF, 0);
    BaseType_t ok = xTaskCreate(console_task, "console", 4096, NULL, 4, NULL);
    return ok == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}
