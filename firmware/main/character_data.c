#include "character_art.h"

extern const uint8_t character_bin_start[] asm("_binary_character_bin_start");
extern const uint8_t character_bin_end[] asm("_binary_character_bin_end");

bool character_art_init(void)
{
    return character_art_load(character_bin_start, (size_t)(character_bin_end - character_bin_start));
}
