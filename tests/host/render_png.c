#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "character_art.h"
#include "events.h"
#include "host_character.h"
#include "host_fonts.h"
#include "scene.h"
#include "screen.h"

#define ROW_BYTES (1 + SCENE_W * 3)
#define RAW_BYTES (SCENE_H * ROW_BYTES)
#define STORED_MAX 65535

static uint16_t pixels[SCENE_W * SCENE_H];
static uint8_t raw[RAW_BYTES];

static uint32_t crc_table[256];

static void crc_init(void)
{
    for (uint32_t n = 0; n < 256; n++) {
        uint32_t c = n;
        for (int k = 0; k < 8; k++) {
            c = c & 1 ? 0xedb88320u ^ (c >> 1) : c >> 1;
        }
        crc_table[n] = c;
    }
}

static uint32_t crc(uint32_t c, const uint8_t *data, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        c = crc_table[(c ^ data[i]) & 0xff] ^ (c >> 8);
    }
    return c;
}

static void put32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);
    p[3] = (uint8_t)v;
}

static void chunk(FILE *out, const char *type, const uint8_t *data, size_t len)
{
    uint8_t head[8];
    put32(head, (uint32_t)len);
    memcpy(head + 4, type, 4);
    fwrite(head, 1, 8, out);
    fwrite(data, 1, len, out);
    uint32_t c = crc(0xffffffffu, head + 4, 4);
    c = crc(c, data, len) ^ 0xffffffffu;
    uint8_t tail[4];
    put32(tail, c);
    fwrite(tail, 1, 4, out);
}

static uint8_t *zlib_stored(size_t *len)
{
    size_t blocks = (RAW_BYTES + STORED_MAX - 1) / STORED_MAX;
    uint8_t *out = malloc(2 + blocks * 5 + RAW_BYTES + 4);
    size_t at = 0;
    out[at++] = 0x78;
    out[at++] = 0x01;
    for (size_t done = 0; done < RAW_BYTES;) {
        size_t n = RAW_BYTES - done < STORED_MAX ? RAW_BYTES - done : STORED_MAX;
        out[at++] = done + n == RAW_BYTES ? 1 : 0;
        out[at++] = (uint8_t)n;
        out[at++] = (uint8_t)(n >> 8);
        out[at++] = (uint8_t)~n;
        out[at++] = (uint8_t)(~n >> 8);
        memcpy(out + at, raw + done, n);
        at += n;
        done += n;
    }
    uint32_t a = 1;
    uint32_t b = 0;
    for (size_t i = 0; i < RAW_BYTES; i++) {
        a = (a + raw[i]) % 65521;
        b = (b + a) % 65521;
    }
    put32(out + at, b << 16 | a);
    *len = at + 4;
    return out;
}

static bool write_png(const char *path)
{
    FILE *out = fopen(path, "wb");
    if (out == NULL) {
        return false;
    }
    static const uint8_t signature[8] = {0x89, 'P', 'N', 'G', '\r', '\n', 0x1a, '\n'};
    fwrite(signature, 1, sizeof signature, out);
    uint8_t header[13] = {0};
    put32(header, SCENE_W);
    put32(header + 4, SCENE_H);
    header[8] = 8;
    header[9] = 2;
    chunk(out, "IHDR", header, sizeof header);
    size_t len;
    uint8_t *data = zlib_stored(&len);
    chunk(out, "IDAT", data, len);
    free(data);
    chunk(out, "IEND", NULL, 0);
    return fclose(out) == 0;
}

static bool state_from(const char *word, row_state_t *state)
{
    static const char *const words[] = {[ROW_WAITING] = "waiting", [ROW_ERROR] = "error",
                                        [ROW_RUNNING] = "running", [ROW_DONE] = "done"};
    for (int s = ROW_WAITING; s <= ROW_DONE; s++) {
        if (strcmp(word, words[s]) == 0) {
            *state = (row_state_t)s;
            return true;
        }
    }
    return false;
}

