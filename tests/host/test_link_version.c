#include <stdio.h>
#include <string.h>

#include "link_version.h"

static int failures;

static void expect(const char *data, size_t len, bool accepted, uint16_t want)
{
    uint16_t got = 0;
    bool ok = link_version_parse(data, len, &got);
    if (ok != accepted || (accepted && got != want)) {
        printf("FAIL len=%zu accepted=%d got=%u\n", len, ok, (unsigned)got);
        failures++;
    }
}

int main(void)
{
    expect("2", 1, true, 2);
    expect("1", 1, true, 1);
    expect("10", 2, true, 10);
    expect("9999", 4, true, 9999);
    expect("", 0, false, 0);
    expect("0", 1, false, 0);
    expect("02", 2, false, 0);
    expect("10000", 5, false, 0);
    expect("-1", 2, false, 0);
    expect("+2", 2, false, 0);
    expect(" 2", 2, false, 0);
    expect("2 ", 2, false, 0);
    expect("2\n", 2, false, 0);
    expect("2\0", 2, false, 0);
    expect("2a", 2, false, 0);
    expect("a", 1, false, 0);
    char own[LINK_VERSION_TEXT_MAX];
    int len = link_version_text(LINK_VERSION, own, sizeof(own));
    uint16_t back = 0;
    if (len <= 0 || !link_version_parse(own, (size_t)len, &back) || back != LINK_VERSION) {
        printf("FAIL own version %d does not round trip\n", LINK_VERSION);
        failures++;
    }
    if (failures == 0) {
        printf("ALL PASSED\n");
    }
    return failures == 0 ? 0 : 1;
}
