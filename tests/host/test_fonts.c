#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "font.h"

static int failures;

static void check(int ok, const char *what, unsigned long codepoint)
{
    if (!ok) {
        printf("FAIL %s U+%04lX\n", what, codepoint);
        failures++;
    }
}

static int has_ink(const font_t *font, font_glyph_t glyph)
{
    for (int y = 0; y < font->line_height; y++) {
        for (int x = 0; x < glyph.width; x++) {
            if (font_alpha(font, &glyph, x, y) != 0) {
                return 1;
            }
        }
    }
    return 0;
}

static uint8_t *read_all(const char *path, size_t *size)
{
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        return NULL;
    }
    fseek(file, 0, SEEK_END);
    *size = (size_t)ftell(file);
    fseek(file, 0, SEEK_SET);
    uint8_t *data = malloc(*size);
    if (data != NULL && fread(data, 1, *size, file) != *size) {
        free(data);
        data = NULL;
    }
    fclose(file);
    return data;
}

static void check_rejects_damage(const uint8_t *data, size_t size)
{
    font_t font;
    check(!font_load(&font, data, 10), "loads a truncated header", 0);
    check(!font_load(&font, data, size - 1), "loads a file missing its last byte", 0);
    uint8_t *copy = malloc(size);
    memcpy(copy, data, size);
    copy[0] ^= 0xff;
    check(!font_load(&font, copy, size), "loads a file with a wrong magic", 0);
    free(copy);
}

int main(int argc, char **argv)
{
    if (argc != 3) {
        printf("usage: test_fonts <font.bin> <expectations>\n");
        return 2;
    }
    size_t size = 0;
    uint8_t *data = read_all(argv[1], &size);
    font_t font;
    if (data == NULL || !font_load(&font, data, size)) {
        printf("FAIL cannot load %s\n", argv[1]);
        return 1;
    }
    check_rejects_damage(data, size);
    font_glyph_t box = font_glyph(&font, FONT_MISSING);
    FILE *list = fopen(argv[2], "r");
    char kind[8];
    unsigned long codepoint;
    int checked = 0;
    while (list != NULL && fscanf(list, "%7s %lx", kind, &codepoint) == 2) {
        font_glyph_t glyph = font_glyph(&font, (uint32_t)codepoint);
        if (strcmp(kind, "ink") == 0) {
            check(font_has(&font, (uint32_t)codepoint) && glyph.codepoint == codepoint && has_ink(&font, glyph),
                  "draws", codepoint);
        } else if (strcmp(kind, "have") == 0) {
            check(font_has(&font, (uint32_t)codepoint) && glyph.codepoint == codepoint && glyph.width > 0,
                  "holds", codepoint);
        } else {
            check(!font_has(&font, (uint32_t)codepoint) && font_has(&font, FONT_MISSING)
                  && glyph.codepoint == FONT_MISSING && has_ink(&font, glyph), "boxes", codepoint);
        }
        checked++;
    }
    if (list != NULL) {
        fclose(list);
    }
    const char *mixed = "\xe5\x80\x91" "a";
    check(font_width(&font, mixed, strlen(mixed)) == box.width + font_glyph(&font, 'a').width,
          "width of a boxed character and a letter", 0x5011);
    free(data);
    printf("checked %d characters in %u glyphs, line height %u, %u bit\n", checked, (unsigned)font.glyph_count,
           font.line_height, font.bits);
    if (failures == 0) {
        printf("ALL PASSED\n");
    }
    return failures == 0 ? 0 : 1;
}
