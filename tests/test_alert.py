import re
import time

import pytest

from bridge_harness import (ROOT, artifacts, bash, bridge, card, card_rows, event, hook_times,  # noqa: F401
                            real_session, request_touch, run_dir, wait_session_ready, wait_until, write_summary)
from host_render import compare_screen, scene_of, screen_tools  # noqa: F401
from test_rows import fresh_rows, prompt

ARTIFACTS_PREFIX = "sc5"
ARTIFACTS_SUBDIR = "alert"
ALERT_WITHIN_S = 3.0
ALERT_SHOWS_S = 6.0
CHEER_SHOWS_S = 3.0
THEMES = (0, 1, 2, 3)
FIRMWARE_MAIN = ROOT / "firmware" / "main"
KEY_PRESSES = [f"key {key} {how}" for key in ("ok", "up", "down") for how in ("press", "long")]
ON_KEY_CALLS = {"app_get_scene", "on_pairing_key", "app_set_theme", "screen_next_theme"}


def card_events(card, start):
    return [line for _, line in card.since(start) if line.startswith(("EVENT ", "AUDIO "))]


def set_theme(card, theme):
    mark = card.mark()
    assert card.request(f"theme {theme}") == f"OK theme {theme}"
    card.wait_for(lambda line, n=theme: line == f"THEME {n}", 5.0, mark)


def screen_now(card, tools, run_dir, name):
    return compare_screen(card, tools, run_dir, name, *scene_of(card_rows(card)))


@pytest.mark.parametrize("case", ["[SC-5] the alert command shows the waiting and the error alert in all four themes "
                                  "byte for byte as the host draws them, while the character rises and once risen, "
                                  "and plays the matching chime, muted for the test, to its end"])
def test_alert_command_on_card(case, card, run_dir, screen_tools):
    screens = []
    lines = []
    try:
        for theme in THEMES:
            set_theme(card, theme)
            for kind, name in (("wait", "FunMedal"), ("error", "SiBlog")):
                mark = card.mark()
                assert card.request(f"alert {kind} {name}") == f"OK alert {kind} {name}"
                shown = time.monotonic()
                rising = screen_now(card, screen_tools, run_dir, f"theme-{theme}-{kind}-rising")
                card.wait_for(lambda line: line == f"AUDIO play {kind} muted", 2.0, mark)
                risen = screen_now(card, screen_tools, run_dir, f"theme-{theme}-{kind}")
                card.wait_for(lambda line: line == f"AUDIO done {kind}", ALERT_SHOWS_S, mark)
                screens += [rising, risen]
                lines.append(card_events(card, mark))
                time.sleep(max(0.0, shown + ALERT_SHOWS_S + 0.5 - time.monotonic()))
    finally:
        write_summary(run_dir, {"screens": [strip(s) for s in screens], "lines": lines})
        set_theme(card, 0)
    assert [s["differing_pixels"] for s in screens] == [0] * len(screens), [strip(s) for s in screens]
    assert all(s["meta"]["event"] in ("wait", "error") for s in screens), [strip(s) for s in screens]
    assert any(int(s["meta"]["event_ms"]) < 400 for s in screens[::2]), [strip(s) for s in screens]
    for entry, (kind, name) in zip(lines, [("wait", "FunMedal"), ("error", "SiBlog")] * len(THEMES)):
        assert entry == [f"EVENT {kind} {name}", f"AUDIO play {kind} muted", f"AUDIO done {kind}"], lines


def strip(screen):
    return {k: screen[k] for k in ("theme", "differing_pixels", "rows")} | {
        "event": screen["meta"]["event"], "event_ms": screen["meta"]["event_ms"]}


@pytest.mark.parametrize("case", ["[SC-5] a new alert while one shows starts over with the new sentence and cuts the "
                                  "old chime, and a cheer or hello cannot push an alert away"])
def test_alert_restarts_and_outranks(case, card, run_dir):
    mark = card.mark()
    assert card.request("alert wait FunMedal") == "OK alert wait FunMedal"
    card.wait_for(lambda line: line == "AUDIO play wait muted", 2.0, mark)
    assert card.request("alert error SiBlog") == "OK alert error SiBlog"
    assert card.request("alert cheer agent-skills") == "ERR alert a more important event is showing"
    assert card.request("alert hello FunMedal") == "ERR alert a more important event is showing"
    card.wait_for(lambda line: line == "AUDIO done error", ALERT_SHOWS_S, mark)
    lines = card_events(card, mark)
    write_summary(run_dir, {"lines": lines})
    assert lines == ["EVENT wait FunMedal", "AUDIO play wait muted", "EVENT error SiBlog", "AUDIO cut wait",
                     "AUDIO play error muted", "AUDIO done error"], lines


