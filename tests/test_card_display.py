import glob
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import cardlink  # noqa: E402
from host_render import compare_screen, screen_tools  # noqa: E402,F401

W, H = 240, 320
STILL_SECONDS = 1.5
KEY_SEQUENCE = [(t, k) for k in ("ok", "up", "down") for t in ("press", "long", "release")]
ADC_STREAM_SECONDS = 120


@pytest.fixture(scope="module")
def artifacts():
    path = Path(os.environ.get("FM_ARTIFACTS") or ROOT / "artifacts" / f"fm12-{datetime.now():%Y%m%d-%H%M%S}")
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="module")
def card(artifacts):
    if not glob.glob("/dev/cu.usbmodem*"):
        pytest.skip("card USB serial port not found; press a key on the card to wake it")
    link = cardlink.Card()
    try:
        info = link.wait_ready()
        (artifacts / "card-info.txt").write_text(info + "\n", encoding="utf-8")
        yield link
        link.command("board")
    finally:
        (artifacts / "serial.log").write_text("\n".join(cardlink.compact_log(link.log)) + "\n", encoding="utf-8")
        link.close()


def capture(card, artifacts, name):
    meta, width, height, data = card.snapshot()
    assert (width, height) == (W, H)
    pixels = cardlink.rgb565_wire_to_rgb(data)
    png = artifacts / f"{name}.png"
    cardlink.write_png(png, width, height, pixels)
    record = {"name": name, "meta": meta, "png": png.name, "sha256": hashlib.sha256(png.read_bytes()).hexdigest()}
    with (artifacts / "snapshots.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return meta, pixels


def at(pixels, x, y):
    return pixels[y * W + x]


@pytest.mark.parametrize("case", ["[SC-10] with no bridge connected the card draws the unlinked screen byte for "
                                  "byte as the host renderer draws it"])
def test_unlinked_screen_matches_host(case, card, artifacts, screen_tools):
    card.command("board")
    rows = info_fields(card.command("rows"))
    result = compare_screen(card, screen_tools, artifacts, "unlinked", "unlinked", quiet=True)
    (artifacts / "unlinked.json").write_text(json.dumps({"rows": rows, **result}), encoding="utf-8")
    assert rows["linked"] == "0", f"a bridge is connected to the card: {rows}"
    assert result["meta"]["mode"] == "unlinked", result
    assert result["differing_pixels"] == 0, result


@pytest.mark.parametrize("case", ["[FM-12] test pattern marks each corner"])
def test_pattern_marks_corners(case, card, artifacts):
    card.command("pattern")
    meta, pixels = capture(card, artifacts, "pattern")
    assert meta["pattern"] == "1"
    white, black = (255, 255, 255), (0, 0, 0)
    for x in range(W):
        assert at(pixels, x, 0) == white and at(pixels, x, H - 1) == white
    for y in range(H):
        assert at(pixels, 0, y) == white and at(pixels, W - 1, y) == white
    assert at(pixels, 20, 20) == (255, 0, 0)
    assert at(pixels, W - 21, 20) == (0, 255, 0)
    assert at(pixels, 20, H - 21) == (0, 0, 255)
    assert at(pixels, W - 21, H - 21) == white
    assert at(pixels, W // 2, 61) == white and at(pixels, W // 2 - 20, 61) == black
    assert at(pixels, W // 2 - 20, 115) == white
    card.command("board")


@pytest.mark.parametrize("case", ["[FM-12] thirty back-to-back snapshots of the still test pattern arrive complete "
                                  "and identical"])
def test_back_to_back_snapshots_complete(case, card, artifacts):
    card.command("pattern")
    outcomes = []
    for i in range(30):
        try:
            meta, width, height, data = card.snapshot()
            outcomes.append({"i": i, "sha256": hashlib.sha256(data).hexdigest(), "meta": meta})
        except (RuntimeError, TimeoutError) as error:
            outcomes.append({"i": i, "error": str(error)})
            card.command("info", timeout=30.0)
    (artifacts / "back-to-back.jsonl").write_text("".join(json.dumps(o) + "\n" for o in outcomes), encoding="utf-8")
    card.command("board")
    assert [o for o in outcomes if "error" in o] == []
    assert len({o["sha256"] for o in outcomes}) == 1


def info_fields(line):
    return dict(p.split("=", 1) for p in line.split()[2:] if "=" in p)


def read_until_quiet(card, quiet_s, limit_s, ignore=lambda line: False):
    lines = []
    end = time.monotonic() + limit_s
    last = time.monotonic()
    while time.monotonic() < end:
        line = card.read_line(min(end, last + quiet_s))
        if line is None:
            break
        lines.append(line)
        if not ignore(line):
            last = time.monotonic()
    return lines


@pytest.mark.parametrize("case", ["[FM-12] the display task draws nothing while the still test pattern stays the "
                                  "same and redraws the whole screen once after the panel settings change"])
def test_display_task_redraws_only_changes(case, card, artifacts):
    card.command("pattern")
    time.sleep(0.5)
    first = info_fields(card.command("info"))
    time.sleep(STILL_SECONDS)
    still = info_fields(card.command("info"))
    assert card.command("lcd invert 1").startswith("OK lcd invert=1")
    time.sleep(0.5)
    rebuilt = info_fields(card.command("info"))
    time.sleep(STILL_SECONDS)
    last = info_fields(card.command("info"))
    record = {"first": first, "still": still, "rebuilt": rebuilt, "last": last}
    (artifacts / "display-task.json").write_text(json.dumps(record), encoding="utf-8")
    card.command("board")
    assert first["pattern"] == "1", first
    assert still["draws"] == first["draws"], record
    assert int(rebuilt["draws"]) == int(still["draws"]) + 1, record
    assert last["draws"] == rebuilt["draws"], record
    assert last["draw_errors"] == first["draw_errors"], record


@pytest.mark.parametrize("case", ["[FM-12] key sampling keeps its pace while the Mac stops reading"])
def test_key_sampling_survives_unread_backlog(case, card, artifacts):
    snaps = 16
    card.command("board")
    before = info_fields(card.command("info"))
    card.command("adc 60")
    for _ in range(snaps):
        card.send("snap")
    time.sleep(16)
    lines = read_until_quiet(card, quiet_s=3.0, limit_s=90.0, ignore=lambda line: line.startswith("ADC"))
    ends = [l for l in lines if l.startswith("SNAP END") or l == "ERR snap write timed out"]
    card.command("adc 1")
    after = info_fields(card.command("info"))
    read_until_quiet(card, quiet_s=1.5, limit_s=5.0)
    (artifacts / "unread-backlog.json").write_text(json.dumps({"ends": ends, "before": before, "info": after}), encoding="utf-8")
    assert any(e.startswith("ERR") or "write_timeouts=0" not in e for e in ends), ends
    assert int(after["key_gap_ms"]) < 100, after
    assert int(after["events_dropped"]) - int(before["events_dropped"]) > 0, (before, after)
    unanswered = int(after["replies_dropped"]) - int(before["replies_dropped"])
    assert len(ends) + unanswered == snaps, (len(ends), unanswered)
    assert after["adc_errors"] == before["adc_errors"], (before, after)


@pytest.mark.parametrize("case", ["[FM-12] console rejects malformed commands"])
@pytest.mark.parametrize("command", ["state idle", "frame 0", "board now", "bench 0 400", "fill 12345g", "adc 0",
                                     "nonsense"])
def test_console_rejects_malformed_commands(case, command, card):
    line = card.request(command)
    assert line.startswith("ERR "), line
    assert card.command("info").startswith("OK info fw=funmedal-0.1")


@pytest.mark.skipif(not os.environ.get("FM_KEY_PRESS_SECONDS"), reason="needs a person to press the keys; set FM_KEY_PRESS_SECONDS")
@pytest.mark.parametrize("case", ["[FM-12] person presses top, middle, bottom key"])
def test_physical_key_presses(case, card, artifacts):
    seconds = float(os.environ["FM_KEY_PRESS_SECONDS"])
    before = info_fields(card.command("info"))
    card.command(f"adc {ADC_STREAM_SECONDS}")
    (artifacts / "key-press-ready.txt").write_text(f"{time.time():.3f}\n", encoding="utf-8")
    deadline = time.monotonic() + seconds
    events, pressed_samples, idle_samples = [], [], 0
    while time.monotonic() < deadline and len(events) < len(KEY_SEQUENCE):
        line = card.read_line(deadline)
        if line is None:
            continue
        if line.startswith("KEY "):
            events.append(tuple(line.split()[1:3]))
        elif line == "ADC END":
            card.send(f"adc {ADC_STREAM_SECONDS}")
        elif line.startswith("ADC "):
            if line.split()[3] == "none":
                idle_samples += 1
            else:
                pressed_samples.append(line)
    card.command("adc 1")
    after = info_fields(card.command("info"))
    read_until_quiet(card, quiet_s=1.5, limit_s=5.0)
    (artifacts / "key-events.txt").write_text("\n".join(" ".join(e) for e in events) + "\n", encoding="utf-8")
    record = {"before": before, "after": after, "idle_samples": idle_samples, "pressed_samples": pressed_samples}
    (artifacts / "key-adc.json").write_text(json.dumps(record), encoding="utf-8")
    assert events == KEY_SEQUENCE
    assert after["adc_errors"] == before["adc_errors"], (before, after)
