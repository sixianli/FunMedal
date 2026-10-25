import struct
import zlib
from pathlib import Path

SIGNATURE = b"\x89PNG\r\n\x1a\n"
RGB, PALETTE, GRAY_ALPHA, RGBA = 2, 3, 4, 6
CHANNELS = {0: 1, RGB: 3, PALETTE: 1, GRAY_ALPHA: 2, RGBA: 4}


class PngError(Exception):
    pass


def chunks(data: bytes):
    pos = len(SIGNATURE)
    while pos + 8 <= len(data):
        length, kind = struct.unpack_from(">I4s", data, pos)
        yield kind, data[pos + 8:pos + 8 + length]
        pos += 12 + length


def paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def unfilter(raw: bytes, width: int, height: int, step: int) -> bytearray:
    stride = width * step
    out = bytearray(stride * height)
    prior = bytearray(stride)
    pos = 0
    for y in range(height):
        kind = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        if kind == 1:
            for i in range(step, stride):
                line[i] = (line[i] + line[i - step]) & 0xFF
        elif kind == 2:
            for i in range(stride):
                line[i] = (line[i] + prior[i]) & 0xFF
        elif kind == 3:
            for i in range(stride):
                left = line[i - step] if i >= step else 0
                line[i] = (line[i] + ((left + prior[i]) >> 1)) & 0xFF
        elif kind == 4:
            for i in range(stride):
                left = line[i - step] if i >= step else 0
                upper_left = prior[i - step] if i >= step else 0
                line[i] = (line[i] + paeth(left, prior[i], upper_left)) & 0xFF
        elif kind != 0:
            raise PngError(f"未知的行过滤方式 {kind}")
        out[y * stride:(y + 1) * stride] = line
        prior = line
    return out


def read_rgba(path) -> tuple[int, int, bytes]:
    data = Path(path).read_bytes()
    if not data.startswith(SIGNATURE):
        raise PngError("不是 PNG 文件")
    header = palette = alpha_table = None
    compressed = bytearray()
    for kind, body in chunks(data):
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = body
        elif kind == b"tRNS":
            alpha_table = body
        elif kind == b"IDAT":
            compressed += body
    if header is None:
        raise PngError("缺少 IHDR")
    width, height, depth, color, _, _, interlace = header
    if depth != 8 or interlace != 0 or color not in CHANNELS:
        raise PngError(f"只支持 8 位、不隔行的 PNG（这张是 {depth} 位、颜色类型 {color}、隔行 {interlace}）")
    if color in (0, RGB) or (color == PALETTE and alpha_table is None):
        raise PngError("没有透明通道")
    pixels = unfilter(zlib.decompress(bytes(compressed)), width, height, CHANNELS[color])
    if color == RGBA:
        return width, height, bytes(pixels)
    out = bytearray(width * height * 4)
    for i in range(width * height):
        if color == GRAY_ALPHA:
            gray, alpha = pixels[2 * i], pixels[2 * i + 1]
            out[4 * i:4 * i + 4] = bytes((gray, gray, gray, alpha))
        else:
            index = pixels[i]
            alpha = alpha_table[index] if index < len(alpha_table) else 255
            out[4 * i:4 * i + 4] = palette[3 * index:3 * index + 3] + bytes((alpha,))
    return width, height, bytes(out)


def write(path, width: int, height: int, pixels: bytes, color: int) -> None:
    stride = width * CHANNELS[color]
    raw = b"".join(b"\x00" + pixels[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    png = SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    Path(path).write_bytes(png)


def write_rgba(path, width: int, height: int, pixels: bytes) -> None:
    write(path, width, height, pixels, RGBA)


def write_rgb(path, width: int, height: int, pixels: bytes) -> None:
    write(path, width, height, pixels, RGB)
