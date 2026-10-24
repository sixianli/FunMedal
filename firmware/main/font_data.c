#include "font.h"

extern const uint8_t font_large_start[] asm("_binary_font_large_bin_start");
extern const uint8_t font_large_end[] asm("_binary_font_large_bin_end");
extern const uint8_t font_text_start[] asm("_binary_font_text_bin_start");
extern const uint8_t font_text_end[] asm("_binary_font_text_bin_end");
extern const uint8_t font_pixel_start[] asm("_binary_font_pixel_bin_start");
extern const uint8_t font_pixel_end[] asm("_binary_font_pixel_bin_end");

font_t font_large;
font_t font_text;
font_t font_pixel;

bool font_init(void)
{
    bool large = font_load(&font_large, font_large_start, (size_t)(font_large_end - font_large_start));
    bool text = font_load(&font_text, font_text_start, (size_t)(font_text_end - font_text_start));
    bool pixel = font_load(&font_pixel, font_pixel_start, (size_t)(font_pixel_end - font_pixel_start));
    return large && text && pixel;
}
