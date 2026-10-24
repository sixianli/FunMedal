import re
import subprocess
import sys
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
