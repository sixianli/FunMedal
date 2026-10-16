#include "keys.h"

const key_level_t keys_board_levels[] = {
    {KEY_NONE, 2897},
    {KEY_OK, 3},
    {KEY_UP, 301},
    {KEY_DOWN, 597},
};
const int keys_board_level_count = sizeof(keys_board_levels) / sizeof(keys_board_levels[0]);
const int keys_board_tolerance_mv = 100;
