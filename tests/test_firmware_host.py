import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE_MAIN = ROOT / "firmware" / "main"
KEY_SOURCES = [FIRMWARE_MAIN / "keys.c", FIRMWARE_MAIN / "key_levels.c"]
KEY_CAPTURE = ROOT / "notes" / "data" / "key-adc-2026-10-16.txt"
SCENE_SOURCES = [FIRMWARE_MAIN / "scene.c", FIRMWARE_MAIN / "font.c", FIRMWARE_MAIN / "font_data.c",
                 FIRMWARE_MAIN / "pet_art.c"]


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
