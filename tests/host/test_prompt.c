#include <stdio.h>
#include <string.h>

#include "font.h"
#include "prompt.h"

#define MAX_WIDTH 220
#define LIT(s) s, sizeof(s) - 1

static int failures;

static void fail(const char *what, const char *detail)
{
    printf("FAIL %s: %s\n", what, detail);
    failures++;
}

static bool parse(const char *data, size_t len, prompt_t *out)
{
    char copy[PROMPT_VALUE_MAX + 64];
    memset(copy, '#', sizeof(copy));
    memcpy(copy, data, len);
    memset(out, 0x5a, sizeof(*out));
    return prompt_parse(copy, len, out);
}

static void expect_reject(const char *what, const char *data, size_t len)
{
    prompt_t out;
    if (parse(data, len, &out)) {
        fail("accepted", what);
    }
}

static void expect_prompt(const char *what, const char *data, size_t len, uint32_t id, int index, int count,
                          const char *tool, const char *text)
{
    prompt_t out;
    if (!parse(data, len, &out)) {
        fail("rejected", what);
        return;
    }
    if (!out.active || out.id != id || out.index != index || out.count != count || strcmp(out.tool, tool) != 0 ||
        strcmp(out.text, text) != 0) {
        printf("  got active=%d id=%lu %d/%d tool=[%s] text=[%s]\n", out.active, (unsigned long)out.id, out.index,
               out.count, out.tool, out.text);
        fail("wrong fields", what);
    }
}

static void repeat(char *buf, char c, int n)
{
    memset(buf, c, (size_t)n);
    buf[n] = '\0';
}

static void parsing(void)
{
    char buf[1024];
    char tool[64];
    char text[512];

    expect_prompt("plain", LIT("7\t1\t2\tBash\techo hi"), 7, 1, 2, "Bash", "echo hi");
    expect_prompt("empty text", LIT("7\t1\t1\tmcp__x__y\t"), 7, 1, 1, "mcp__x__y", "");
    expect_prompt("largest id and count", LIT("4294967295\t999\t999\tBash\tls"), 4294967295u, 999, 999, "Bash", "ls");
    expect_prompt("newline kept", LIT("3\t1\t1\tBash\tcd a\nmake"), 3, 1, 1, "Bash", "cd a\nmake");
    expect_prompt("chinese becomes one mark each", LIT("3\t1\t1\tBash\techo \xe4\xbd\xa0\xe5\xa5\xbd"), 3, 1, 1, "Bash",
                  "echo ??");
    expect_prompt("control and nul become marks", LIT("3\t1\t1\tBash\ta\x01" "b\x00" "c\x7f"), 3, 1, 1, "Bash",
                  "a?b?c?");
    expect_prompt("invalid utf-8 becomes marks", LIT("3\t1\t1\tBash\t\xff" "a\xe4\xbd"), 3, 1, 1, "Bash", "?a?");
    expect_prompt("non-ascii tool becomes marks", LIT("3\t1\t1\tBash\xe4\xbd\xa0\tls"), 3, 1, 1, "Bash?", "ls");
    expect_prompt("newline in tool becomes mark", LIT("3\t1\t1\tBa\nsh\tls"), 3, 1, 1, "Ba?sh", "ls");

    repeat(tool, 'T', PROMPT_TOOL_MAX);
    repeat(text, 'x', PROMPT_TEXT_MAX);
    int n = snprintf(buf, sizeof(buf), "9\t1\t1\t%s\t%s", tool, text);
    expect_prompt("longest tool and text", buf, (size_t)n, 9, 1, 1, tool, text);

    repeat(tool, 'T', PROMPT_TOOL_MAX + 1);
    n = snprintf(buf, sizeof(buf), "9\t1\t1\t%s\tls", tool);
    expect_reject("tool too long", buf, (size_t)n);
    repeat(text, 'x', PROMPT_TEXT_MAX + 1);
    n = snprintf(buf, sizeof(buf), "9\t1\t1\tBash\t%s", text);
    expect_reject("text too long", buf, (size_t)n);

    expect_reject("four fields", LIT("7\t1\t2\tBash"));
    expect_reject("tab in text", LIT("7\t1\t2\tBash\ta\tb"));
    expect_reject("id zero", LIT("0\t1\t1\tBash\tls"));
    expect_reject("id empty", LIT("\t1\t1\tBash\tls"));
    expect_reject("id letters", LIT("a7\t1\t1\tBash\tls"));
    expect_reject("id signed", LIT("+7\t1\t1\tBash\tls"));
    expect_reject("id negative", LIT("-7\t1\t1\tBash\tls"));
    expect_reject("id overflow", LIT("4294967296\t1\t1\tBash\tls"));
    expect_reject("id too many digits", LIT("00000000001\t1\t1\tBash\tls"));
    expect_reject("index zero", LIT("7\t0\t1\tBash\tls"));
    expect_reject("index above count", LIT("7\t3\t2\tBash\tls"));
    expect_reject("count too big", LIT("7\t1\t1000\tBash\tls"));
    expect_reject("tool empty", LIT("7\t1\t1\t\tls"));
    expect_reject("space in number", LIT("7 \t1\t1\tBash\tls"));

    prompt_t out;
    if (!parse("", 0, &out) || out.active) {
        fail("empty value", "should clear the prompt");
    }
    prompt_t a;
    prompt_t b;
    parse(LIT("7\t1\t2\tBash\tls"), &a);
    parse(LIT("7\t1\t2\tBash\tls"), &b);
    if (!prompt_equal(&a, &b)) {
        fail("equal", "same value parsed twice differs");
    }
    parse(LIT("7\t1\t1\tBash\tls"), &b);
    if (prompt_equal(&a, &b)) {
        fail("equal", "different count compares equal");
    }
}

