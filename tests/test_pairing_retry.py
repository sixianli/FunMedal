import time

import pytest

from bridge_harness import (Bridge, artifacts, bridge, card, card_lines, run_dir, wait_until,  # noqa: F401
                            write_summary)

ARTIFACTS_PREFIX = "ob14"
ARTIFACTS_SUBDIR = "pairing-retry"
PAIRING_ATTEMPTS = 3
QUIET_S = 15.0
ENCRYPTION_INSUFFICIENT = "Code=15"


def failures(bridge):
    return [e for e in bridge.log_entries() if e.get("kind") == "version_unreadable"]


def refuse(card, times):
    assert card.request(f"ble reject version {times}") == f"OK ble reject version {times}"


def reconnect(card, bridge, connects_before):
    assert card.request("ble disconnect") == "OK ble disconnect"
    return wait_until(lambda: (s := bridge.status()) and s["card"] == "connected" and s["connects"] > connects_before
                      and s, 60.0, "bridge connected again after the card dropped the link")


@pytest.mark.parametrize("case", ["[OB-14] after one failed pairing the bridge reconnects by itself and gets in"])
def test_one_failure_reconnects(case, card, bridge, run_dir):
    refuse(card, 1)
    start = card.mark()
    status = bridge.start()
    summary = {"status": status, "failures": failures(bridge), "refused": card_lines(card, start, "BLE reject")}
    write_summary(run_dir, summary)
    assert status["card"] == "connected" and status["connects"] == 2, summary
    assert summary["refused"] == ["BLE reject version read"], summary
    assert [(ENCRYPTION_INSUFFICIENT in e["error"], e["pairing_failures"]) for e in summary["failures"]] == [(True, 1)]


@pytest.mark.parametrize("case", ["[OB-14] the failure count starts again after the bridge gets in, so two short runs "
                                  "of failures never add up to the limit"])
def test_count_resets_after_success(case, card, bridge, run_dir):
    refuse(card, PAIRING_ATTEMPTS - 1)
    first = bridge.start()
    refuse(card, PAIRING_ATTEMPTS - 1)
    second = reconnect(card, bridge, first["connects"])
    summary = {"first": first, "second": second, "failures": failures(bridge)}
    write_summary(run_dir, summary)
    assert first["connects"] == PAIRING_ATTEMPTS, summary
    assert second["card"] == "connected", summary
    assert [e["pairing_failures"] for e in summary["failures"]] == [1, 2, 1, 2], summary


@pytest.mark.parametrize("case", ["[OB-14] after three failed pairings in a row the bridge stops trying until it "
                                  "is restarted, and a restarted bridge gets in"])
def test_stops_after_limit(case, card, run_dir):
    stuck = Bridge(run_dir)
    summary = {}
    try:
        refuse(card, PAIRING_ATTEMPTS + 2)
        start = card.mark()
        stuck.launch()
        first = wait_until(lambda: (s := stuck.status()) and s["card"] == "pairing_failed" and s, 60.0,
                           "bridge giving up on pairing")
        time.sleep(QUIET_S)
        summary = {"first": first, "after_quiet": stuck.status(), "failures": failures(stuck),
                   "connected_lines": card_lines(card, start, "BLE connected")}
    finally:
        stuck.quit()
        stuck.activity.unlink(missing_ok=True)
        summary["cleared"] = card.request("ble reject none")
    restarted = Bridge(run_dir / "restarted")
    (run_dir / "restarted").mkdir(exist_ok=True)
    try:
        summary["restarted"] = restarted.start()
    finally:
        write_summary(run_dir, summary)
        restarted.quit()
        restarted.activity.unlink(missing_ok=True)
    after = summary["after_quiet"]
    assert after["card"] == "pairing_failed" and after["connects"] == PAIRING_ATTEMPTS, summary
    assert len(summary["connected_lines"]) == PAIRING_ATTEMPTS, summary
    assert [e["pairing_failures"] for e in summary["failures"]] == list(range(1, PAIRING_ATTEMPTS + 1)), summary
    assert summary["cleared"] == "OK ble reject none", summary
    assert summary["restarted"]["card"] == "connected" and summary["restarted"]["connects"] == 1, summary
