import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import cardlink  # noqa: E402

FIRMWARE_MAIN = ROOT / "firmware" / "main"
HOST = ROOT / "tests" / "host"
GEN_FONTS = ROOT / "tools" / "gen_fonts.py"
SCREEN_SOURCES = [FIRMWARE_MAIN / f"{name}.c" for name in (
    "scene", "font", "link_version", "draw", "dirty", "screen", "theme_board", "rows")] + [HOST / "host_fonts.c"]
W, H = 240, 320


def build(source, binary, sources):
    subprocess.run(
        ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", f"-I{FIRMWARE_MAIN}",
         str(source), *map(str, sources), "-o", str(binary)],
        check=True,
    )


def generate_fonts(out):
    done = subprocess.run([sys.executable, str(GEN_FONTS), "--out", str(out)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    return out


@pytest.fixture(scope="module")
def screen_tools(tmp_path_factory):
    tools = generate_fonts(tmp_path_factory.mktemp("screen-tools"))
    build(HOST / "render_png.c", tools / "render_png", SCREEN_SOURCES)
    return tools


def render(tools, out_dir, name, mode="rows", rows=(), total=None, tick=0):
    lines = ["theme 0", f"tick {tick}", f"mode {mode}", f"total {len(rows) if total is None else total}"]
    lines += [f"row {state} {minutes} {title}" for state, minutes, title in rows]
    scene = tools / f"{name}.txt"
    scene.write_text("\n".join(lines) + "\n", encoding="utf-8")
    png, rgb, wire = out_dir / f"{name}.png", tools / f"{name}.rgb", tools / f"{name}.wire"
    done = subprocess.run([str(tools / "render_png"), str(tools), str(scene), str(png), str(rgb), str(wire)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    regions = [tuple(int(v) for v in line.split()[1:3]) for line in done.stdout.splitlines()
               if line.startswith("REGION ")]
    return {"rgb": rgb.read_bytes(), "wire": wire.read_bytes(), "regions": regions}


def scene_of(on_card):
    reply = on_card["reply"]
    if reply["linked"] == "0":
        return "unlinked", [], 0
    rows = [(r["state"], "-" if r["minutes"] is None else str(r["minutes"]), r["name"]) for r in on_card["rows"]]
    return ("empty" if reply["total"] == "0" else "rows"), rows, int(reply["total"])


def compare_screen(card, tools, out_dir, name, mode, rows=(), total=0):
    meta, width, height, data = card.snapshot()
    assert (width, height) == (W, H)
    cardlink.write_png(out_dir / f"{name}-card.png", width, height, cardlink.rgb565_wire_to_rgb(data))
    host = render(tools, out_dir, f"{name}-host", mode, rows, total, int(meta["tick"]))
    differing = sum(1 for i in range(0, len(data), 2) if data[i:i + 2] != host["wire"][i:i + 2])
    return {"meta": meta, "mode": mode, "rows": list(rows), "total": total, "differing_pixels": differing}
