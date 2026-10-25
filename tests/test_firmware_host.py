import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE_MAIN = ROOT / "firmware" / "main"
KEY_SOURCES = [FIRMWARE_MAIN / "keys.c", FIRMWARE_MAIN / "key_levels.c"]
KEY_CAPTURE = ROOT / "notes" / "data" / "key-adc-2026-10-16.txt"
FONT_SOURCES = [FIRMWARE_MAIN / "font.c"]
GEN_FONTS = ROOT / "tools" / "gen_fonts.py"
OUTSIDE_LEVEL_ONE = ["\u5011", "\u8aaa", "\u30a2", "\U0001f600"]
LINK_SOURCES = [FIRMWARE_MAIN / "link_version.c"]
ROWS_SOURCES = [FIRMWARE_MAIN / "rows.c"]
HOST = ROOT / "tests" / "host"
SCREEN_SOURCES = [FIRMWARE_MAIN / f"{name}.c" for name in (
    "scene", "pet_art", "font", "link_version", "draw", "dirty", "screen", "theme_board", "rows")] + [HOST / "host_fonts.c"]
SIX_ROWS = [("waiting", "-", "FunMedal"), ("error", "-", "SiBlog"), ("running", "-", "himawari-agent"),
            ("running", "-", "\u7ed8\u672c\u5de5\u574a"), ("done", "5", "agent-skills"),
            ("done", "135", "\u4e00\u4e2a\u540d\u5b57\u7279\u522b\u7279\u522b\u957f\u7684\u9879\u76ee\u4f1a\u88ab\u622a\u77ed")]
W, H = 240, 320
C_STRING = re.compile(r'"((?:[^"\\\n]|\\.)*)"')


def build(source, binary, sources=KEY_SOURCES):
    subprocess.run(
        ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", f"-I{FIRMWARE_MAIN}",
         str(source), *map(str, sources), "-o", str(binary)],
        check=True,
    )


@pytest.mark.parametrize("case", ["[FM-12] key voltage mapping and debounce"])
def test_key_logic_on_host(case, tmp_path):
    binary = tmp_path / "test_keys"
    build(ROOT / "tests" / "host" / "test_keys.c", binary)
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    print(result.stdout)
    assert result.returncode == 0, result.stdout
    assert "ALL PASSED" in result.stdout


@pytest.mark.parametrize("case", ["[FM-12] recorded presses top, middle, bottom map to ok, up, down"])
def test_recorded_presses_replay(case, tmp_path):
    binary = tmp_path / "replay_keys"
    build(ROOT / "tests" / "host" / "replay_keys.c", binary)
    result = subprocess.run([str(binary)], stdin=KEY_CAPTURE.open("rb"), capture_output=True, check=True)
    lines = result.stdout.decode().splitlines()
    print("\n".join(lines))
    events = [tuple(line.split()[2:]) for line in lines if line.startswith("EVENT ")]
    expected = []
    for key in ("ok", "up", "down"):
        expected += [("press", key), ("long", key), ("release", key)]
    assert events == expected


@pytest.mark.parametrize("case", ["[SC-3] the card accepts only well formed session tables and heartbeats, counts "
                                  "the minutes of done rows itself and treats 90 silent seconds as a lost link"])
def test_rows_on_host(case, tmp_path):
    binary = tmp_path / "test_rows"
    build(ROOT / "tests" / "host" / "test_rows.c", binary, ROWS_SOURCES)
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    print(result.stdout)
    assert result.returncode == 0, result.stdout
    assert "ALL PASSED" in result.stdout


def level_one():
    found = []
    for high in range(0xB0, 0xD8):
        for low in range(0xA1, 0xFF):
            try:
                found.append(bytes([high, low]).decode("gb2312"))
            except UnicodeDecodeError:
                pass
    return found


def source_characters():
    texts = [text for path in sorted(FIRMWARE_MAIN.glob("*.c"))
             for text in C_STRING.findall(path.read_text(encoding="utf-8"))]
    return sorted({c for text in texts for c in text if ord(c) > 0x7e})


