import fcntl
import json
import os
import re
import select
import signal
import struct
import subprocess
import termios
import threading
import time
from pathlib import Path

CURSOR_FORWARD = re.compile(rb"\x1b\[(\d*)C")
ESCAPES = re.compile(rb"\x1b\[[0-9;?<>=]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[@-Z\\-_]")
INHERITED_PREFIXES = ("CLAUDE", "ANTHROPIC", "GROK_", "USE_LOCAL_OAUTH", "USE_STAGING_OAUTH", "MCP_", "OTEL_")
DOWN = "\x1b[B"
TRUST_YES = "❯Yes,Itrustthisfolder"
TRUST_NO = "❯No,exit"


def plain(data: bytes) -> str:
    data = CURSOR_FORWARD.sub(lambda m: b" " * int(m.group(1) or 1), data)
    return ESCAPES.sub(b"", data).decode("utf-8", "replace")


def squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text)


class ClaudeSession:
    def __init__(self, workdir: Path, settings: Path, out: Path, model: str = "haiku"):
        self.workdir = Path(workdir)
        self.out = Path(out)
        self.argv = ["claude", "--setting-sources", "project", "--settings", str(settings),
                     "--model", model, "--permission-mode", "default"]
        self.timeline: list[dict] = []
        self.text = ""
        self.last_output = time.time()
        self.proc = None
        self.fd = None
        self.start_time = None
        self.exit_status = None
        self._lock = threading.Lock()
        self._reader = None
        self._raw = None

    def note(self, event: str, **fields) -> None:
        with self._lock:
            self.timeline.append({"t": time.time(), "event": event, **fields})

    def start(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.out.mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith(INHERITED_PREFIXES)}
        env["TERM"] = "xterm-256color"
        self.start_time = time.time()
        master, slave = os.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        self.proc = subprocess.Popen(self.argv, cwd=self.workdir, env=env, stdin=slave, stdout=slave, stderr=slave,
                                     start_new_session=True)
        os.close(slave)
        self.fd = master
        self._raw = open(self.out / "terminal.raw", "wb", buffering=0)
        self.note("started", argv=self.argv)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        while True:
            try:
                ready, _, _ = select.select([self.fd], [], [], 0.2)
            except (OSError, ValueError):
                return
            if not ready:
                continue
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            now = time.time()
            self._raw.write(struct.pack(">dI", now - self.start_time, len(chunk)) + chunk)
            with self._lock:
                self.text += plain(chunk)
                self.last_output = now

    def screen(self) -> str:
        with self._lock:
            return squeeze(self.text)

    def mark(self) -> int:
        return len(self.screen())

    def wait_screen(self, needle: str, timeout: float, after: int = 0) -> int:
        target = squeeze(needle)
        deadline = time.time() + timeout
        while time.time() < deadline:
            index = self.screen().find(target, after)
            if index >= 0:
                self.note("screen", needle=needle)
                return index
            time.sleep(0.1)
        raise TimeoutError(f"{needle!r} did not appear on the Claude Code screen within {timeout} s")

    def wait_quiet(self, seconds: float, timeout: float) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                quiet = time.time() - self.last_output
            if quiet >= seconds:
                return
            time.sleep(0.1)
        raise TimeoutError(f"Claude Code screen kept changing for {timeout} s")

    def send(self, keys: str, label: str) -> None:
        os.write(self.fd, keys.encode())
        self.note("sent", keys=label)

    def type_line(self, text: str, label: str) -> None:
        os.write(self.fd, text.encode())
        time.sleep(0.5)
        self.send("\r", label)

    def pass_startup(self, ready, timeout: float = 60.0) -> None:
        deadline = time.time() + timeout
        answered_at = -1
        while time.time() < deadline:
            flat = self.screen()
            asking = flat.rfind(TRUST_NO)
            with self._lock:
                quiet = time.time() - self.last_output
            if asking > answered_at and asking > flat.rfind(TRUST_YES) and quiet >= 1.0:
                self.send(DOWN, "down (trust folder)")
                self.wait_screen(TRUST_YES, 5.0, asking)
                time.sleep(0.3)
                self.send("\r", "enter (trust folder)")
                answered_at = len(self.screen())
            if ready():
                self.wait_quiet(2.0, max(1.0, deadline - time.time()))
                return
            time.sleep(0.2)
        raise TimeoutError("Claude Code did not become ready")

    def alive(self) -> bool:
        self.exit_status = self.proc.poll()
        return self.exit_status is None

    def exit(self, timeout: float = 15.0) -> None:
        for _ in range(2):
            if not self.alive():
                break
            self.send("\x03", "ctrl-c")
            time.sleep(0.7)
        deadline = time.time() + timeout
        while self.alive() and time.time() < deadline:
            time.sleep(0.1)

    def close(self) -> None:
        if self.proc is None:
            return
        for sig in (None, signal.SIGTERM, signal.SIGKILL):
            if not self.alive():
                break
            if sig is not None:
                self.proc.send_signal(sig)
                self.note("signal", signal=int(sig))
            deadline = time.time() + 3.0
            while self.alive() and time.time() < deadline:
                time.sleep(0.1)
        self.exit_status = self.proc.wait()
        try:
            os.close(self.fd)
        except OSError:
            pass
        if self._reader is not None:
            self._reader.join(timeout=2.0)
        self._raw.close()
        with self._lock:
            (self.out / "terminal.txt").write_text(self.text, encoding="utf-8")
            timeline = [{**e, "t_rel": round(e["t"] - self.start_time, 3)} for e in self.timeline]
        (self.out / "claude-timeline.json").write_text(
            json.dumps({"argv": self.argv, "exit_status": self.exit_status, "timeline": timeline}, indent=2) + "\n",
            encoding="utf-8")
        self.proc = None
