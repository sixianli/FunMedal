import subprocess
import time

import pytest

from bridge_harness import (INSTALLED_BRIDGE, artifacts, card, encode, event, info_fields, run_dir,  # noqa: F401
                            write_summary)

ARTIFACTS_PREFIX = "guard"
ARTIFACTS_SUBDIR = "resident"
WATCH_S = 10.0


def resident_status():
    done = subprocess.run([str(INSTALLED_BRIDGE), "status"], capture_output=True, text=True, timeout=10)
    return done.returncode, done.stdout


@pytest.mark.parametrize("case", ["a session start during card tests cannot start the resident bridge and take "
                                  "the card"])
def test_session_start_cannot_take_the_card(case, card, run_dir):
    if not INSTALLED_BRIDGE.exists():
        pytest.skip(f"no installed bridge at {INSTALLED_BRIDGE}")
    hook = subprocess.run([str(INSTALLED_BRIDGE), "hook"], input=encode(event("SessionStart", "guard")),
                          capture_output=True, timeout=10)
    seen = []
    deadline = time.monotonic() + WATCH_S
    try:
        while time.monotonic() < deadline:
            code, out = resident_status()
            seen.append({"status_exit": code, "ble": info_fields(card.request("info"))["ble"]})
            time.sleep(0.5)
    finally:
        code, out = resident_status()
        if code == 0:
            subprocess.run([str(INSTALLED_BRIDGE), "quit"], capture_output=True, timeout=10)
        write_summary(run_dir, {"hook_exit": hook.returncode, "seen": seen, "resident_at_end": out})
    assert hook.returncode == 0, hook
    assert all(s["status_exit"] != 0 for s in seen), seen
    assert all(s["ble"] == "advertising" for s in seen), seen
