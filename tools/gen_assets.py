import argparse
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pngio  # noqa: E402

MAGIC = b"FMA1"
POSES = ["01-lying", "02-lying-sleepy", "03-tap-up", "04-tap-down", "05-tilt",
         "06-mumble", "07-call", "08-worry", "09-wave", "10-cheer"]
BOXES = [(64, 74), (84, 108)]
VISIBLE = 16
HEADER = 8
ENTRY = 8


class AssetError(Exception):
    pass


def load_pose(path: Path) -> tuple[int, int, bytes]:
    if not path.is_file():
        raise AssetError(f"{path}: 找不到这张图")
    try:
        width, height, rgba = pngio.read_rgba(path)
    except (pngio.PngError, ValueError, IndexError, struct.error, zlib.error) as error:
        raise AssetError(f"{path}: {error}") from error
    alphas = rgba[3::4]
    if min(alphas) == 255:
        raise AssetError(f"{path}: 透明通道里每个点都不透明，抠图没做")
    return width, height, rgba


def crop(width: int, height: int, rgba: bytes, path: Path) -> tuple[int, int, bytes]:
    rows = [y for y in range(height) if max(rgba[(y * width) * 4 + 3:((y + 1) * width) * 4:4]) >= VISIBLE]
    if not rows:
        raise AssetError(f"{path}: 整张图都是透明的")
    columns = [x for x in range(width) if max(rgba[(x + y * width) * 4 + 3] for y in rows) >= VISIBLE]
    x0, x1, y0, y1 = columns[0], columns[-1] + 1, rows[0], rows[-1] + 1
    out = bytearray()
    for y in range(y0, y1):
        out += rgba[(y * width + x0) * 4:(y * width + x1) * 4]
    return x1 - x0, y1 - y0, bytes(out)


def weights(source: int, target: int) -> list[list[tuple[int, float]]]:
    ratio = source / target
    table = []
    for t in range(target):
        start, end = t * ratio, (t + 1) * ratio
        taps = []
        s = int(start)
        while s < end and s < source:
            overlap = min(end, s + 1) - max(start, s)
            if overlap > 0:
                taps.append((s, overlap / ratio))
            s += 1
        table.append(taps)
    return table


def shrink(width: int, height: int, rgba: bytes, out_w: int, out_h: int) -> list[tuple[float, float, float, float]]:
    premultiplied = []
    for i in range(width * height):
        r, g, b, a = rgba[4 * i:4 * i + 4]
        premultiplied.append((r * a, g * a, b * a, a))
    across = weights(width, out_w)
    rows = []
    for y in range(height):
        line = premultiplied[y * width:(y + 1) * width]
        row = []
        for taps in across:
            r = g = b = a = 0.0
            for x, weight in taps:
                pr, pg, pb, pa = line[x]
                r += pr * weight
                g += pg * weight
                b += pb * weight
                a += pa * weight
            row.append((r, g, b, a))
        rows.append(row)
    down = weights(height, out_h)
    out = []
    for taps in down:
        for x in range(out_w):
            r = g = b = a = 0.0
            for y, weight in taps:
                pr, pg, pb, pa = rows[y][x]
                r += pr * weight
                g += pg * weight
                b += pb * weight
                a += pa * weight
            out.append((r, g, b, a))
    return out


def encode(pixels: list[tuple[float, float, float, float]]) -> bytes:
    colors = bytearray()
    alphas = []
    for r, g, b, a in pixels:
        level = round(a / 255 * 15)
        if level == 0:
            colors += b"\x00\x00"
            alphas.append(0)
            continue
        red, green, blue = (min(255.0, c / a) for c in (r, g, b))
        value = round(red * 31 / 255) << 11 | round(green * 63 / 255) << 5 | round(blue * 31 / 255)
        colors += struct.pack(">H", value)
        alphas.append(level)
    if len(alphas) % 2:
        alphas.append(0)
    packed = bytes(alphas[i] << 4 | alphas[i + 1] for i in range(0, len(alphas), 2))
    return bytes(colors) + packed


def build(pack: Path) -> bytes:
    poses = []
    for name in POSES:
        path = pack / "character" / f"{name}.png"
        width, height, rgba = load_pose(path)
        poses.append(crop(width, height, rgba, path))
    sprites = []
    for box_w, box_h in BOXES:
        scale = min(min(box_w / w, box_h / h) for w, h, _ in poses)
        for w, h, rgba in poses:
            out_w, out_h = max(1, min(box_w, round(w * scale))), max(1, min(box_h, round(h * scale)))
            sprites.append((out_w, out_h, encode(shrink(w, h, rgba, out_w, out_h))))
    order = [sprites[size * len(POSES) + pose] for pose in range(len(POSES)) for size in range(len(BOXES))]
    table = bytearray()
    data = bytearray()
    base = HEADER + ENTRY * len(order)
    for w, h, body in order:
        table += struct.pack("<HHI", w, h, base + len(data))
        data += body
    return MAGIC + struct.pack("<BBH", len(POSES), len(BOXES), 0) + bytes(table) + bytes(data)


def main() -> int:
    parser = argparse.ArgumentParser(description="把素材包里的角色图转成卡片固件嵌入的二进制文件")
    parser.add_argument("--pack", required=True, type=Path, help="素材包目录，里面有 character/01-lying.png 等十张图")
    parser.add_argument("--out", required=True, type=Path, help="输出目录，写 character.bin")
    args = parser.parse_args()
    try:
        blob = build(args.pack)
    except AssetError as error:
        print(f"gen_assets.py: {error}", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / "character.bin"
    partial = target.with_suffix(".part")
    partial.write_bytes(blob)
    partial.replace(target)
    print(f"gen_assets.py: {args.pack} → {target}（{len(blob)} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
