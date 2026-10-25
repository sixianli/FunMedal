#include "host_character.h"

#include <stdio.h>
#include <stdlib.h>

#include "character_art.h"

bool host_character_load(const char *dir)
{
    char path[1024];
    snprintf(path, sizeof(path), "%s/character.bin", dir);
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        printf("FAIL cannot open %s\n", path);
        return false;
    }
    fseek(file, 0, SEEK_END);
    long size = ftell(file);
    fseek(file, 0, SEEK_SET);
    uint8_t *data = malloc((size_t)size);
    bool ok = data != NULL && fread(data, 1, (size_t)size, file) == (size_t)size && character_art_load(data, (size_t)size);
    fclose(file);
    if (!ok) {
        printf("FAIL %s is not a character pack\n", path);
    }
    return ok;
}
