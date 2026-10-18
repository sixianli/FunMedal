#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define LINK_VERSION 2
#define LINK_VERSION_DIGITS_MAX 4
#define LINK_VERSION_TEXT_MAX (LINK_VERSION_DIGITS_MAX + 1)

bool link_version_parse(const char *data, size_t len, uint16_t *version);
int link_version_text(uint16_t version, char *out, size_t size);