static int char_width(char c)
{
    const font_glyph_t *g = font_find(&font_text, (unsigned char)c);
    return g == NULL ? -1 : g->width;
}

static void check_layout(const char *what, const char *text, bool want_truncated, int want_lines)
{
    prompt_layout_t layout;
    memset(&layout, 0x5a, sizeof(layout));
    prompt_layout(&font_text, text, MAX_WIDTH, &layout);
    int ellipsis = font_find(&font_text, FONT_ELLIPSIS)->width;
    size_t len = strlen(text);
    size_t pos = 0;
    if (layout.count < 0 || layout.count > PROMPT_LINES_MAX) {
        fail("line count out of range", what);
        return;
    }
    for (int i = 0; i < layout.count; i++) {
        prompt_line_t line = layout.lines[i];
        if (line.start != pos) {
            fail("line does not start where the previous ended", what);
            return;
        }
        int width = 0;
        for (int j = 0; j < line.len; j++) {
            char c = text[line.start + j];
            if (c == '\n' || char_width(c) < 0) {
                fail("line holds a newline or a character without a glyph", what);
                return;
            }
            width += char_width(c);
        }
        bool last = i == layout.count - 1;
        int limit = last && layout.truncated ? MAX_WIDTH - ellipsis : MAX_WIDTH;
        if (width > limit) {
            fail("line wider than the screen", what);
        }
        pos = (size_t)line.start + line.len;
        bool at_newline = pos < len && text[pos] == '\n';
        if (!last || !layout.truncated) {
            if (pos < len && !at_newline && width + char_width(text[pos]) <= MAX_WIDTH) {
                fail("line broken before it was full", what);
            }
        }
        if (at_newline && !(last && layout.truncated)) {
            pos++;
        }
    }
    bool consumed = pos == len;
    if (layout.truncated == consumed) {
        fail("truncated flag disagrees with what was shown", what);
    }
    if (layout.truncated != want_truncated || (want_lines >= 0 && layout.count != want_lines)) {
        printf("  got lines=%d truncated=%d\n", layout.count, layout.truncated);
        fail("unexpected layout", what);
    }
}

static void layout(void)
{
    char buf[PROMPT_TEXT_MAX + 1];
    check_layout("empty", "", false, 0);
    check_layout("short", "ls", false, 1);
    check_layout("two lines", "cd build\nmake", false, 2);
    check_layout("blank line kept", "a\n\nb", false, 3);
    check_layout("trailing newline adds no line", "ls\n", false, 1);
    check_layout("seven lines fit", "1\n2\n3\n4\n5\n6\n7", false, 7);
    check_layout("eighth line cut", "1\n2\n3\n4\n5\n6\n7\n8", true, 7);
    repeat(buf, 'W', PROMPT_TEXT_MAX);
    check_layout("wide letters cut", buf, true, 7);
    repeat(buf, 'i', 120);
    check_layout("narrow letters wrap", buf, false, -1);
    check_layout("long command wraps", "rm -rf /Users/someone/projects/funmedal/build/esp-idf/main/CMakeFiles "
                 "&& idf.py -p /dev/cu.usbmodem11301 build flash monitor", false, -1);
    for (int c = 0x20; c < 0x7f; c++) {
        if (char_width((char)c) <= 0) {
            printf("  missing U+%04x\n", c);
            fail("text font lacks a printable ascii glyph", "ascii");
        }
    }
}

static int text_width(const char *text, size_t len)
{
    int width = 0;
    for (size_t i = 0; i < len; i++) {
        width += char_width(text[i]);
    }
    return width;
}

static void check_fit(const char *what, const char *text, bool want_truncated)
{
    int ellipsis = font_find(&font_text, FONT_ELLIPSIS)->width;
    size_t len = strlen(text);
    bool truncated = !want_truncated;
    size_t kept = prompt_fit(&font_text, text, len, MAX_WIDTH, &truncated);
    if (truncated != want_truncated || kept > len) {
        printf("  got kept=%zu truncated=%d\n", kept, truncated);
        fail("unexpected fit", what);
        return;
    }
    if (!truncated) {
        if (kept != len || text_width(text, len) > MAX_WIDTH) {
            fail("untruncated text must be kept whole and fit", what);
        }
        return;
    }
    if (text_width(text, kept) + ellipsis > MAX_WIDTH) {
        fail("kept text and ellipsis wider than the screen", what);
    }
    if (text_width(text, kept + 1) + ellipsis <= MAX_WIDTH) {
        fail("kept text could hold one more character", what);
    }
}

static void fitting(void)
{
    check_fit("short tool", "Bash", false);
    check_fit("empty tool", "", false);
    check_fit("long mcp tool", "mcp__claude-in-chrome__javascri", true);
    check_fit("wide letters", "WWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW", true);
}

int main(void)
{
    parsing();
    layout();
    fitting();
    if (failures == 0) {
        printf("ALL PASSED\n");
    }
    return failures == 0 ? 0 : 1;
}
