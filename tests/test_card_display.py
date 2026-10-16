import glob
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import cardlink  # noqa: E402

W, H = 240, 320
PET_BOX = (24, 36, 24 + 32 * 6, 36 + 32 * 6)
LABEL_ROWS = (250, 293)
GLYPH_WIDTH = 30
STYLES = {
    "idle": {"label": "空闲", "background": (0x2B2D42, 0x2B2D42), "text": 0xEDF2F4},
    "working": {"label": "干活中", "background": (0x1D4E89, 0x1D4E89), "text": 0xFFFFFF},
    "waiting": {"label": "等你批准", "background": (0xFF9F1C, 0xFFBF69), "text": 0x1A1C2C},
    "done": {"label": "完成了", "background": (0x2A9D8F, 0x2A9D8F), "text": 0xFFFFFF},
    "error": {"label": "出错了", "background": (0x9B2226, 0x9B2226), "text": 0xFFFFFF},
}
VALID_ROW = re.compile(r"SNAP \d+ [0-9a-f]{%d}" % (W * 4))
KEY_SEQUENCE = [(t, k) for k in ("ok", "up", "down") for t in ("press", "long", "release")]


def compact_log(lines):
    out, run = [], 0
    for line in lines:
        if VALID_ROW.fullmatch(line):
            run += 1
            continue
        if run:
            out.append(f"# {run} valid SNAP rows omitted; pixels are in the PNG files")
            run = 0
        out.append(line)
    if run:
        out.append(f"# {run} valid SNAP rows omitted; pixels are in the PNG files")
    return out


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
        link.command("frame auto")
        link.command("state idle")
    finally:
        (artifacts / "serial.log").write_text("\n".join(compact_log(link.log)) + "\n", encoding="utf-8")
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


@pytest.mark.parametrize("case", ["[FM-12] five states render distinct animated scenes"])
def test_states_render_distinct_scenes(case, card, artifacts):
    images = {}
    for state, style in STYLES.items():
        for frame in (0, 1):
            card.command(f"frame {frame}")
            card.command(f"state {state}")
            meta, pixels = capture(card, artifacts, f"{state}-{frame}")
            assert (meta["state"], meta["frame"], meta["pattern"]) == (state, str(frame), "0")
            background = cardlink.rgb888_to_565_rgb(style["background"][frame])
            for x, y in ((0, 0), (W - 1, 0), (0, H - 1), (W - 1, H - 1)):
                assert at(pixels, x, y) == background, (state, frame, x, y)
            x0, y0, x1, y1 = PET_BOX
            pet = sum(at(pixels, x, y) != background for y in range(y0, y1) for x in range(x0, x1))
            assert pet > (x1 - x0) * (y1 - y0) // 20, (state, frame, pet)
            text = cardlink.rgb888_to_565_rgb(style["text"])
            ink = [x for y in range(*LABEL_ROWS) for x in range(W) if at(pixels, x, y) == text]
            assert ink, (state, frame)
            expected_width = len(style["label"]) * GLYPH_WIDTH
            assert expected_width - 12 <= max(ink) - min(ink) + 1 <= expected_width, (state, frame, min(ink), max(ink))
            assert abs((min(ink) + max(ink)) / 2 - W / 2) <= 6, (state, frame, min(ink), max(ink))
            images[(state, frame)] = pixels
    for state in STYLES:
        assert images[(state, 0)] != images[(state, 1)], state
    keys = list(images)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if a[0] != b[0]:
                assert images[a] != images[b], (a, b)


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
    card.command("state idle")


@pytest.mark.parametrize("case", ["[FM-12] thirty back-to-back snapshots arrive complete"])
def test_back_to_back_snapshots_complete(case, card, artifacts):
    card.command("frame 0")
    card.command("state working")
    outcomes = []
    for i in range(30):
        try:
            meta, width, height, data = card.snapshot()
            outcomes.append({"i": i, "sha256": hashlib.sha256(data).hexdigest(), "meta": meta})
        except (RuntimeError, TimeoutError) as error:
            outcomes.append({"i": i, "error": str(error)})
            card.command("info", timeout=30.0)
    (artifacts / "back-to-back.jsonl").write_text("".join(json.dumps(o) + "\n" for o in outcomes), encoding="utf-8")
    card.command("frame auto")
    card.command("state idle")
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


def watch_frames(card, seconds):
    shown = set()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        last = info_fields(card.command("info"))
        shown.add(last["shown_frame"])
        time.sleep(0.1)
    return shown, last


@pytest.mark.parametrize("case", ["[FM-12] display task keeps drawing and alternates frames"])
def test_display_task_keeps_drawing(case, card, artifacts):
    card.command("state done")
    card.command("frame auto")
    first = info_fields(card.command("info"))
    shown_before, before_lcd = watch_frames(card, 2.0)
    assert card.command("lcd invert 1").startswith("OK lcd invert=1")
    rebuilt = info_fields(card.command("info"))
    shown_after, last = watch_frames(card, 2.0)
    record = {"first": first, "shown_before": sorted(shown_before), "before_lcd": before_lcd,
              "rebuilt": rebuilt, "shown_after": sorted(shown_after), "last": last}
    (artifacts / "display-task.json").write_text(json.dumps(record), encoding="utf-8")
    card.command("state idle")
    assert shown_before == {"0", "1"}
    assert int(before_lcd["draws"]) - int(first["draws"]) >= 3
    assert shown_after == {"0", "1"}
    assert int(last["draws"]) - int(rebuilt["draws"]) >= 3
    assert last["draw_errors"] == first["draw_errors"]


@pytest.mark.parametrize("case", ["[FM-12] key sampling keeps its pace while the Mac stops reading"])
def test_key_sampling_survives_unread_backlog(case, card, artifacts):
    snaps = 16
    card.command("frame 0")
    card.command("state working")
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
    card.command("frame auto")
    card.command("state idle")
    assert any(e.startswith("ERR") or "write_timeouts=0" not in e for e in ends), ends
    assert int(after["key_gap_ms"]) < 100, after
    assert int(after["events_dropped"]) - int(before["events_dropped"]) > 0, (before, after)
    unanswered = int(after["replies_dropped"]) - int(before["replies_dropped"])
    assert len(ends) + unanswered == snaps, (len(ends), unanswered)


@pytest.mark.parametrize("case", ["[FM-12] console rejects malformed commands"])
@pytest.mark.parametrize("command", ["state sleepy", "frame 2", "fill 12345g", "adc 0", "nonsense"])
def test_console_rejects_malformed_commands(case, command, card):
    line = card.request(command)
    assert line.startswith("ERR "), line
    assert card.command("info").startswith("OK info fw=funmedal-0.1")


@pytest.mark.skipif(not os.environ.get("FM_KEY_PRESS_SECONDS"), reason="needs a person to press the keys; set FM_KEY_PRESS_SECONDS")
@pytest.mark.parametrize("case", ["[FM-12] person presses top, middle, bottom key"])
def test_physical_key_presses(case, card, artifacts):
    seconds = float(os.environ["FM_KEY_PRESS_SECONDS"])
    (artifacts / "key-press-ready.txt").write_text(f"{time.time():.3f}\n", encoding="utf-8")
    deadline = time.monotonic() + seconds
    events = []
    while time.monotonic() < deadline and len(events) < len(KEY_SEQUENCE):
        line = card.read_line(deadline)
        if line is not None and line.startswith("KEY "):
            events.append(tuple(line.split()[1:3]))
    (artifacts / "key-events.txt").write_text("\n".join(" ".join(e) for e in events) + "\n", encoding="utf-8")
    assert events == KEY_SEQUENCE
