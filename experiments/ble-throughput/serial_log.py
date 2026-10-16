import sys
import time

import serial


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: serial_log.py <port> <seconds> <output>", file=sys.stderr)
        return 64
    port, seconds, output = sys.argv[1], float(sys.argv[2]), sys.argv[3]
    deadline = time.monotonic() + seconds
    with serial.Serial(port, 115200, timeout=0.5) as link, open(output, "w", encoding="utf-8") as out:
        buffer = b""
        while time.monotonic() < deadline:
            buffer += link.read(4096)
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                stamp = time.strftime("%H:%M:%S")
                out.write(f"{stamp} {line.decode('utf-8', 'replace').rstrip()}\n")
                out.flush()
        if buffer:
            out.write(buffer.decode("utf-8", "replace") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
