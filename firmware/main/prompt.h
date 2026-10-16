#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "font.h"

#define PROMPT_TOOL_MAX 31
#define PROMPT_TEXT_MAX 240
#define PROMPT_COUNT_MAX 999
#define PROMPT_VALUE_MAX (10 + 1 + 3 + 1 + 3 + 1 + PROMPT_TOOL_MAX + 1 + PROMPT_TEXT_MAX)
#define PROMPT_LINES_MAX 7

typedef struct {
    bool active;
    uint32_t id;
    uint16_t index;
    uint16_t count;
    char tool[PROMPT_TOOL_MAX + 1];
    char text[PROMPT_TEXT_MAX + 1];
} prompt_t;

typedef struct {
    uint8_t start;
    uint8_t len;
} prompt_line_t;

typedef struct {
    int count;
    bool truncated;
    prompt_line_t lines[PROMPT_LINES_MAX];
} prompt_layout_t;

bool prompt_parse(const char *data, size_t len, prompt_t *out);
bool prompt_equal(const prompt_t *a, const prompt_t *b);
void prompt_layout(const font_t *font, const char *text, int max_width, prompt_layout_t *out);
size_t prompt_fit(const font_t *font, const char *text, size_t len, int max_width, bool *truncated);
