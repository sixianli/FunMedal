import argparse
import hashlib
import re
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "firmware" / "main"
SWIFT_TOOL = ROOT / "tools" / "gen_font.swift"
FETCH = "sh tools/fetch-fonts.sh"
NOTO = (ROOT / "third_party" / "noto-sans-sc" / "NotoSansSC-Regular.otf",
        "faa6c9df652116dde789d351359f3d7e5d2285a2b2a1f04a2d7244df706d5ea9")
FUSION = (ROOT / "third_party" / "fusion-pixel" / "fusion-pixel-12px-proportional-zh_hans.bdf",
          "9d69fe7a51e928dbb0689a70bed8eec28666713d8c57410fba0f0aa69c0005c6")
KUAILE = (ROOT / "third_party" / "zcool-kuaile" / "ZCOOLKuaiLe-Regular.ttf",
          "812a6fc1fe54b6d73a419245c32dfeba8aa33104d5be90d1cf6af082007cb71d")
C_STRING = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
MAGIC = b"FMF1"
MISSING = "□"
ELLIPSIS = "…"
TEXT_PIXELS = 16
LARGE_PIXELS = 30


class FontError(Exception):
    pass


def level_one() -> str:
    found = []
    for high in range(0xB0, 0xD8):
        for low in range(0xA1, 0xFF):
            try:
                found.append(bytes([high, low]).decode("gb2312"))
            except UnicodeDecodeError:
                pass
    return "".join(found)


def source_characters() -> str:
    texts = [text for path in sorted(SOURCES.glob("*.c"))
             for text in C_STRING.findall(path.read_text(encoding="utf-8"))]
    return "".join(sorted({c for text in texts for c in text if ord(c) > 0x7E}))


def checked(font: tuple[Path, str]) -> Path:
    path, expected = font
    if not path.exists():
        raise FontError(f"{path.relative_to(ROOT)} is missing; run {FETCH}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected:
        raise FontError(f"{path.relative_to(ROOT)} has SHA-256 {digest}, expected {expected}; run {FETCH}")
    return path


def swift_tool(out: Path) -> Path:
    tool = out / "gen_font"
    if not tool.exists() or tool.stat().st_mtime < SWIFT_TOOL.stat().st_mtime:
        subprocess.run(["swiftc", "-O", str(SWIFT_TOOL), "-o", str(tool)], check=True)
    return tool


def gray_glyphs(tool: Path, font: Path, characters: str, pixels: int, raw: Path):
    subprocess.run([str(tool), str(font), characters, str(pixels), str(raw)], check=True)
    data = raw.read_bytes()
    line_height = data[0]
    at = 1
    glyphs = []
    while at < len(data):
        codepoint, width = struct.unpack_from("<IH", data, at)
        at += 6
        gray = data[at:at + width * line_height]
        at += width * line_height
        rows = [[value >> 4 for value in gray[y * width:(y + 1) * width]] for y in range(line_height)]
        glyphs.append((codepoint, width, rows))
    return line_height, glyphs


def bdf_glyphs(font: Path, characters: str):
    wanted = {ord(c) for c in characters}
    ascent = descent = None
    glyphs = {}
    current = None
    for line in font.read_text(encoding="utf-8").splitlines():
        key, _, rest = line.partition(" ")
        if key == "FONT_ASCENT":
            ascent = int(rest)
        elif key == "FONT_DESCENT":
            descent = int(rest)
        elif key == "STARTCHAR":
            current = {"bitmap": None}
        elif current is None:
            continue
        elif key == "ENCODING":
            current["codepoint"] = int(rest.split()[0])
        elif key == "DWIDTH":
            current["advance"] = int(rest.split()[0])
        elif key == "BBX":
            current["box"] = tuple(int(part) for part in rest.split())
        elif key == "BITMAP":
            current["bitmap"] = []
        elif key == "ENDCHAR":
            if current.get("codepoint") in wanted:
                glyphs[current["codepoint"]] = current
            current = None
        elif current["bitmap"] is not None:
            current["bitmap"].append(int(line, 16) if line else 0)
    if ascent is None or descent is None:
        raise FontError(f"{font.name} has no FONT_ASCENT or FONT_DESCENT")
    missing = wanted - glyphs.keys()
    if missing:
        raise FontError(f"{font.name} lacks " + " ".join(f"U+{c:04X}" for c in sorted(missing)))
    line_height = ascent + descent
    result = []
    for codepoint in sorted(glyphs):
        glyph = glyphs[codepoint]
        width = max(1, glyph["advance"])
        box_w, box_h, x_off, y_off = glyph["box"]
        bits_per_row = (box_w + 7) // 8 * 8
        rows = [[0] * width for _ in range(line_height)]
        for row, bits in enumerate(glyph["bitmap"][:box_h]):
            y = ascent - (y_off + box_h) + row
            for col in range(box_w):
                x = x_off + col
                if bits >> (bits_per_row - 1 - col) & 1 and 0 <= x < width and 0 <= y < line_height:
                    rows[y][x] = 15
        result.append((codepoint, width, rows))
    return line_height, result


def pack(line_height: int, bits: int, glyphs) -> bytes:
    index = bytearray()
    bitmaps = bytearray()
    for codepoint, width, rows in sorted(glyphs):
        index += struct.pack("<IIHH", codepoint, len(bitmaps), width, 0)
        for row in rows:
            if bits == 4:
                padded = row + [0] * (len(row) % 2)
                bitmaps += bytes(padded[i] << 4 | padded[i + 1] for i in range(0, len(padded), 2))
            else:
                padded = row + [0] * (-len(row) % 8)
                bitmaps += bytes(sum((1 if padded[i + j] else 0) << (7 - j) for j in range(8))
                                 for i in range(0, len(padded), 8))
    return MAGIC + struct.pack("<BBHI", line_height, bits, 0, len(glyphs)) + bytes(index) + bytes(bitmaps)


def generate(out: Path) -> str:
    noto, fusion, kuaile = checked(NOTO), checked(FUSION), checked(KUAILE)
    out.mkdir(parents=True, exist_ok=True)
    wide = source_characters()
    ascii_printable = "".join(chr(c) for c in range(0x20, 0x7F))
    full = "".join(sorted(set(ascii_printable + level_one() + ELLIPSIS + MISSING + wide)))
    large = "".join(sorted(set("0123456789/" + wide)))
    tool = swift_tool(out)
    fonts = {
        "text": (4, *gray_glyphs(tool, noto, full, TEXT_PIXELS, out / "font_text.raw")),
        "large": (4, *gray_glyphs(tool, kuaile, large, LARGE_PIXELS, out / "font_large.raw")),
        "pixel": (1, *bdf_glyphs(fusion, full)),
    }
    sizes = []
    for name, (bits, line_height, glyphs) in fonts.items():
        data = pack(line_height, bits, glyphs)
        (out / f"font_{name}.bin").write_bytes(data)
        (out / f"font_{name}.raw").unlink(missing_ok=True)
        sizes.append(f"font_{name}.bin {len(glyphs)} glyphs {len(data)} bytes line height {line_height}")
    return "; ".join(sizes)


def main() -> int:
    parser = argparse.ArgumentParser(description="generate the card fonts as embedded binary files")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        print("gen_fonts.py: " + generate(args.out))
    except FontError as error:
        print(f"gen_fonts.py: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
