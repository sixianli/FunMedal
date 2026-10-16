#include <stdio.h>
#include <string.h>

#include "scene.h"

static int failures;

static void expect(const char *data, size_t len, bool accepted, pet_state_t want)
{
    pet_state_t got = PET_STATE_COUNT;
    bool ok = scene_state_from_bytes(data, len, &got);
    if (ok != accepted || (accepted && got != want)) {
        printf("FAIL len=%zu accepted=%d got=%d\n", len, ok, (int)got);
        failures++;
    }
}

int main(void)
{
    expect("idle", 4, true, PET_IDLE);
    expect("working", 7, true, PET_WORKING);
    expect("waiting", 7, true, PET_WAITING);
    expect("done", 4, true, PET_DONE);
    expect("error", 5, true, PET_ERROR);
    expect("", 0, false, 0);
    expect("idl", 3, false, 0);
    expect("idlex", 5, false, 0);
    expect("idle\0xx", 7, false, 0);
    expect("idle\0", 5, false, 0);
    expect("idle\n", 5, false, 0);
    expect("IDLE", 4, false, 0);
    expect("donework", 8, false, 0);
    for (int i = 0; i < PET_STATE_COUNT; i++) {
        const char *name = scene_state_name((pet_state_t)i);
        pet_state_t back = PET_STATE_COUNT;
        if (!scene_state_from_name(name, &back) || back != (pet_state_t)i) {
            printf("FAIL round trip %s\n", name);
            failures++;
        }
    }
    if (failures == 0) {
        printf("ALL PASSED\n");
    }
    return failures == 0 ? 0 : 1;
}
