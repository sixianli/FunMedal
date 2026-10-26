import math
import os
import re
import shutil
import struct
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from host_render import SCREEN_SOURCES, generate_assets, render as render_screen

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


@pytest.mark.parametrize("case", ["[SC-12] the character's daily loop lies 5 s, taps the desk three times, tilts "
                                  "with a question mark, mumbles with a bubble and dozes with drifting zZ, on a "
                                  "virtual clock"])
def test_character_timeline_on_host(case, tmp_path):
    binary = tmp_path / "test_character"
    build(HOST / "test_character.c", binary, SCREEN_SOURCES)
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    print(result.stdout)
    assert result.returncode == 0, result.stdout
    assert "ALL PASSED" in result.stdout


CHIME_RATE = 16000
CHIME_PEAK = 0.25 * 32767
CHIMES = {"wait": [(784.0, 0.22, 0.10), (1046.5, 0.6, 0.18)], "error": [(1046.5, 0.22, 0.10), (784.0, 0.6, 0.18)]}


def chime_reference(notes):
    samples = []
    for hz, seconds, decay in notes:
        for i in range(int(CHIME_RATE * seconds)):
            t = i / CHIME_RATE
            envelope = min(1.0, t / 0.005) * math.exp(-t / decay)
            wave = math.sin(2 * math.pi * hz * t) + 0.3 * math.sin(4 * math.pi * hz * t)
            samples.append(round(envelope * wave / 1.3 * CHIME_PEAK))
    return samples


@pytest.mark.parametrize("case", ["[SC-5] the card synthesizes the ding-dong the owner picked: waiting goes G5 up to "
                                  "C6, error goes C6 down to G5, sample for sample within 2 steps of the sample "
                                  "they heard"])
def test_chime_on_host(case, tmp_path):
    binary = tmp_path / "test_chime"
    build(HOST / "test_chime.c", binary, [FIRMWARE_MAIN / "chime.c"])
    result = subprocess.run([str(binary), str(tmp_path)], capture_output=True, text=True)
    print(result.stdout)
    assert result.returncode == 0, result.stdout
    assert "ALL PASSED" in result.stdout
    for name, notes in CHIMES.items():
        data = (tmp_path / f"{name}.pcm").read_bytes()
        card = struct.unpack(f"<{len(data) // 2}h", data)
        reference = chime_reference(notes)
        assert len(card) == len(reference), name
        worst = max(abs(a - b) for a, b in zip(card, reference))
        assert worst <= 2, f"{name}: worst difference {worst}"
        assert max(abs(v) for v in card) <= CHIME_PEAK + 1, name


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
    generate_assets(fonts)
    build(HOST / "test_screen.c", fonts / "test_screen", SCREEN_SOURCES)
    build(HOST / "render_png.c", fonts / "render_png", SCREEN_SOURCES)
    return fonts


@pytest.fixture(scope="module")
def screen_artifacts():
    base = Path(os.environ.get("FM_ARTIFACTS") or ROOT / "artifacts" / f"sc-screens-{datetime.now():%Y%m%d-%H%M%S}")
    path = base.resolve() / "screens"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="module")
def blank_tools(screen_tools, tmp_path_factory):
    tools = tmp_path_factory.mktemp("blank-tools") / "tools"
    shutil.copytree(screen_tools, tools)
    poses, sizes = 10, 5
    table_end = 8 + 8 * poses * sizes
    entries = b"".join(struct.pack("<HHI", 1, 1, table_end) for _ in range(poses * sizes))
    (tools / "character.bin").write_bytes(b"FMA1" + struct.pack("<BBH", poses, sizes, 0) + entries + bytes(3))
    return tools


def render(tools, artifacts, name, mode="rows", rows=(), total=None, tick=0):
    result = render_screen(tools, artifacts, name, mode, rows, total, tick)
    return result["rgb"], result["regions"]


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
def test_sessions_fill_their_slots(case, count, blank_tools, screen_artifacts):
    image, regions = render(blank_tools, screen_artifacts, f"board-{count}", rows=SIX_ROWS[:count])
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


@pytest.mark.parametrize("case, theme", [
    ("[SC-17] in the comic theme a sixth and seventh session only add +N to the header and leave the "
     "character's cell alone", 2),
    ("[SC-17] in the tower theme a sixth and seventh session only add +N to the header and leave the "
     "character's cell alone", 3),
])
def test_character_keeps_its_cell(case, theme, screen_tools, screen_artifacts):
    five = render_screen(screen_tools, screen_artifacts, f"theme{theme}-5", rows=SIX_ROWS[:5], tick=70, theme=theme)
    six = render_screen(screen_tools, screen_artifacts, f"theme{theme}-6", rows=SIX_ROWS, tick=70, theme=theme)
    seven = render_screen(screen_tools, screen_artifacts, f"theme{theme}-7", rows=SIX_ROWS, total=7, tick=70,
                          theme=theme)
    for more in (six, seven):
        changed = changed_pixels(five["rgb"], more["rgb"])
        assert changed, "the header shows no +N"
        assert all(y < 24 for _, y in changed), changed[:10]
    assert changed_pixels(six["rgb"], seven["rgb"])


