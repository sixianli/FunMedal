import argparse
import glob
import re
import sys
import time
import zlib
import struct

import serial


def find_port() -> str:
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not ports:
        raise RuntimeError("card USB serial port not found; press a key on the card to wake it")
    return ports[0]


class Card:
    def __init__(self, port: str | None = None):
        self.port = port or find_port()
        self.link = serial.Serial(self.port, 115200, timeout=0.1)
        self.pending = b""
        self.log: list[str] = []

    def close(self) -> None:
        self.link.close()

    def read_line(self, deadline: float) -> str | None:
        while b"\n" not in self.pending:
            if time.monotonic() > deadline:
                return None
            self.pending += self.link.read(4096)
        line, self.pending = self.pending.split(b"\n", 1)
        text = line.decode("utf-8", "replace").rstrip("\r")
        self.log.append(text)
        return text

    def wait_for(self, predicate, timeout: float = 5.0) -> str:
        deadline = time.monotonic() + timeout
        while True:
            line = self.read_line(deadline)
            if line is None:
                raise TimeoutError("card did not answer in time")
            if predicate(line):
                return line

    def send(self, command: str) -> None:
        self.link.write((command + "\n").encode())
        self.link.flush()

    def request(self, command: str, timeout: float = 5.0) -> str:
        verb = command.split()[0]
        self.send(command)
        return self.wait_for(lambda l: is_reply(l, verb), timeout)

    def command(self, command: str, timeout: float = 5.0) -> str:
        line = self.request(command, timeout)
        if line.startswith("ERR "):
            raise RuntimeError(f"{command!r} failed: {line}")
        return line

    def wait_ready(self, timeout: float = 10.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                return self.command("info", timeout=1.0)
            except TimeoutError:
                continue
        raise TimeoutError("card console not ready")

    def snapshot(self, timeout: float = 30.0) -> tuple[dict, int, int, bytes]:
        self.send("snap")
        return self.read_snapshot(timeout)

    def read_snapshot(self, timeout: float = 30.0) -> tuple[dict, int, int, bytes]:
        lines = [self.wait_for(lambda l: l.startswith("SNAP BEGIN"), timeout)]
        while not lines[-1].startswith("SNAP END"):
            lines.append(self.wait_for(lambda l: l.startswith("SNAP "), timeout))
        return parse_snapshot(lines)


SNAP_ROW = re.compile(r"SNAP \d+ [0-9a-f]{960}$")


def parse_snapshot(lines: list[str]) -> tuple[dict, int, int, bytes]:
    parts = lines[0].split()
    width, height = int(parts[2]), int(parts[3])
    meta = dict(p.split("=", 1) for p in parts[4:])
    rows: dict[int, bytes] = {}
    for line in lines[1:-1]:
        try:
            _, y, data = line.split(" ", 2)
            rows[int(y)] = bytes.fromhex(data)
        except ValueError as error:
            raise RuntimeError(f"corrupted snapshot line: {line[:24]}... ({len(line)} characters)") from error
    meta.update(p.split("=", 1) for p in lines[-1].split()[2:])
    if sorted(rows) != list(range(height)) or any(len(r) != width * 2 for r in rows.values()):
        raise RuntimeError("incomplete snapshot")
    return meta, width, height, b"".join(rows[y] for y in range(height))


def compact_log(lines: list[str]) -> list[str]:
    out, run = [], 0
    for line in lines:
        if SNAP_ROW.search(line):
            run += 1
            continue
        if run:
            out.append(f"# {run} SNAP rows omitted; pixels are in the PNG files")
            run = 0
        out.append(line)
    if run:
        out.append(f"# {run} SNAP rows omitted; pixels are in the PNG files")
    return out


def is_reply(line: str, verb: str) -> bool:
    words = line.split()
    if len(words) >= 2 and words[0] == "OK":
        return words[1] == verb
    if len(words) >= 2 and words[0] == "ERR":
        return words[1] == verb or words[1:4] == ["unknown", "command", verb]
    return False


def rgb565_wire_to_rgb(data: bytes) -> list[tuple[int, int, int]]:
    pixels = []
    for i in range(0, len(data), 2):
        value = (data[i] << 8) | data[i + 1]
        r = (value >> 11) & 0x1F
        g = (value >> 5) & 0x3F
        b = value & 0x1F
        pixels.append(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)))
    return pixels


def rgb888_to_565_rgb(rgb: int) -> tuple[int, int, int]:
    value = ((rgb >> 8) & 0xF800) | ((rgb >> 5) & 0x07E0) | ((rgb >> 3) & 0x001F)
    return rgb565_wire_to_rgb(bytes([value >> 8, value & 0xFF]))[0]


def write_png(path, width: int, height: int, pixels: list[tuple[int, int, int]]) -> None:
    raw = b"".join(b"\x00" + bytes(c for p in pixels[y * width:(y + 1) * width] for c in p) for y in range(height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", metavar="LAST_LINE",
                        help="after the commands, print every card line with a Mac timestamp until this line arrives")
    parser.add_argument("--timeout", type=float, default=150.0)
    parser.add_argument("commands", nargs="+")
    args = parser.parse_args()
    card = Card()
    try:
        card.wait_ready()
        for command in args.commands:
            print(card.command(command), file=sys.stderr if args.watch else sys.stdout)
        if args.watch:
            deadline = time.monotonic() + args.timeout
            while True:
                line = card.read_line(deadline)
                if line is None:
                    raise TimeoutError(f"{args.watch!r} did not arrive")
                print(f"{time.monotonic():.3f} {line}", flush=True)
                if line == args.watch:
                    break
    finally:
        card.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
