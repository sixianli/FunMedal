import glob
import threading
import time

import serial

from cardlink import is_reply, parse_snapshot


class CardWatcher:
    def __init__(self, port_glob: str = "/dev/cu.usbmodem*", mute: bool = False):
        self.port_glob = port_glob
        self.mute = mute
        self.lines: list[tuple[float, str]] = []
        self._cond = threading.Condition()
        self._link: serial.Serial | None = None
        self._pending = b""
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self, timeout: float = 10.0) -> None:
        self._thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._cond:
                if self._link is not None:
                    return
            time.sleep(0.05)
        raise TimeoutError("card USB serial port not found")

    def close(self) -> None:
        self._stop = True
        self._thread.join(timeout=2.0)
        with self._cond:
            if self._link is not None:
                self._link.close()
                self._link = None

    def _open(self) -> None:
        ports = sorted(glob.glob(self.port_glob))
        if not ports:
            time.sleep(0.1)
            return
        try:
            link = serial.Serial(ports[0], 115200, timeout=0.05)
        except (serial.SerialException, OSError):
            time.sleep(0.1)
            return
        self._keep_quiet(link)
        with self._cond:
            self._link = link
            self._pending = b""
            self._cond.notify_all()

    def _keep_quiet(self, link: serial.Serial) -> None:
        if not self.mute:
            return
        try:
            link.write(b"mute on\n")
            link.flush()
        except (serial.SerialException, OSError):
            pass

    def _run(self) -> None:
        while not self._stop:
            link = self._link
            if link is None:
                self._open()
                continue
            try:
                chunk = link.read(4096)
            except (serial.SerialException, OSError, TypeError):
                with self._cond:
                    try:
                        link.close()
                    except (serial.SerialException, OSError):
                        pass
                    self._link = None
                continue
            if not chunk:
                continue
            now = time.time()
            self._pending += chunk
            rebooted = False
            with self._cond:
                while b"\n" in self._pending:
                    line, self._pending = self._pending.split(b"\n", 1)
                    text = line.decode("utf-8", "replace").rstrip("\r")
                    rebooted = rebooted or text.startswith("READY ")
                    self.lines.append((now, text))
                self._cond.notify_all()
            if rebooted:
                self._keep_quiet(link)

    def mark(self) -> int:
        with self._cond:
            return len(self.lines)

    def send(self, command: str, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            with self._cond:
                link = self._link
            if link is not None:
                try:
                    link.write((command + "\n").encode())
                    link.flush()
                    return
                except (serial.SerialException, OSError):
                    pass
            if time.monotonic() > deadline:
                raise TimeoutError(f"could not send {command!r}: card port missing")
            time.sleep(0.05)

    def wait_for(self, predicate, timeout: float, start: int = 0) -> tuple[int, float, str]:
        deadline = time.monotonic() + timeout
        index = start
        with self._cond:
            while True:
                while index < len(self.lines):
                    t, line = self.lines[index]
                    if predicate(line):
                        return index, t, line
                    index += 1
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError(f"card line not seen within {timeout} s")
                self._cond.wait(left)

    def request(self, command: str, timeout: float = 5.0) -> str:
        verb = command.split()[0]
        start = self.mark()
        self.send(command)
        return self.wait_for(lambda line: is_reply(line, verb), timeout, start)[2]

    def since(self, start: int) -> list[tuple[float, str]]:
        with self._cond:
            return list(self.lines[start:])

    def snapshot(self, timeout: float = 30.0) -> tuple[dict, int, int, bytes]:
        start = self.mark()
        self.send("snap")
        begin, _, _ = self.wait_for(lambda line: line.startswith("SNAP BEGIN"), timeout, start)
        end, _, _ = self.wait_for(lambda line: line.startswith("SNAP END") or line == "ERR snap write timed out",
                                  timeout, begin)
        with self._cond:
            lines = [line for _, line in self.lines[begin:end + 1] if line.startswith("SNAP ")]
        if not lines[-1].startswith("SNAP END"):
            raise RuntimeError("card gave up writing the snapshot")
        return parse_snapshot(lines)
