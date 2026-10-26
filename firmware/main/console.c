#include "console.h"

#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "app.h"
#include "character_art.h"
#include "audio.h"
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
#include "screen.h"

#define FIRMWARE_VERSION "funmedal-0.1"
#define LINE_MAX_LEN 96
#define LINE_OUT_MAX 384
#define WRITE_TRY_MS 20
#define WRITE_LIMIT_US (2000 * 1000LL)
#define EVENT_WAIT_MS 5
#define REBOOT_DELAY_MS 100
#define IDLE_MIN_S 5
#define IDLE_MAX_S 3600

static const char *TAG = "console";
static _Atomic uint32_t events_dropped;
static volatile uint32_t replies_dropped;

static const char *const SCREEN_MODES[] = {
    [SCREEN_UNLINKED] = "unlinked",
    [SCREEN_EMPTY] = "empty",
    [SCREEN_ROWS] = "rows",
};

static void print_info(void)
{
    scene_t scene;
    display_config_t config;
    app_get_scene(&scene);
    display_get_config(&config);
    uint32_t draws;
    uint32_t draw_errors;
    uint32_t shown_tick;
    app_get_draw_stats(&draws, &draw_errors, &shown_tick);
    char security[40];
    ble_link_security(security, sizeof(security));
    console_printf("OK info fw=%s state=%s pattern=%d mode=%s theme=%u assets=%08lx tick=%lu shown_tick=%lu invert=%d bgr=%d mirror=%d,%d "
                   "gap=%d,%d "
                   "draws=%lu draw_errors=%lu key_gap_ms=%d events_dropped=%lu replies_dropped=%lu adc_errors=%lu "
                   "ble=%s ble_writes=%lu sec=%s gatt_changed=%u audio=%s mute=%s link=%s uptime_s=%lld\n",
                   FIRMWARE_VERSION, app_top_state(), scene.test_pattern, SCREEN_MODES[scene.screen.mode],
                   (unsigned)scene.theme, (unsigned long)character_art_fingerprint(), (unsigned long)scene.screen.tick,
                   (unsigned long)shown_tick, config.invert, config.bgr,
                   config.mirror_x, config.mirror_y, config.gap_x, config.gap_y, (unsigned long)draws,
                   (unsigned long)draw_errors, key_input_take_max_gap_ms(),
                   (unsigned long)atomic_load(&events_dropped), (unsigned long)replies_dropped,
                   (unsigned long)key_input_adc_errors(), ble_link_connected() ? "connected" : "advertising",
                   (unsigned long)ble_link_writes(), security, (unsigned)ble_link_changed_handle(),
                   audio_ready() ? "ready" : "off", audio_muted() ? "on" : "off", scene_link_name(scene.link),
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
    char event_name[2 * ROWS_NAME_MAX + 1] = "";
    for (size_t i = 0; scene.screen.event.name[i] != '\0'; i++) {
        uint8_t byte = (uint8_t)scene.screen.event.name[i];
        event_name[2 * i] = digits[byte >> 4];
        event_name[2 * i + 1] = digits[byte & 0x0f];
        event_name[2 * i + 2] = '\0';
    }
    int len = snprintf(line, sizeof(line),
                       "SNAP BEGIN %d %d state=%s tick=%lu mode=%s pattern=%d theme=%u assets=%08lx daily=%lu "
                       "event=%s event_ms=%lu event_name=%s\n",
                       SCENE_W, SCENE_H, app_top_state(), (unsigned long)scene.screen.tick,
                       SCREEN_MODES[scene.screen.mode], scene.test_pattern, (unsigned)scene.theme,
                       (unsigned long)character_art_fingerprint(), (unsigned long)scene.screen.daily,
                       events_name(scene.screen.event.kind), (unsigned long)scene.screen.event.ms, event_name);
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
    static const char *const names[] = {"keys", "display", "console", "nimble_host", "chime"};
    char line[LINE_OUT_MAX];
    int len = snprintf(line, sizeof(line), "OK stacks");
    for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
        TaskHandle_t task = xTaskGetHandle(names[i]);
        len += snprintf(line + len, sizeof(line) - (size_t)len, " %s=%ld", names[i],
                        task != NULL ? (long)uxTaskGetStackHighWaterMark(task) : -1L);
    }
    console_printf("%s\n", line);
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
    console_printf("ERR ble usage: ble unpair | ble disconnect | ble layout forget | "
                   "ble reject version [<1-%d>] | ble reject none\n", BLE_REFUSE_MAX_TIMES);
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
        [BLE_REFUSE_NONE] = "none", [BLE_REFUSE_VERSION] = "version"};
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