@pytest.mark.parametrize("case", ["[SC-18] for every theme and 0 to 6 sessions the character takes the tallest of "
                                  "74, 112, 145 and 200 px that fits the free space, as in the owner's pictures, "
                                  "and neither she nor her question mark or bubble touches a session slot"])
def test_character_grows_on_host(case, screen_tools):
    result = subprocess.run([str(screen_tools / "test_screen"), str(screen_tools), "grow"], capture_output=True,
                            text=True)
    print(result.stdout)
    assert result.returncode == 0, result.stdout
    assert "ALL PASSED" in result.stdout


def inside(points, box):
    x, y, w, h = box
    return all(x <= px < x + w and y <= py < y + h for px, py in points)


@pytest.mark.parametrize("case, theme, count, height", [
    ("[SC-18] on the board two sessions leave room for her 200 px picture, drawn only below the sessions", 0, 2, 200),
    ("[SC-18] on the arcade four sessions give her the 145 px picture, drawn only below the sessions", 1, 4, 145),
    ("[SC-18] in the comic theme two sessions give her the 145 px picture inside the merged home", 2, 2, 145),
    ("[SC-18] in the tower theme five sessions keep her at the default 74 px in the sixth cell", 3, 5, 74),
    ("[SC-18] on the board with no session she grows to 145 px below the hint text", 0, 0, 145),
])
def test_character_drawn_at_its_size(case, theme, count, height, screen_tools, blank_tools, screen_artifacts):
    mode = "rows" if count else "empty"
    rows = SIX_ROWS[:count]
    full = render_screen(screen_tools, screen_artifacts, f"grow-{theme}-{count}", mode, rows, tick=51, theme=theme)
    bare = render_screen(blank_tools, screen_artifacts, f"grow-{theme}-{count}-bare", mode, rows, tick=51,
                         theme=theme)
    x, y, w, h = full["character"]
    assert h == height
    changed = changed_pixels(full["rgb"], bare["rgb"])
    assert changed and inside(changed, (x, y, w, h)), (full["character"], changed[:10])
    drawn = max(py for _, py in changed) - min(py for _, py in changed) + 1
    assert drawn > 74 if height > 74 else drawn <= 74, drawn
    if theme < 2:
        slots = [(0, 24 + 36 * i, W, 36) for i in range(count)]
    else:
        slots = [(3 + i % 2 * 120, 24 + i // 2 * 86, 114, 80) for i in range(count)]
    assert not any(inside([point], slot) for point in changed for slot in slots), slots


@pytest.mark.parametrize("case, theme", [
    ("[SC-18] in the comic theme with two sessions the four free cells are drawn as one home with no gaps", 2),
    ("[SC-18] in the tower theme with two sessions the four free cells are drawn as one home with no gaps", 3),
])
def test_free_cells_merge_into_one_home(case, theme, blank_tools, screen_artifacts):
    two = render_screen(blank_tools, screen_artifacts, f"home-{theme}-2", rows=SIX_ROWS[:2], theme=theme)["rgb"]
    five = render_screen(blank_tools, screen_artifacts, f"home-{theme}-5", rows=SIX_ROWS[:5], theme=theme)["rgb"]
    home = pixel(two, 180, 240)
    assert pixel(five, 180, 240) == home
    for x, y in ((60, 150), (120, 150), (60, 193), (120, 193), (180, 150)):
        assert pixel(two, x, y) == home, (x, y)
    assert pixel(five, 120, 240) != home


BADGE_ORANGE = bytes((255, 89, 0))


def slot_of(theme, index):
    if theme < 2:
        return (0, 24 + 36 * index, W, 36)
    return (3 + index % 2 * 120, 24 + index // 2 * 86, 114, 80)


def count_colour(image, area, colour):
    x0, y0, w, h = area
    return sum(pixel(image, x, y) == colour for y in range(y0, y0 + h) for x in range(x0, x0 + w))


@pytest.mark.parametrize("case, theme", [
    (f"[SC-22] in the {name} theme a project thread gets the orange P sticker before its name inside its own slot, "
     f"and a plain local thread has none", theme)
    for theme, name in enumerate(("board", "arcade", "comic", "tower"))
])
def test_project_badge_on_host(case, theme, blank_tools, screen_artifacts):
    plain_rows = SIX_ROWS[:3]
    project_rows = [plain_rows[0], (*plain_rows[1], True), plain_rows[2]]
    plain = render_screen(blank_tools, screen_artifacts, f"badge-{theme}-plain", rows=plain_rows, theme=theme)["rgb"]
    marked = render_screen(blank_tools, screen_artifacts, f"badge-{theme}-project", rows=project_rows,
                           theme=theme)["rgb"]
    slot = slot_of(theme, 1)
    changed = changed_pixels(plain, marked)
    assert changed and inside(changed, slot), (slot, changed[:10])
    gained = count_colour(marked, slot, BADGE_ORANGE) - count_colour(plain, slot, BADGE_ORANGE)
    assert gained >= 100, gained
    badge_x = min(x for x, y in changed if pixel(marked, x, y) == BADGE_ORANGE)
    assert badge_x < slot[0] + 30, badge_x


@pytest.mark.parametrize("case", ["[SC-5] fresh rows start one alert per table, an error wins over waiting, a new "
                                  "alert restarts the old one, running to done cheers, a link waves hello, and an "
                                  "alert outranks a cheer, which outranks hello"])
def test_events_on_host(case, tmp_path):
    binary = tmp_path / "test_events"
    build(HOST / "test_events.c", binary, [FIRMWARE_MAIN / "events.c"])
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    print(result.stdout)
    assert result.returncode == 0, result.stdout
    assert "ALL PASSED" in result.stdout


THEMES = ("board", "arcade", "comic", "tower")


@pytest.mark.parametrize("case, theme", [
    (f"[SC-5] in the {name} theme the waiting and the error alert cover the table, differ from each other, and "
     f"the character rises inside the alert area and then holds still", theme)
    for theme, name in enumerate(THEMES)
])
def test_alert_on_host(case, theme, screen_tools, screen_artifacts):
    def shot(label, event):
        return render_screen(screen_tools, screen_artifacts, f"alert-{THEMES[theme]}-{label}", rows=SIX_ROWS[:3],
                             theme=theme, tick=40, daily=0, event=event)
    calm = render_screen(screen_tools, screen_artifacts, f"alert-{THEMES[theme]}-calm", rows=SIX_ROWS[:3],
                         theme=theme, tick=40, daily=0)
    rising = shot("rising", ("wait", 150, "FunMedal"))
    risen = shot("wait", ("wait", 900, "FunMedal"))
    later = shot("wait-later", ("wait", 5900, "FunMedal"))
    error = shot("error", ("error", 900, "SiBlog"))
    covered = changed_pixels(calm["rgb"], risen["rgb"])
    assert len(covered) > W * H // 4, len(covered)
    assert changed_pixels(risen["rgb"], error["rgb"])
    moved = changed_pixels(rising["rgb"], risen["rgb"])
    assert moved and inside(moved, risen["alert"]), (risen["alert"], moved[:10])
    assert risen["rgb"] == later["rgb"]


@pytest.mark.parametrize("case, theme", [
    (f"[SC-5] in the {name} theme a cheer shows the finished session's name in its strip and the character "
     f"cheers, and the hello screen replaces the table", theme)
    for theme, name in enumerate(THEMES)
])
def test_cheer_and_hello_on_host(case, theme, screen_tools, screen_artifacts):
    rows = SIX_ROWS[:3]
    calm = render_screen(screen_tools, screen_artifacts, f"cheer-{THEMES[theme]}-calm", rows=rows, theme=theme,
                         tick=40, daily=0)
    cheer = render_screen(screen_tools, screen_artifacts, f"cheer-{THEMES[theme]}", rows=rows, theme=theme,
                          tick=40, daily=0, event=("cheer", 300, "agent-skills"))
    other = render_screen(screen_tools, screen_artifacts, f"cheer-{THEMES[theme]}-other", rows=rows, theme=theme,
                          tick=40, daily=0, event=("cheer", 300, "SiBlog"))
    hello = render_screen(screen_tools, screen_artifacts, f"hello-{THEMES[theme]}", rows=rows, theme=theme,
                          tick=40, daily=0, event=("hello", 300, ""))
    strip = cheer["cheer"]
    named = changed_pixels(cheer["rgb"], other["rgb"])
    assert named and inside(named, strip), (strip, named[:10])
    changed = changed_pixels(calm["rgb"], cheer["rgb"])
    assert any(inside([p], cheer["character"]) and not inside([p], strip) for p in changed), "the character does not cheer"
    slots = [slot_of(theme, i) for i in range(len(rows))]
    assert not any(inside([p], slot) for p in changed for slot in slots), "the cheer covers a session row"
    waved = changed_pixels(calm["rgb"], hello["rgb"])
    assert all(any(inside([p], slot) for p in waved) for slot in slots), "the hello screen keeps a session row"
