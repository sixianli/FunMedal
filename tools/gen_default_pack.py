import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pngio  # noqa: E402

WIDTH, HEIGHT = 200, 240
INK = (54, 64, 92)
SKIN = (255, 214, 170)
DESK = (150, 104, 64)
LIMB = 7.0
HEAD = 22.0
EYE = 2.6


def standing(arm_left, arm_right, head_dx=0.0, legs=12.0):
    neck, hip = (100, 104), (100, 168)
    head = (100 + head_dx, 76)
    lines = [(neck, hip), (hip, (100 - legs, 226)), (hip, (100 + legs, 226)),
             ((100, 116), arm_left), ((100, 116), arm_right)]
    return head, lines


def lying(head_y):
    head = (52, head_y)
    lines = [((74, 200), (170, 206)), ((170, 206), (196, 222)), ((170, 206), (190, 230)),
             ((84, 202), (64, 222)), ((64, 222), (40, 214))]
    return head, lines


def pose(name):
    desk = None
    eyes = "open"
    front = []
    if name == "01-lying":
        head, lines = lying(186)
    elif name == "02-lying-sleepy":
        head, lines = lying(198)
        eyes = "closed"
    elif name == "03-tap-up":
        head, lines = standing((70, 150), (150, 70))
        desk = ((120, 176), (196, 176))
    elif name == "04-tap-down":
        head, lines = standing((70, 150), (152, 168))
        desk = ((120, 176), (196, 176))
    elif name == "05-tilt":
        head, lines = standing((72, 156), (128, 156), head_dx=-12)
    elif name == "06-mumble":
        head, lines = standing((72, 156), (114, 92))
        front = [lines.pop()]
    elif name == "07-call":
        head, lines = standing((64, 40), (136, 40))
    elif name == "08-worry":
        head, lines = standing((84, 80), (116, 80))
        front = [lines.pop(), lines.pop()]
        eyes = "closed"
    elif name == "09-wave":
        head, lines = standing((72, 156), (150, 46))
    else:
        head, lines = standing((52, 52), (148, 52), legs=24)
    return head, lines, front, desk, eyes


def segment_distance(px, py, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def coverage(distance, radius):
    return max(0.0, min(1.0, radius + 0.5 - distance))


def draw(name):
    head, lines, front, desk, eyes = pose(name)
    pixels = bytearray(WIDTH * HEIGHT * 4)
    for y in range(HEIGHT):
        for x in range(WIDTH):
            px, py = x + 0.5, y + 0.5
            layers = []
            if desk:
                layers.append((DESK, coverage(segment_distance(px, py, *desk), 4.0)))
            body = max(coverage(segment_distance(px, py, a, b), LIMB / 2) for a, b in lines)
            layers.append((INK, body))
            face = coverage(math.hypot(px - head[0], py - head[1]), HEAD)
            layers.append((INK, coverage(math.hypot(px - head[0], py - head[1]), HEAD + 2.5)))
            layers.append((SKIN, face))
            for side in (-8, 8):
                eye = (head[0] + side, head[1] - 2)
                if eyes == "open":
                    layers.append((INK, coverage(math.hypot(px - eye[0], py - eye[1]), EYE)))
                else:
                    layers.append((INK, coverage(segment_distance(px, py, (eye[0] - 4, eye[1]), (eye[0] + 4, eye[1])), 1.2)))
            for a, b in front:
                layers.append((INK, coverage(segment_distance(px, py, a, b), LIMB / 2)))
            r = g = b = a = 0.0
            for (lr, lg, lb), la in layers:
                r, g, b = r * (1 - la) + lr * la, g * (1 - la) + lg * la, b * (1 - la) + lb * la
                a = a * (1 - la) + la
            if a > 0:
                r, g, b = r / a, g / a, b / a
            i = (y * WIDTH + x) * 4
            pixels[i:i + 4] = bytes((min(255, round(r)), min(255, round(g)), min(255, round(b)), round(a * 255)))
    return bytes(pixels)


def main() -> int:
    parser = argparse.ArgumentParser(description="画默认素材包的十张简笔人形")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "assets" / "default")
    args = parser.parse_args()
    folder = args.out / "character"
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("01-lying", "02-lying-sleepy", "03-tap-up", "04-tap-down", "05-tilt",
                 "06-mumble", "07-call", "08-worry", "09-wave", "10-cheer"):
        pngio.write_rgba(folder / f"{name}.png", WIDTH, HEIGHT, draw(name))
    print(f"gen_default_pack.py: 十张简笔人形写进 {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