@pytest.mark.parametrize("case", ["[SC-5] through the bridge a link waves hello, a fresh waiting row alerts within "
                                  "3 s, every frame with fresh rows gives exactly one alert that uses the error "
                                  "sentence when any fresh row is an error, and running to done cheers"])
def test_bridge_events_on_card(case, card, bridge, run_dir):
    mark = card.mark()
    bridge.start()
    hello = card.wait_for(lambda line: line.startswith("EVENT "), 5.0, mark)[2]
    prompt(bridge, "w", "web")
    prompt(bridge, "e", "api")
    prompt(bridge, "d", "docs")
    bridge.settle_rows([("running", "api"), ("running", "docs"), ("running", "web")])
    frames_before = len(bridge.frames())
    time.sleep(CHEER_SHOWS_S)
    mark = card.mark()
    sent = time.time()
    run = bridge.hook_async(event("PermissionRequest", "w", **bash("make deploy")), project="web")
    _, waiting_t, waiting = card.wait_for(lambda line: line.startswith("EVENT "), ALERT_WITHIN_S + 2.0, mark)
    permission_t = hook_times(bridge, "PermissionRequest")
    bridge.hook(event("StopFailure", "e", error="rate_limit"), project="api")
    bridge.hook(event("Notification", "w", notification_type="permission_prompt", message="needs network"),
                project="web")
    bridge.settle_rows([("waiting", "web"), ("error", "api"), ("running", "docs")])
    time.sleep(1.0)
    last_alert_t = max(t for t, line in card.since(mark) if line.startswith("EVENT "))
    time.sleep(max(0.0, last_alert_t + ALERT_SHOWS_S + 0.5 - time.time()))
    mark_cheer = card.mark()
    bridge.hook(event("Stop", "d", stop_hook_active=False), project="docs")
    bridge.settle_rows([("waiting", "web"), ("error", "api"), ("done", "docs")])
    time.sleep(1.0)
    run.stop()
    frames = bridge.frames()[frames_before:]
    alerts = [line for _, line in card.since(mark) if line.startswith("EVENT ")]
    cheers = [line for _, line in card.since(mark_cheer) if line.startswith("EVENT cheer")]
    fresh = [fresh_rows(f) for f in frames if fresh_rows(f)]
    expected = []
    for frame in frames:
        names = fresh_rows(frame)
        if names:
            errors = [r["name"] for r in frame["rows"] if r["fresh"] and r["state"] == "error"]
            expected.append(f"EVENT error {errors[0]}" if errors else f"EVENT wait {names[0]}")
    summary = {"hello": hello, "waiting": waiting, "latency_s": waiting_t - permission_t[0] if permission_t else None,
               "sent": sent, "alerts": alerts, "cheers": cheers, "fresh": fresh, "expected": expected,
               "frames": frames}
    write_summary(run_dir, summary)
    assert hello.startswith("EVENT hello"), summary
    assert waiting == "EVENT wait web", summary
    assert summary["latency_s"] is not None and 0 <= summary["latency_s"] < ALERT_WITHIN_S, summary
    assert [a for a in alerts if not a.startswith("EVENT cheer")] == expected, summary
    assert "EVENT error api" in expected, summary
    assert cheers == ["EVENT cheer docs"], summary


@pytest.mark.parametrize("case", ["[SC-5] in a real Claude Code session a permission request alerts on the card "
                                  "within 3 s and the card plays the waiting chime"])
