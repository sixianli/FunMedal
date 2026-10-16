import sys
from pathlib import Path

SIZE = 32
PALETTE = {
    ".": None,
    "K": 0x1A1C2C,
    "B": 0xFFE9C5,
    "W": 0xFFFFFF,
    "P": 0xFF8FAB,
    "G": 0x94A3B8,
    "D": 0x475569,
    "C": 0x73EFF7,
    "O": 0xFFB000,
    "R": 0xEF3E36,
    "Y": 0xFFD93D,
    "L": 0x41A6F6,
    "S": 0xF2C58F,
}
CODES = list(PALETTE)
STATES = ["idle", "working", "waiting", "done", "error"]


class Canvas:
    def __init__(self):
        self.grid = [["."] * SIZE for _ in range(SIZE)]

    def put(self, x, y, c):
        if 0 <= x < SIZE and 0 <= y < SIZE:
            self.grid[y][x] = c

    def rect(self, x, y, w, h, c):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.put(xx, yy, c)

    def stamp(self, x, y, rows):
        for dy, row in enumerate(rows):
            for dx, c in enumerate(row):
                if c != " ":
                    self.put(x + dx, y + dy, c)

    def body(self, dy):
        cx, cy, rx, ry = 15.5, 19.5 + dy, 11.0, 9.5

        def inside(x, y):
            return ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1.0

        for y in range(SIZE):
            for x in range(SIZE):
                if not inside(x, y):
                    continue
                edge = any(not inside(x + ox, y + oy) for ox, oy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
                if edge:
                    self.put(x, y, "K")
                elif y >= cy + 5:
                    self.put(x, y, "S")
                else:
                    self.put(x, y, "B")
        feet_y = int(cy + ry)
        self.rect(10, feet_y, 3, 2, "K")
        self.rect(19, feet_y, 3, 2, "K")

    def cheeks(self, dy):
        self.rect(8, 21 + dy, 2, 1, "P")
        self.rect(22, 21 + dy, 2, 1, "P")

    def eyes(self, kind, dy):
        y = 17 + dy
        for x in (10, 19):
            if kind == "open":
                self.rect(x, y, 2, 3, "K")
                self.put(x, y, "W")
            elif kind == "down":
                self.rect(x, y + 1, 2, 3, "K")
                self.put(x + 1, y + 3, "W")
            elif kind == "closed":
                self.stamp(x - 1, y + 1, ["K  K", " KK "])
            elif kind == "happy":
                self.stamp(x - 1, y, [" KK ", "K  K"])
            elif kind == "x":
                self.stamp(x - 1, y, ["K K", " K ", "K K"])

    def mouth(self, kind, dy):
        y = 23 + dy
        if kind == "small":
            self.rect(15, y, 2, 1, "K")
        elif kind == "smile":
            self.stamp(13, y - 1, ["K    K", " KKKK "])
        elif kind == "o":
            self.stamp(15, y - 1, ["KK", "KK"])
        elif kind == "frown":
            self.stamp(13, y, [" KKKK ", "K    K"])
        elif kind == "sleep":
            self.put(16, y, "K")


def idle(frame):
    c = Canvas()
    dy = frame
    c.body(dy)
    c.cheeks(dy)
    c.eyes("closed", dy)
    c.mouth("sleep", dy)
    c.stamp(23, 6, ["WWWW", "  W ", " W  ", "WWWW"])
    if frame == 1:
        c.stamp(26, 0, ["WWWWW", "   W ", "  W  ", " W   ", "WWWWW"])
    return c


def working(frame):
    c = Canvas()
    c.body(0)
    c.cheeks(0)
    c.eyes("down", 0)
    c.mouth("small", 0)
    left_y, right_y = (23, 24) if frame == 0 else (24, 23)
    c.rect(4, 26, 24, 5, "D")
    c.rect(5, 27, 22, 3, "G")
    for x in range(6, 26, 3):
        c.put(x, 28, "D")
    c.stamp(8, left_y, ["KKKK", "KBBK", "KKKK"])
    c.stamp(20, right_y, ["KKKK", "KBBK", "KKKK"])
    if frame == 0:
        c.stamp(27, 20, ["C", " ", "C"])
    else:
        c.stamp(28, 19, ["C", " ", "C", " ", "C"])
    return c


def waiting(frame):
    c = Canvas()
    c.body(0)
    c.cheeks(0)
    c.eyes("open", 0)
    c.mouth("o", 0)
    arm_x = 25 if frame == 0 else 26
    c.stamp(arm_x, 6, [" KKK ", "KBBBK", "KBBBK", " KBK ", " KBK ", " KBK ", " KBK ", " KBK "])
    if frame == 0:
        c.rect(14, 1, 3, 5, "K")
        c.rect(14, 7, 3, 2, "K")
    return c


def done(frame):
    c = Canvas()
    dy = 0 if frame == 0 else -2
    c.body(dy)
    c.cheeks(dy)
    c.eyes("happy", dy)
    c.mouth("smile", dy)
    sparkle = [" Y ", "YWY", " Y "]
    spots = [(2, 6), (27, 4), (26, 13)] if frame == 0 else [(4, 2), (25, 8), (1, 13)]
    for x, y in spots:
        c.stamp(x, y, sparkle)
    return c


def error(frame):
    c = Canvas()
    c.body(0)
    c.eyes("x", 0)
    c.mouth("frown", 0)
    c.stamp(24, 9 + frame * 2, [" L ", "LLL", "LWL", " L "])
    return c


BUILDERS = {"idle": idle, "working": working, "waiting": waiting, "done": done, "error": error}


def main():
    out = Path(sys.argv[1])
    lines = ['#include "pet_art.h"', "", f"const uint32_t pet_palette[PET_PALETTE_SIZE] = {{"]
    for code in CODES:
        value = PALETTE[code]
        lines.append(f"    0x{0 if value is None else value:06x},")
    lines += ["};", "", "const uint8_t pet_sprites[PET_STATE_COUNT][PET_FRAMES][PET_SIZE][PET_SIZE] = {"]
    for state in STATES:
        lines.append("    {")
        for frame in range(2):
            grid = BUILDERS[state](frame).grid
            lines.append("        {")
            for row in grid:
                lines.append("            {" + ", ".join(str(CODES.index(c)) for c in row) + "},")
            lines.append("        },")
        lines.append("    },")
    lines.append("};")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if len(sys.argv) > 2:
        preview = []
        for state in STATES:
            for frame in range(2):
                preview.append(f"{state} {frame}")
                preview += ["".join(row) for row in BUILDERS[state](frame).grid]
        Path(sys.argv[2]).write_text("\n".join(preview) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
