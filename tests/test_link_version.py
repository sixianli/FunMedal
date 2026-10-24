import time

import pytest

from bridge_harness import (W, Bridge, artifacts, ble_writes, bridge, card, card_lines, info_fields, ink,  # noqa: F401
                            run_dir, snapshot, wait_until, write_summary)
from cardlink import rgb888_to_565_rgb

ARTIFACTS_PREFIX = "ob13"
ARTIFACTS_SUBDIR = "link-version"
LINK_VERSION = 2
MISMATCH_BACKGROUND = rgb888_to_565_rgb(0x9D0208)
TITLE_ROWS = (60, 92)
VERSION_ROWS = (120, 140)
HINT_ROWS = (170, 212)
QUIET_S = 15.0
MISMATCH_WAIT_S = 60.0
ADVICE = {"newer": (LINK_VERSION + 1, "update_firmware"), "older": (LINK_VERSION - 1, "update_bridge")}


def link_lines(card, start):
    return card_lines(card, start, "LINK ")


def mismatch_status(bridge):
    return wait_until(lambda: (s := bridge.status()) and s["card"] == "mismatch" and s, MISMATCH_WAIT_S,
                      "bridge reporting a version mismatch")


def version_entries(bridge):
    return [e for e in bridge.log_entries() if e.get("kind") == "version"]


@pytest.mark.parametrize("case", ["[OB-13] with the same link version the bridge reads the card's version, writes its "
                                  "own and then shows the state as before"])
def test_same_version_connects(case, card, bridge, run_dir):
    assert card.request("state error") == "OK state error"
    start = card.mark()
    status = bridge.start()
    card.wait_for(lambda line: line == "STATE idle", 10.0, start)
    summary = {"status": status, "link": link_lines(card, start), "writes": ble_writes(card, start),
               "info": info_fields(card.request("info")), "versions": version_entries(bridge)}
    write_summary(run_dir, summary)
    assert status["card_version"] == LINK_VERSION and status["bridge_version"] == LINK_VERSION, summary
    assert summary["link"] == [f"LINK peer={LINK_VERSION} own={LINK_VERSION} match=1"], summary
    assert summary["writes"] == ["idle"], summary
    assert summary["info"]["link"] == "match", summary
    assert [(e["card"], e["bridge"], e["match"]) for e in summary["versions"]] == [(LINK_VERSION, LINK_VERSION, True)]


@pytest.mark.parametrize("case, way", [
    ("[OB-13] a bridge newer than the card connects once, leaves the card saying to update its firmware, and comes "
     "back once a bridge with the same version starts", "newer"),
    ("[OB-13] a bridge older than the card connects once, leaves the card saying to update the bridge, and comes "
     "back once a bridge with the same version starts", "older"),
])
def test_version_mismatch(case, way, card, run_dir):
    pretend, advice = ADVICE[way]
    odd = Bridge(run_dir)
    summary = {}
    try:
        start = card.mark()
        odd.launch("--link-version", str(pretend))
        first = mismatch_status(odd)
        card.wait_for(lambda line: line.startswith("LINK "), 10.0, start)
        time.sleep(QUIET_S)
        shot = snapshot(card, run_dir, f"mismatch-{way}")
        summary = {"first": first, "after_quiet": odd.status(), "link": link_lines(card, start),
                   "writes": ble_writes(card, start), "info": info_fields(card.request("info")),
                   "versions": version_entries(odd), "png": shot["png"],
                   "background": shot["pixels"][5 * W + 5] == MISMATCH_BACKGROUND,
                   "ink": {name: ink(shot["pixels"], rows, MISMATCH_BACKGROUND)
                           for name, rows in (("title", TITLE_ROWS), ("versions", VERSION_ROWS), ("hint", HINT_ROWS))}}
    finally:
        odd.quit()
    recovered = Bridge(run_dir / "recovered")
    (run_dir / "recovered").mkdir(exist_ok=True)
    try:
        back = card.mark()
        summary["recovered"] = recovered.start()
        card.wait_for(lambda line: line.startswith("BLE state "), 10.0, back)
        summary["recovered_link"] = link_lines(card, back)
        summary["recovered_info"] = info_fields(card.request("info"))
    finally:
        write_summary(run_dir, summary)
        recovered.quit()
    after = summary["after_quiet"]
    assert after["card"] == "mismatch" and after["connects"] == 1, summary
    assert after["card_version"] == LINK_VERSION and after["bridge_version"] == pretend, summary
    assert summary["link"] == [f"LINK peer={pretend} own={LINK_VERSION} match=0"], summary
    assert summary["writes"] == [], summary
    assert summary["info"]["link"] == advice, summary
    assert [(e["card"], e["bridge"], e["match"]) for e in summary["versions"]] == [(LINK_VERSION, pretend, False)]
    assert summary["background"] and all(count > 50 for count in summary["ink"].values()), summary
    assert summary["recovered"]["card"] == "connected", summary
    assert summary["recovered_link"] == [f"LINK peer={LINK_VERSION} own={LINK_VERSION} match=1"], summary
    assert summary["recovered_info"]["link"] == "match", summary