def test_real_permission_alerts(case, card, bridge, run_dir):
    summary = {}
    try:
        with real_session(bridge, run_dir) as (session, marker):
            wait_session_ready(session, bridge)
            mark = card.mark()
            request_touch(session, marker)
            _, alert_t, alert = card.wait_for(lambda line: line.startswith("EVENT wait"), 120.0, mark)
            card.wait_for(lambda line: line == "AUDIO done wait", ALERT_SHOWS_S, mark)
            session.wait_screen("Do you want to proceed", 10.0)
            keys_mark = card.mark()
            replies = [card.request(press) for press in KEY_PRESSES]
            time.sleep(2.0)
            still = bridge.waits()
            screen_still = session.wait_screen("Do you want to proceed", 5.0)
            key_lines = [line for _, line in card.since(keys_mark) if not line.startswith(("THEME", "SNAP"))]
            session.send("1", "1 (yes in terminal)")
            time.sleep(0.3)
            session.send("\r", "enter (confirm)")
            wait_until(marker.exists, 30.0, "marker file")
            permission_t = hook_times(bridge, "PermissionRequest")
            summary = {"alert": alert, "latency_s": alert_t - permission_t[0] if permission_t else None,
                       "lines": card_events(card, mark), "key_replies": replies, "waits_after_keys": still,
                       "screen_after_keys": bool(screen_still), "key_lines": key_lines, "status": bridge.status()}
    finally:
        write_summary(run_dir, summary)
        set_theme(card, 0)
    assert summary["alert"].startswith("EVENT wait"), summary
    assert summary["latency_s"] is not None and 0 <= summary["latency_s"] < ALERT_WITHIN_S, summary
    assert "AUDIO play wait muted" in summary["lines"] and "AUDIO done wait" in summary["lines"], summary
    assert summary["key_replies"] == [f"OK {press}" for press in KEY_PRESSES], summary
    assert summary["waits_after_keys"] and len(summary["waits_after_keys"]) == 1, summary


def function_body(source, name):
    start = re.search(rf"^static \w+ {name}\([^)]*\)\n{{\n", source, re.M)
    assert start, name
    end = source.index("\n}\n", start.end())
    return source[start.end():end]


@pytest.mark.parametrize("case", ["[SC-11] the firmware has no path from a key to approving or refusing a Claude "
                                  "request: keys only confirm pairing or turn the theme, and the card's bluetooth "
                                  "service offers the Mac nothing to read but the link version"])
def test_no_key_decides(case, run_dir):
    main = (FIRMWARE_MAIN / "main.c").read_text(encoding="utf-8")
    ble = (FIRMWARE_MAIN / "ble_link.c").read_text(encoding="utf-8")
    on_key = function_body(main, "on_key")
    pairing = function_body(main, "on_pairing_key")
    calls = set(re.findall(r"\b(\w+)\s*\(", on_key)) - {"if"}
    pairing_calls = set(re.findall(r"\b(\w+)\s*\(", pairing)) - {"if"}
    table = ble[ble.index("static const struct ble_gatt_svc_def services[]"):]
    table = table[:table.index("};") + 2]
    characteristics = re.findall(r"\.uuid = &(\w+)\.u,\s*\.access_cb = (\w+),\s*\.flags = ([^,]+),", table)
    entries = table.count(".uuid = &") - 1
    version = function_body(ble, "version_access")
    sources = {p.name: p.read_text(encoding="utf-8") for p in FIRMWARE_MAIN.glob("*.c")}
    senders = sorted(name for name, text in sources.items()
                     if re.search(r"ble_gatts_notify|ble_gatts_indicate|ble_gatts_chr_updated|ble_gattc_\w+", text))
    words = sorted(name for name, text in sources.items() if re.search(r"\b(approve|deny|DECISION|PROMPT)\b", text))
    summary = {"entries": entries, "on_key_calls": sorted(calls), "pairing_calls": sorted(pairing_calls),
               "characteristics": characteristics, "senders": senders, "decision_words": words,
               "version_reads": re.findall(r"os_mbuf_append\([^;]*;", version)}
    write_summary(run_dir, summary)
    assert calls == ON_KEY_CALLS, summary
    assert pairing_calls == {"ble_link_confirm_pairing"}, summary
    assert entries == 2, summary
    assert [(uuid, cb) for uuid, cb, _ in characteristics] == [("version_uuid", "version_access"),
                                                               ("rows_uuid", "rows_access")], summary
    assert all("NOTIFY" not in flags and "INDICATE" not in flags for _, _, flags in characteristics), summary
    assert characteristics[1][2].strip() == "SECURE_FLAGS_WRITE", summary
    assert len(summary["version_reads"]) == 1 and "ctxt->om, own," in summary["version_reads"][0], summary
    assert senders == [], summary
    assert words == [], summary
