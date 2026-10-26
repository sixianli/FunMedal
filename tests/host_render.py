import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import cardlink  # noqa: E402

FIRMWARE_MAIN = ROOT / "firmware" / "main"
HOST = ROOT / "tests" / "host"
GEN_FONTS = ROOT / "tools" / "gen_fonts.py"
GEN_ASSETS = ROOT / "tools" / "gen_assets.py"
DEFAULT_PACK = ROOT / "assets" / "default"
SCREEN_SOURCES = [FIRMWARE_MAIN / f"{name}.c" for name in (
    "scene", "font", "link_version", "draw", "dirty", "screen",
    "theme_board", "theme_arcade", "theme_comic", "theme_tower", "rows", "character", "character_art", "events")] + [
    HOST / "host_fonts.c", HOST / "host_character.c"]
W, H = 240, 320
QUIET_WITHIN_S = 10.0
QUIET_POLL_S = 0.5


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


def generate_assets(out, pack=DEFAULT_PACK):
    done = subprocess.run([sys.executable, str(GEN_ASSETS), "--pack", str(pack), "--out", str(out)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    return out


@pytest.fixture(scope="module")
def screen_tools(tmp_path_factory):
    tools = generate_assets(generate_fonts(tmp_path_factory.mktemp("screen-tools")))
    build(HOST / "render_png.c", tools / "render_png", SCREEN_SOURCES)
    return tools


def row_count(row):
    return row[4] if len(row) > 4 else 1


def render(tools, out_dir, name, mode="rows", rows=(), total=None, tick=0, theme=0, daily=None, event=None,
           urgent=None):
    if urgent is None:
        urgent = sum(row_count(r) for r in rows if r[0] in ("waiting", "error"))
    lines = [f"theme {theme}", f"tick {tick}", f"mode {mode}",
             f"total {sum(row_count(r) for r in rows) if total is None else total}", f"urgent {urgent}"]
    if daily is not None:
        lines.append(f"daily {daily}")
    if event is not None:
        kind, ms, title = event
        lines.append(f"event {kind} {ms} {title}")
    lines += [f"row {r[0]} {r[1]} {'p' if len(r) > 3 and r[3] else '-'} {row_count(r)} {r[2]}" for r in rows]
    scene = tools / f"{name}.txt"
    scene.write_text("\n".join(lines) + "\n", encoding="utf-8")
    png, rgb, wire = out_dir / f"{name}.png", tools / f"{name}.rgb", tools / f"{name}.wire"
    done = subprocess.run([str(tools / "render_png"), str(tools), str(scene), str(png), str(rgb), str(wire)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    regions = [tuple(int(v) for v in line.split()[1:3]) for line in done.stdout.splitlines()
               if line.startswith("REGION ")]
    assets = [line.split()[1] for line in done.stdout.splitlines() if line.startswith("ASSETS ")]
    character = [tuple(int(v) for v in line.split()[1:5]) for line in done.stdout.splitlines()
                 if line.startswith("CHARACTER ")]
    boxes = {line.split()[0]: tuple(int(v) for v in line.split()[1:5]) for line in done.stdout.splitlines()
             if line.startswith(("ALERT ", "CHEER "))}
    return {"rgb": rgb.read_bytes(), "wire": wire.read_bytes(), "regions": regions, "assets": assets[0],
            "character": character[0] if character else None, "alert": boxes["ALERT"], "cheer": boxes["CHEER"]}


def scene_of(on_card):
    reply = on_card["reply"]
    if reply["linked"] == "0":
        return "unlinked", [], 0
    rows = [(r["state"], "-" if r["minutes"] is None else str(r["minutes"]), r["name"], r["project_thread"], r["count"])
            for r in on_card["rows"]]
    return ("empty" if reply["total"] == "0" else "rows"), rows, int(reply["total"]), int(reply["urgent"])


def wait_quiet(card):
    deadline = time.monotonic() + QUIET_WITHIN_S
    while card.snapshot()[0]["event"] != "none":
        assert time.monotonic() < deadline, "an event is still showing"
        time.sleep(QUIET_POLL_S)


def compare_screen(card, tools, out_dir, name, mode, rows=(), total=0, urgent=0, quiet=False):
    meta, width, height, data = card.snapshot()
    assert not quiet or meta["event"] == "none", meta
    assert (width, height) == (W, H)
    cardlink.write_png(out_dir / f"{name}-card.png", width, height, cardlink.rgb565_wire_to_rgb(data))
    event = None
    if meta["event"] != "none":
        event = (meta["event"], int(meta["event_ms"]), bytes.fromhex(meta["event_name"]).decode("utf-8"))
    host = render(tools, out_dir, f"{name}-host", mode, rows, total, int(meta["tick"]), int(meta["theme"]),
                  int(meta["daily"]), event, urgent)
    assert meta["assets"] == host["assets"], (meta["assets"], host["assets"])
    differing = sum(1 for i in range(0, len(data), 2) if data[i:i + 2] != host["wire"][i:i + 2])
    return {"meta": meta, "theme": int(meta["theme"]), "assets": meta["assets"], "mode": mode, "rows": list(rows), "total": total, "differing_pixels": differing,
            "character": host["character"]}
