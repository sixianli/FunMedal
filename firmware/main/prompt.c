#include "prompt.h"

#include <string.h>

#define FIELD_COUNT 5
#define NUMBER_DIGITS_MAX 10

static bool parse_number(const char *s, size_t len, uint32_t min, uint32_t max, uint32_t *out)
{
    if (len == 0 || len > NUMBER_DIGITS_MAX) {
        return false;
    }
    uint64_t value = 0;
    for (size_t i = 0; i < len; i++) {
        if (s[i] < '0' || s[i] > '9') {
            return false;
        }
        value = value * 10 + (uint64_t)(s[i] - '0');
    }
    if (value < min || value > max) {
        return false;
    }
    *out = (uint32_t)value;
    return true;
}

static void copy_printable(const char *s, size_t len, bool keep_newlines, char *out)
{
    const char *end = s + len;
    size_t n = 0;
    while (s < end) {
        uint32_t cp = font_next_utf8(&s, end);
        bool printable = cp >= 0x20 && cp <= 0x7e;
        out[n++] = printable || (keep_newlines && cp == '\n') ? (char)cp : '?';
    }
    out[n] = '\0';
}

bool prompt_parse(const char *data, size_t len, prompt_t *out)
{
    if (len == 0) {
        memset(out, 0, sizeof(*out));
        return true;
    }
    if (len > PROMPT_VALUE_MAX) {
        return false;
    }
    const char *field[FIELD_COUNT];
    size_t field_len[FIELD_COUNT];
    int count = 0;
    const char *start = data;
    for (size_t i = 0; i <= len; i++) {
        if (i == len || data[i] == '\t') {
            if (count == FIELD_COUNT) {
                return false;
            }
            field[count] = start;
            field_len[count] = (size_t)(data + i - start);
            count++;
            start = data + i + 1;
        }
    }
    uint32_t id;
    uint32_t index;
    uint32_t total;
    if (count != FIELD_COUNT || !parse_number(field[0], field_len[0], 1, UINT32_MAX, &id) ||
        !parse_number(field[2], field_len[2], 1, PROMPT_COUNT_MAX, &total) ||
        !parse_number(field[1], field_len[1], 1, total, &index) || field_len[3] == 0 ||
        field_len[3] > PROMPT_TOOL_MAX || field_len[4] > PROMPT_TEXT_MAX) {
        return false;
    }
    memset(out, 0, sizeof(*out));
    out->active = true;
    out->id = id;
    out->index = (uint16_t)index;
    out->count = (uint16_t)total;
    copy_printable(field[3], field_len[3], false, out->tool);
    copy_printable(field[4], field_len[4], true, out->text);
    return true;
}

bool prompt_equal(const prompt_t *a, const prompt_t *b)
{
    if (!a->active || !b->active) {
        return a->active == b->active;
    }
    return a->id == b->id && a->index == b->index && a->count == b->count && strcmp(a->tool, b->tool) == 0 &&
           strcmp(a->text, b->text) == 0;
}

static int char_width(const font_t *font, char c)
{
    const font_glyph_t *glyph = font_find(font, (unsigned char)c);
    return glyph != NULL ? glyph->width : 0;
}

static size_t fit_prefix(const font_t *font, const char *text, size_t len, int max_width)
{
    size_t kept = 0;
    int width = 0;
    while (kept < len && width + char_width(font, text[kept]) <= max_width) {
        width += char_width(font, text[kept]);
        kept++;
    }
    return kept;
}

static int width_before_ellipsis(const font_t *font, int max_width)
{
    const font_glyph_t *ellipsis = font_find(font, FONT_ELLIPSIS);
    return max_width - (ellipsis != NULL ? ellipsis->width : 0);
}

void prompt_layout(const font_t *font, const char *text, int max_width, prompt_layout_t *out)
{
    size_t len = strlen(text);
    size_t pos = 0;
    out->count = 0;
    out->truncated = false;
    while (pos < len) {
        if (out->count == PROMPT_LINES_MAX) {
            out->truncated = true;
            break;
        }
        size_t start = pos;
        int width = 0;
        while (pos < len && text[pos] != '\n' && width + char_width(font, text[pos]) <= max_width) {
            width += char_width(font, text[pos]);
            pos++;
        }
        out->lines[out->count++] = (prompt_line_t){(uint8_t)start, (uint8_t)(pos - start)};
        if (pos < len && text[pos] == '\n') {
            pos++;
        }
    }
    if (!out->truncated) {
        return;
    }
    prompt_line_t *last = &out->lines[out->count - 1];
    last->len = (uint8_t)fit_prefix(font, text + last->start, last->len, width_before_ellipsis(font, max_width));
}

size_t prompt_fit(const font_t *font, const char *text, size_t len, int max_width, bool *truncated)
{
    *truncated = fit_prefix(font, text, len, max_width) < len;
    if (!*truncated) {
        return len;
    }
    return fit_prefix(font, text, len, width_before_ellipsis(font, max_width));
}
