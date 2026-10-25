#include "host_fonts.h"

#include <stdio.h>
#include <stdlib.h>

#include "font.h"

font_t font_large;
font_t font_text;
font_t font_pixel;

static bool load(font_t *font, const char *dir, const char *name)
{
    char path[1024];
    snprintf(path, sizeof(path), "%s/font_%s.bin", dir, name);
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        printf("FAIL cannot open %s\n", path);
        return false;
    }
    fseek(file, 0, SEEK_END);
    long size = ftell(file);
    fseek(file, 0, SEEK_SET);
    uint8_t *data = malloc((size_t)size);
    bool ok = data != NULL && fread(data, 1, (size_t)size, file) == (size_t)size && font_load(font, data, (size_t)size);
    fclose(file);
    if (!ok) {
        printf("FAIL %s is not a font\n", path);
    }
    return ok;
}

bool host_fonts_load(const char *dir)
{
    return load(&font_large, dir, "large") && load(&font_text, dir, "text") && load(&font_pixel, dir, "pixel");
}
