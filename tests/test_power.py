import time

import pytest

from bridge_harness import artifacts, bash, bridge, card, event, hook_times, info_fields, run_dir, write_summary  # noqa: F401
from test_rows import prompt

ARTIFACTS_PREFIX = "sc9"
ARTIFACTS_SUBDIR = "power"
IDLE_S = 5
ALERT_SHOWS_S = 6.0
HELLO_SHOWS_S = 3.0
WAKE_WITHIN_S = 3.0
DEFAULT_IDLE_S = 300


def power(card):
    return info_fields(card.request("power"))


def draws(card):
    return int(info_fields(card.request("info"))["draws"])


def lines(card, start):
    return [line for _, line in card.since(start)]


def wait_dark(card, start):
    return card.wait_for(lambda line: line == "POWER dark", ALERT_SHOWS_S + IDLE_S + 3.0, start)


@pytest.mark.parametrize("case", ["[SC-9] on battery the card goes dark after the idle time, a finished session "
                                  "leaves it dark, a fresh waiting row lights it within 3 s with the alert and the "
                                  "chime, a key press only lights it, and plugging into a computer lights it"])
def test_dark_and_wake_on_card(case, card, bridge, run_dir):
    summary = {}
    try:
        mark = card.mark()
        bridge.start()
        card.wait_for(lambda line: line.startswith("EVENT hello"), 5.0, mark)
        prompt(bridge, "w", "web")
        prompt(bridge, "d", "docs")
        bridge.settle_rows([("running", "web"), ("running", "docs")])
        time.sleep(HELLO_SHOWS_S)
        assert power(card)["screen"] == "on"
        assert card.request("power battery on") == "OK power battery on"
        assert card.request(f"idle {IDLE_S}") == f"OK idle {IDLE_S}"
        dark_mark = card.mark()
        set_t = time.time()
        _, dark_t, _ = wait_dark(card, dark_mark)
        summary["dark_after_s"] = dark_t - set_t
        summary["power_dark"] = power(card)
        summary["draws_dark"] = draws(card)

        done_mark = card.mark()
        bridge.hook(event("Stop", "d", stop_hook_active=False), project="docs")
        bridge.settle_rows([("running", "web"), ("done", "docs")])
        time.sleep(2.0)
        summary["after_done"] = lines(card, done_mark)
        summary["power_after_done"] = power(card)
        summary["draws_after_done"] = draws(card)

        alert_mark = card.mark()
        run = bridge.hook_async(event("PermissionRequest", "w", **bash("make deploy")), project="web")
        _, lit_t, lit = card.wait_for(lambda line: line.startswith("POWER lit"), WAKE_WITHIN_S + 2.0, alert_mark)
        card.wait_for(lambda line: line.startswith("AUDIO done"), ALERT_SHOWS_S, alert_mark)
        permission_t = hook_times(bridge, "PermissionRequest")
        summary["lit"] = lit
        summary["lit_latency_s"] = lit_t - permission_t[0] if permission_t else None
        summary["after_alert"] = lines(card, alert_mark)
        summary["power_after_alert"] = power(card)
        summary["draws_after_alert"] = draws(card)
        run.stop()

        key_mark = card.mark()
        wait_dark(card, key_mark)
        theme = card.request("theme")
        press_mark = card.mark()
        summary["key_reply"] = card.request("key up press")
        card.wait_for(lambda line: line.startswith("POWER lit"), WAKE_WITHIN_S, press_mark)
        time.sleep(1.0)
        summary["after_key"] = lines(card, press_mark)
        summary["theme_before_key"] = theme
        summary["theme_after_key"] = card.request("theme")

        usb_mark = card.mark()
        wait_dark(card, usb_mark)
        plug_mark = card.mark()
        assert card.request("power battery off") == "OK power battery off"
        card.wait_for(lambda line: line.startswith("POWER lit"), WAKE_WITHIN_S, plug_mark)
        summary["after_usb"] = lines(card, plug_mark)
        summary["power_after_usb"] = power(card)
    finally:
        card.request("power battery off")
        card.request(f"idle {DEFAULT_IDLE_S}")
        write_summary(run_dir, summary)
    assert IDLE_S - 0.3 <= summary["dark_after_s"] < IDLE_S + 2.0, summary
    assert summary["power_dark"]["screen"] == "off" and summary["power_dark"]["battery"] == "pretend", summary
    assert summary["power_dark"]["backlight"] == "0", summary
    assert not [line for line in summary["after_done"] if line.startswith(("POWER lit", "EVENT "))], summary
    assert summary["power_after_done"]["screen"] == "off" and summary["power_after_done"]["backlight"] == "0", summary
    assert summary["draws_after_done"] == summary["draws_dark"], summary
    assert summary["draws_after_alert"] > summary["draws_after_done"], summary
    assert summary["power_after_alert"]["backlight"] == "100", summary
    assert summary["lit"] == "POWER lit alert", summary
    assert summary["lit_latency_s"] is not None and 0 <= summary["lit_latency_s"] < WAKE_WITHIN_S, summary
    for line in ("EVENT wait web", "AUDIO play wait muted", "AUDIO done wait"):
        assert line in summary["after_alert"], summary
    assert summary["power_after_alert"]["screen"] == "on", summary
    assert summary["key_reply"] == "OK key up press", summary
    assert "POWER lit key" in summary["after_key"], summary
    assert not [line for line in summary["after_key"] if line.startswith("THEME")], summary
    assert summary["theme_after_key"] == summary["theme_before_key"], summary
    assert "POWER lit usb" in summary["after_usb"], summary
    assert summary["power_after_usb"]["screen"] == "on" and summary["power_after_usb"]["usb"] == "yes", summary


@pytest.mark.parametrize("case", ["[SC-9] plugged into a computer the card stays lit past the idle time"])
def test_usb_stays_lit_on_card(case, card, run_dir):
    mark = card.mark()
    try:
        assert card.request(f"idle {IDLE_S}") == f"OK idle {IDLE_S}"
        time.sleep(IDLE_S + 2.0)
        state = power(card)
        seen = lines(card, mark)
    finally:
        card.request(f"idle {DEFAULT_IDLE_S}")
    write_summary(run_dir, {"power": state, "lines": seen})
    assert state["usb"] == "yes" and state["screen"] == "on", state
    assert "POWER dark" not in seen, seen
