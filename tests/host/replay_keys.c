#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "keys.h"

#define STREAM_SAMPLE_MS (2 * KEYS_SAMPLE_MS)

static int parse_adc_mv(const char *line, int *mv)
{
    const char *at = strstr(line, "ADC ");
    if (at == NULL) {
        return 0;
    }
    char *end = NULL;
    long value = strtol(at + 4, &end, 10);
    if (end == at + 4) {
        return 0;
    }
    *mv = (int)value;
    return 1;
}

int main(void)
{
    keys_debouncer_t debouncer;
    keys_debouncer_init(&debouncer);
    char line[256];
    uint32_t now_ms = 0;
    int sample = 0;
    while (fgets(line, sizeof(line), stdin) != NULL) {
        int mv = 0;
        if (!parse_adc_mv(line, &mv)) {
            continue;
        }
        now_ms += STREAM_SAMPLE_MS;
        key_id_t raw = keys_classify(mv, keys_board_levels, keys_board_level_count, keys_board_tolerance_mv);
        key_event_t events[4];
        int n = keys_debounce_update(&debouncer, raw, now_ms, events, 4);
        for (int i = 0; i < n; i++) {
            printf("EVENT %d %s %s\n", sample, keys_event_name(events[i].type), keys_name(events[i].key));
        }
        sample++;
    }
    printf("SAMPLES %d\n", sample);
    return 0;
}
