import argparse
import hashlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FONT = ROOT / "third_party" / "noto-sans-sc" / "NotoSansSC-Regular.otf"
FONT_URL = "https://github.com/notofonts/noto-cjk/raw/main/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf"
FONT_SHA256 = "faa6c9df652116dde789d351359f3d7e5d2285a2b2a1f04a2d7244df706d5ea9"
SCENE = ROOT / "firmware" / "main" / "scene.c"
OUT = ROOT / "firmware" / "main"
C_STRING = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
LARGE_PIXELS = 30
TEXT_PIXELS = 16


def screen_characters() -> str:
    texts = C_STRING.findall(SCENE.read_text(encoding="utf-8"))
    return "".join(sorted({c for text in texts for c in text if ord(c) > 0x7E}))


def main() -> int:
    parser = argparse.ArgumentParser(description="regenerate the card fonts from Noto Sans SC")
    parser.add_argument("--font", type=Path, default=FONT)
    args = parser.parse_args()
    if not args.font.exists():
        print(f"font file missing: download {FONT_URL} to {args.font}", file=sys.stderr)
        return 1
    digest = hashlib.sha256(args.font.read_bytes()).hexdigest()
    if digest != FONT_SHA256:
        print(f"{args.font} has SHA-256 {digest}, expected {FONT_SHA256}", file=sys.stderr)
        return 1
    wide = screen_characters()
    printable = "".join(chr(c) for c in range(0x20, 0x7F))
    with tempfile.TemporaryDirectory() as tmp:
        tool = Path(tmp) / "gen_font"
        subprocess.run(["swiftc", "-O", str(ROOT / "tools" / "gen_font.swift"), "-o", str(tool)], check=True)
        for name, characters, pixels in [("large", wide + "0123456789/", LARGE_PIXELS),
                                         ("text", printable + "…" + wide, TEXT_PIXELS)]:
            subprocess.run([str(tool), str(args.font), name, characters, str(pixels), str(OUT / f"font_{name}.c")],
                           check=True)
    print(f"wrote font_large.c and font_text.c with {len(wide)} Chinese characters: {wide}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
