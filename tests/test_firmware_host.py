import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE_MAIN = ROOT / "firmware" / "main"
KEY_SOURCES = [FIRMWARE_MAIN / "keys.c", FIRMWARE_MAIN / "key_levels.c"]
KEY_CAPTURE = ROOT / "notes" / "data" / "key-adc-2026-10-16.txt"
FONT_SOURCES = [FIRMWARE_MAIN / "font.c", FIRMWARE_MAIN / "font_large.c", FIRMWARE_MAIN / "font_text.c"]
PROMPT_SOURCES = [FIRMWARE_MAIN / "prompt.c", *FONT_SOURCES]
SCENE_SOURCES = [FIRMWARE_MAIN / "scene.c", FIRMWARE_MAIN / "pet_art.c", *PROMPT_SOURCES]
C_STRING = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
GLYPH_ENTRY = re.compile(r"\{0x([0-9a-f]+), \d+, \d+\}")


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


@pytest.mark.parametrize("case", ["[FM-13] bluetooth state value must be exactly one state name"])
def test_state_value_parsing_on_host(case, tmp_path):
    binary = tmp_path / "test_state_names"
    build(ROOT / "tests" / "host" / "test_state_names.c", binary, SCENE_SOURCES)
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    print(result.stdout)
    assert result.returncode == 0, result.stdout
    assert "ALL PASSED" in result.stdout


@pytest.mark.parametrize("case", ["[FM-14] prompt value parsing and layout on host"])
def test_prompt_parsing_and_layout_on_host(case, tmp_path):
    binary = tmp_path / "test_prompt"
    build(ROOT / "tests" / "host" / "test_prompt.c", binary, PROMPT_SOURCES)
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    print(result.stdout)
    assert result.returncode == 0, result.stdout
    assert "ALL PASSED" in result.stdout


def glyphs_in(path):
    return {chr(int(code, 16)) for code in GLYPH_ENTRY.findall(path.read_text(encoding="utf-8"))}


@pytest.mark.parametrize("case", ["[FM-14] both fonts hold every character the screens draw"])
def test_fonts_cover_screen_text(case):
    texts = C_STRING.findall((FIRMWARE_MAIN / "scene.c").read_text(encoding="utf-8"))
    wide = {c for text in texts for c in text if ord(c) > 0x7e}
    large = glyphs_in(FIRMWARE_MAIN / "font_large.c")
    small = glyphs_in(FIRMWARE_MAIN / "font_text.c")
    printable = {chr(c) for c in range(0x20, 0x7f)}
    assert len(wide) >= 20, sorted(wide)
    assert wide - large == set(), sorted(wide - large)
    assert wide - small == set(), sorted(wide - small)
    assert set("0123456789/") - large == set()
    missing_text = (printable | {"\u2026"}) - small
    assert missing_text == set(), sorted(missing_text)