static void print_rows(void)
{
    rows_table_t table;
    int64_t now_us;
    app_get_rows(&table, &now_us);
    for (int i = 0; i < table.frame.shown; i++) {
        const row_t *row = &table.frame.rows[i];
        uint16_t minutes = rows_minutes(&table, i, now_us);
        char mark = row->project ? 'p' : '-';
        if (minutes == ROWS_HIDDEN_MINUTES) {
            console_printf("ROW %u %s - %c %s\n", row->id, rows_state_name(row->state), mark, row->name);
        } else {
            console_printf("ROW %u %s %u %c %s\n", row->id, rows_state_name(row->state), minutes, mark, row->name);
        }
    }
    console_printf("OK rows total=%u shown=%u linked=%d\n", table.frame.total, table.frame.shown, table.linked);
}

static void tone_command(char *args)
{
    int hz = 0;
    int ms = 0;
    int volume = 0xbf;
    int got = sscanf(args, "%d %d %i", &hz, &ms, &volume);
    if (got < 2 || hz < 50 || hz > 7000 || ms < 10 || ms > 5000 || volume < 0 || volume > 0xff) {
        console_printf("ERR tone usage: tone <50-7000 hz> <10-5000 ms> [dac volume 0-255]\n");
        return;
    }
    esp_err_t err = audio_tone(hz, ms, volume);
    if (err != ESP_OK) {
        console_printf("ERR tone %s\n", esp_err_to_name(err));
        return;
    }
    console_printf("OK tone %d %d %d\n", hz, ms, volume);
}

static void mute_command(const char *args)
{
    if (strcmp(args, "on") != 0 && strcmp(args, "off") != 0) {
        console_printf("ERR mute usage: mute <on|off>\n");
        return;
    }
    audio_mute(strcmp(args, "on") == 0);
    console_printf("OK mute %s\n", args);
}

static void idle_command(const char *args)
{
    char *end = NULL;
    long seconds = strtol(args, &end, 10);
    if (end == args || *end != '\0' || seconds < IDLE_MIN_S || seconds > IDLE_MAX_S) {
        console_printf("ERR idle usage: idle <%d-%d seconds>\n", IDLE_MIN_S, IDLE_MAX_S);
        return;
    }
    app_set_idle(seconds * 1000000LL);
    console_printf("OK idle %ld\n", seconds);
}

static void power_command(const char *args)
{
    if (strcmp(args, "battery on") == 0 || strcmp(args, "battery off") == 0) {
        app_pretend_battery(strcmp(args, "battery on") == 0);
        console_printf("OK power %s\n", args);
        return;
    }
    if (args[0] != '\0') {
        console_printf("ERR power usage: power [battery on|off]\n");
        return;
    }
    app_power_t power;
    app_get_power(&power);
    console_printf("OK power usb=%s battery=%s screen=%s backlight=%d idle_s=%lld quiet_ms=%lld\n",
                   power.usb ? "yes" : "no", power.pretend_battery ? "pretend" : "real", power.dark ? "off" : "on",
                   display_backlight_percent(), (long long)(power.idle_us / 1000000),
                   (long long)(power.quiet_us / 1000));
}