static bool read_scene(const char *path, scene_t *scene)
{
    FILE *in = fopen(path, "r");
    if (in == NULL) {
        return false;
    }
    char line[256];
    bool ok = true;
    bool daily_set = false;
    while (ok && fgets(line, sizeof line, in) != NULL) {
        line[strcspn(line, "\n")] = 0;
        char word[16];
        unsigned value;
        char minutes[16];
        char mark[2];
        int used = 0;
        if (line[0] == 0) {
            continue;
        } else if (sscanf(line, "theme %u", &value) == 1) {
            scene->theme = (uint8_t)value;
        } else if (sscanf(line, "tick %u", &value) == 1) {
            scene->screen.tick = value;
            if (!daily_set) {
                scene->screen.daily = value;
            }
        } else if (sscanf(line, "daily %u", &value) == 1) {
            scene->screen.daily = value;
            daily_set = true;
        } else if (sscanf(line, "event %15s %u %n", word, &value, &used) == 2 && used > 0) {
            event_view_t *event = &scene->screen.event;
            ok = events_kind(word, &event->kind);
            event->ms = value;
            snprintf(event->name, sizeof event->name, "%s", line + used);
        } else if (sscanf(line, "total %u", &value) == 1) {
            scene->screen.total = (uint8_t)value;
        } else if (sscanf(line, "mode %15s", word) == 1) {
            scene->screen.mode = strcmp(word, "rows") == 0    ? SCREEN_ROWS
                                 : strcmp(word, "empty") == 0 ? SCREEN_EMPTY
                                                              : SCREEN_UNLINKED;
        } else if (sscanf(line, "row %15s %15s %1s %n", word, minutes, mark, &used) == 3 && used > 0 &&
                   scene->screen.shown < ROWS_MAX) {
            row_t *row = &scene->screen.rows[scene->screen.shown];
            ok = state_from(word, &row->state) && (strcmp(mark, "p") == 0 || strcmp(mark, "-") == 0);
            row->id = (uint8_t)(scene->screen.shown + 1);
            row->minutes = strcmp(minutes, "-") == 0 ? ROWS_HIDDEN_MINUTES : (uint16_t)atoi(minutes);
            row->project = strcmp(mark, "p") == 0;
            snprintf(row->name, sizeof row->name, "%s", line + used);
            scene->screen.shown++;
        } else {
            ok = false;
        }
    }
    fclose(in);
    return ok;
}

int main(int argc, char **argv)
{
    if (argc < 4 || argc > 6 || !host_fonts_load(argv[1]) || !host_character_load(argv[1])) {
        printf("usage: render_png <fonts dir> <scene file> <out.png> [out.rgb] [out.wire]\n");
        return 2;
    }
    scene_t scene = {0};
    if (!read_scene(argv[2], &scene)) {
        printf("FAIL cannot read scene %s\n", argv[2]);
        return 2;
    }
    printf("ASSETS %08x\n", (unsigned)character_art_fingerprint());
    character_stage_t stage = screen_character_stage(screen_theme(scene.theme), &scene.screen);
    printf("CHARACTER %d %d %d %d\n", stage.box.x, stage.box.y, stage.box.w, stage.box.h);
    const theme_t *theme = screen_theme(scene.theme);
    rect_t rise = theme->alert.stage.clip;
    printf("ALERT %d %d %d %d\n", rise.x, rise.y, rise.w, rise.h);
    printf("CHEER %d %d %d %d\n", theme->cheer.x, theme->cheer.y, theme->cheer.w, theme->cheer.h);
    scene_render_rows(&scene, 0, SCENE_H, pixels);
    for (int y = 0; y < SCENE_H; y++) {
        uint8_t *row = raw + y * ROW_BYTES;
        row[0] = 0;
        for (int x = 0; x < SCENE_W; x++) {
            uint32_t rgb = draw_rgb(pixels[y * SCENE_W + x]);
            row[1 + x * 3] = (uint8_t)(rgb >> 16);
            row[2 + x * 3] = (uint8_t)(rgb >> 8);
            row[3 + x * 3] = (uint8_t)rgb;
        }
    }
    crc_init();
    if (!write_png(argv[3])) {
        printf("FAIL cannot write %s\n", argv[3]);
        return 2;
    }
    if (argc >= 5) {
        FILE *out = fopen(argv[4], "wb");
        for (int y = 0; out != NULL && y < SCENE_H; y++) {
            fwrite(raw + y * ROW_BYTES + 1, 1, SCENE_W * 3, out);
        }
        if (out == NULL || fclose(out) != 0) {
            printf("FAIL cannot write %s\n", argv[4]);
            return 2;
        }
    }
    if (argc == 6) {
        FILE *out = fopen(argv[5], "wb");
        if (out == NULL || fwrite(pixels, sizeof pixels[0], SCENE_W * SCENE_H, out) != SCENE_W * SCENE_H ||
            fclose(out) != 0) {
            printf("FAIL cannot write %s\n", argv[5]);
            return 2;
        }
    }
    region_t regions[DIRTY_MAX_REGIONS];
    int count = scene_regions(&scene, regions);
    for (int i = 0; i < count; i++) {
        printf("REGION %d %d %08x\n", regions[i].y, regions[i].h, (unsigned)regions[i].key);
    }
    return 0;
}
