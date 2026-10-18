#include "link_version.h"

#include <stdio.h>

bool link_version_parse(const char *data, size_t len, uint16_t *version)
{
    if (len == 0 || len > LINK_VERSION_DIGITS_MAX || data[0] == '0') {
        return false;
    }
    uint16_t value = 0;
    for (size_t i = 0; i < len; i++) {
        if (data[i] < '0' || data[i] > '9') {
            return false;
        }
        value = (uint16_t)(value * 10 + (data[i] - '0'));
    }
    *version = value;
    return true;
}

int link_version_text(uint16_t version, char *out, size_t size)
{
    return snprintf(out, size, "%u", (unsigned)version);
}