@pytest.fixture(scope="module")
def fonts(tmp_path_factory):
    out = tmp_path_factory.mktemp("fonts")
    done = subprocess.run([sys.executable, str(GEN_FONTS), "--out", str(out)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    binary = out / "test_fonts"
    build(ROOT / "tests" / "host" / "test_fonts.c", binary, FONT_SOURCES)
    return out


def check_font(fonts, name, expectations):
    listing = fonts / f"{name}.expect"
    listing.write_text("".join(f"{kind} {ord(c):x}\n" for kind, c in expectations), encoding="utf-8")
    result = subprocess.run([str(fonts / "test_fonts"), str(fonts / f"font_{name}.bin"), str(listing)],
                            capture_output=True, text=True)
    print(result.stdout)
    return result


@pytest.mark.parametrize("case, name", [
    ("[SC-3] the body font draws every GB2312 level-one character and a box for any other", "text"),
    ("[SC-3] the pixel font draws every GB2312 level-one character and a box for any other", "pixel"),
])
def test_full_font_covers_level_one(case, name, fonts):
    characters = level_one()
    assert len(characters) == 3755
    result = check_font(fonts, name, [("ink", c) for c in characters] + [("box", c) for c in OUTSIDE_LEVEL_ONE])
    assert result.returncode == 0 and "ALL PASSED" in result.stdout, result.stdout[-3000:]


@pytest.mark.parametrize("case, name", [
    ("[FM-14] the body font holds ASCII and every character the firmware source draws", "text"),
    ("[FM-14] the pixel font holds ASCII and every character the firmware source draws", "pixel"),
    ("[FM-14] the large font holds the digits and every character the firmware source draws", "large"),
])
def test_fonts_cover_source_text(case, name, fonts):
    wide = source_characters()
    assert len(wide) >= 20, wide
    base = "0123456789/" if name == "large" else [chr(c) for c in range(0x21, 0x7f)] + ["\u2026"]
    result = check_font(fonts, name, [("have", c) for c in [*base, *wide]])
    assert result.returncode == 0 and "ALL PASSED" in result.stdout, result.stdout[-3000:]


@pytest.mark.parametrize("case", ["[OB-13] the link version value must be a plain decimal number from 1 to 9999"])
def test_link_version_parsing_on_host(case, tmp_path):
    binary = tmp_path / "test_link_version"
    build(ROOT / "tests" / "host" / "test_link_version.c", binary, LINK_SOURCES)
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    print(result.stdout)
    assert result.returncode == 0, result.stdout
    assert "ALL PASSED" in result.stdout


@pytest.fixture(scope="module")
def screen_tools(fonts):
    build(HOST / "test_screen.c", fonts / "test_screen", SCREEN_SOURCES)
    build(HOST / "render_png.c", fonts / "render_png", SCREEN_SOURCES)
    return fonts


@pytest.fixture(scope="module")
def screen_artifacts():
    base = Path(os.environ.get("FM_ARTIFACTS") or ROOT / "artifacts" / f"sc-screens-{datetime.now():%Y%m%d-%H%M%S}")
    path = base.resolve() / "screens"
    path.mkdir(parents=True, exist_ok=True)
    return path


def render(tools, artifacts, name, mode="rows", rows=(), total=None, tick=0):
    lines = ["theme 0", f"tick {tick}", f"mode {mode}", f"total {len(rows) if total is None else total}"]
    lines += [f"row {state} {minutes} {title}" for state, minutes, title in rows]
    scene = tools / f"{name}.txt"
    scene.write_text("\n".join(lines) + "\n", encoding="utf-8")
    png, rgb = tools / f"{name}.png", tools / f"{name}.rgb"
    done = subprocess.run([str(tools / "render_png"), str(tools), str(scene), str(png), str(rgb)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    shutil.copy(png, artifacts / png.name)
    regions = [tuple(int(v) for v in line.split()[1:3]) for line in done.stdout.splitlines()
               if line.startswith("REGION ")]
    return rgb.read_bytes(), regions


def pixel(image, x, y):
    at = (y * W + x) * 3
    return image[at:at + 3]


def changed_pixels(a, b):
    return [(i // 3 % W, i // 3 // W) for i in range(0, len(a), 3) if a[i:i + 3] != b[i:i + 3]]


@pytest.mark.parametrize("case", ["[SC-6] between any two frames every changed pixel lies in a redrawn region, "
                                  "and animation, new names and new states redraw only their own slot"])
def test_dirty_regions_on_host(case, screen_tools):
    result = subprocess.run([str(screen_tools / "test_screen"), str(screen_tools)], capture_output=True, text=True)
    print(result.stdout)
    assert result.returncode == 0, result.stdout
    assert "ALL PASSED" in result.stdout


@pytest.mark.parametrize("case", ["[SC-6] seven sessions draw six slots and add +1 to the header, nothing else"])
def test_seventh_session_adds_plus_one(case, screen_tools, screen_artifacts):
    six, six_regions = render(screen_tools, screen_artifacts, "board-6", rows=SIX_ROWS)
    seven, seven_regions = render(screen_tools, screen_artifacts, "board-7", rows=SIX_ROWS, total=7)
    slots = [(24 + 36 * i, 36) for i in range(6)]
    assert six_regions == seven_regions == [(0, 24), *slots, (240, 80)]
    changed = changed_pixels(six, seven)
    assert changed, "the header shows no +1"
    assert all(y < 24 and x >= W // 2 for x, y in changed), changed[:10]


def ink_in(image, top, height, background):
    return sum(pixel(image, x, y) != background for y in range(top, top + height) for x in range(W))


@pytest.mark.parametrize("case, count", [
    ("[SC-3] one session fills the first slot and leaves the other five empty", 1),
    ("[SC-3] three sessions fill three slots and leave the other three empty", 3),
    ("[SC-3] six sessions fill all six slots", 6),
])
def test_sessions_fill_their_slots(case, count, screen_tools, screen_artifacts):
    image, regions = render(screen_tools, screen_artifacts, f"board-{count}", rows=SIX_ROWS[:count])
    background = pixel(image, 0, H - 1)
    for i, (top, height) in enumerate(regions[1:7]):
        ink = ink_in(image, top, height, background)
        assert (ink > 1000) == (i < count), (i, ink)


@pytest.mark.parametrize("case", ["[SC-10] the unlinked and the empty screens differ and draw no session slots"])
def test_unlinked_and_empty_screens(case, screen_tools, screen_artifacts):
    unlinked, unlinked_regions = render(screen_tools, screen_artifacts, "board-unlinked", mode="unlinked")
    empty, empty_regions = render(screen_tools, screen_artifacts, "board-empty", mode="empty")
    assert unlinked_regions == empty_regions == [(0, 24), (24, 216), (240, 80)]
    changed = changed_pixels(unlinked, empty)
    assert changed and all(24 <= y < 240 for _, y in changed)
    background = pixel(unlinked, 0, H - 1)
    for image in (unlinked, empty):
        assert ink_in(image, 24, 216, background) > 1000