static void bench_command(const char *args)
{
    int y0 = 0;
    int rows = 0;
    char extra = 0;
    if (sscanf(args, "%d %d %c", &y0, &rows, &extra) != 2 || y0 < 0 || rows < 1 || y0 + rows > SCENE_H) {
        console_printf("ERR bench usage: bench <y0> <rows> with y0 + rows <= %d\n", SCENE_H);
        return;
    }
    static scene_t scene;
    rows_table_t table;
    int64_t now_us;
    app_get_rows(&table, &now_us);
    scene = (scene_t){.theme = app_get_theme()};
    screen_from_rows(&scene.screen, &table, now_us, (uint32_t)(now_us / (THEME_TICK_MS * 1000LL)));
    span_t span = {(int16_t)y0, (int16_t)rows};
    int64_t render_us;
    int64_t total_us;
    esp_err_t err = display_draw_spans(&scene, &span, 1, &render_us, &total_us);
    display_invalidate();
    if (err != ESP_OK) {
        console_printf("ERR bench draw failed %s\n", esp_err_to_name(err));
        return;
    }
    console_printf("OK bench y0=%d rows=%d shown=%u render_us=%lld total_us=%lld\n", y0, rows,
                   (unsigned)scene.screen.shown, (long long)render_us, (long long)total_us);
}

static void theme_command(const char *args)
{
    if (args[0] == '\0') {
        console_printf("OK theme %u\n", (unsigned)app_get_theme());
        return;
    }
    char *end = NULL;
    unsigned long theme = strtoul(args, &end, 10);
    if (end == args || *end != '\0' || theme >= SCREEN_THEME_COUNT) {
        console_printf("ERR theme usage: theme [0-%d]\n", SCREEN_THEME_COUNT - 1);
        return;
    }
    app_set_theme((uint8_t)theme);
    console_printf("OK theme %lu\n", theme);
}

static void alert_command(char *args)
{
    char *space = strchr(args, ' ');
    const char *name = space != NULL ? space + 1 : "FunMedal";
    if (space != NULL) {
        *space = '\0';
    }
    event_kind_t kind;
    if (!events_kind(args, &kind) || name[0] == '\0' || strlen(name) > ROWS_NAME_MAX) {
        console_printf("ERR alert usage: alert wait|error|cheer|hello [name up to %d bytes]\n", ROWS_NAME_MAX);
        return;
    }
    if (!app_start_event(kind, name)) {
        console_printf("ERR alert a more important event is showing\n");
        return;
    }
    console_printf("OK alert %s %s\n", events_name(kind), name);
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
    } else if (strcmp(line, "rows") == 0) {
        print_rows();
    } else if (strcmp(line, "board") == 0) {
        if (args[0] != '\0') {
            console_printf("ERR board takes no arguments\n");
            return;
        }
        app_show_board();
        console_printf("OK board\n");
    } else if (strcmp(line, "theme") == 0) {
        theme_command(args);
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
    } else if (strcmp(line, "stacks") == 0) {
        print_stacks();
    } else if (strcmp(line, "reboot") == 0) {
        console_printf("OK reboot\n");
        vTaskDelay(pdMS_TO_TICKS(REBOOT_DELAY_MS));
        esp_restart();
    } else if (strcmp(line, "timing") == 0) {
        int64_t render_us;
        int64_t total_us;
        display_last_timing(&render_us, &total_us);
        console_printf("OK timing render_us=%lld total_us=%lld\n", (long long)render_us, (long long)total_us);
    } else if (strcmp(line, "bench") == 0) {
        bench_command(args);
    } else if (strcmp(line, "alert") == 0) {
        alert_command(args);
    } else if (strcmp(line, "tone") == 0) {
        tone_command(args);
    } else if (strcmp(line, "mute") == 0) {
        mute_command(args);
    } else if (strcmp(line, "idle") == 0) {
        idle_command(args);
    } else if (strcmp(line, "power") == 0) {
        power_command(args);
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
    BaseType_t ok = xTaskCreate(console_task, "console", 5120, NULL, 4, NULL);
    return ok == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}
