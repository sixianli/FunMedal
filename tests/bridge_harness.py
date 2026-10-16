import glob
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from cardlink import compact_log  # noqa: E402
from cardwatch import CardWatcher  # noqa: E402
from claude_session import ClaudeSession  # noqa: E402

APP = ROOT / "mac" / "bridge" / "build" / "FunMedalBridge.app"
BRIDGE = APP / "Contents" / "MacOS" / "funmedal-bridge"
HOOK_EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest", "PostToolUse",
               "PostToolUseFailure", "Stop", "StopFailure", "SessionEnd"]
HOOK_TIMEOUT_S = 5
PERMISSION_HOOK_TIMEOUT_S = 600
SOCKET_PREFIX = "fm-bridge-"


def wait_until(check, timeout, what):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = check()
        if last:
            return last
        time.sleep(0.05)
    raise TimeoutError(f"{what} not reached within {timeout} s")


def bridge_cli(*args, payload=None, timeout=10.0):
    started = time.monotonic()
    result = subprocess.run([str(BRIDGE), *args], input=payload, capture_output=True, timeout=timeout)
    return result, time.monotonic() - started


def require_bridge_app():
    if not BRIDGE.exists():
        pytest.fail(f"bridge app not built: run mac/bridge/build.sh ({BRIDGE} missing)")


def encode(payload):
    return payload if isinstance(payload, bytes) else json.dumps(payload).encode()


def wait_limit_args(wait_limit):
    return [] if wait_limit is None else ["--wait-limit", str(wait_limit)]


class HookRun:
    def __init__(self, args, payload):
        self.started = time.monotonic()
        self.proc = subprocess.Popen([str(BRIDGE), *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
        self.proc.stdin.write(encode(payload))
        self.proc.stdin.close()
        self.ended = None

    def running(self):
        return self.proc.poll() is None

    def result(self, timeout=10.0):
        stdout, stderr = self.proc.communicate(timeout=timeout)
        if self.ended is None:
            self.ended = time.monotonic()
        return {"exit": self.proc.returncode, "stdout": stdout.decode(), "stderr": stderr.decode(),
                "s": self.ended - self.started}

    def stop(self):
        if self.running():
            self.proc.kill()
        self.proc.communicate()


class Bridge:
    def __init__(self, workdir: Path):
        self.socket = os.path.join(tempfile.gettempdir(), f"{SOCKET_PREFIX}{uuid.uuid4().hex[:8]}.sock")
        self.log = workdir / "bridge.jsonl"
        self.started = False
        self.runs: list[HookRun] = []

    def start(self, timeout=60.0):
        subprocess.run(["open", "-n", "-g", str(APP), "--args", "serve", "--socket", self.socket,
                        "--log", str(self.log)], check=True)
        self.started = True
        try:
            return wait_until(lambda: (s := self.status()) and s["card"] == "connected"
                              and s["card_state"] == s["desired"] and s, timeout, "bridge connected to the card")
        except TimeoutError as error:
            raise TimeoutError(f"{error}; last status: {self.status()}") from error

    def status(self):
        result, _ = bridge_cli("status", "--socket", self.socket)
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)

    def hook(self, payload, wait_limit=None):
        return bridge_cli("hook", "--socket", self.socket, *wait_limit_args(wait_limit), payload=encode(payload))

    def hook_async(self, payload, wait_limit=None):
        run = HookRun(["hook", "--socket", self.socket, *wait_limit_args(wait_limit)], payload)
        self.runs.append(run)
        return run

    def settle(self, expected, timeout=5.0):
        try:
            return wait_until(lambda: (s := self.status()) and s["desired"] == expected
                              and s["card_state"] == expected and s, timeout, f"card showing {expected}")
        except TimeoutError as error:
            raise TimeoutError(f"{error}; last status: {self.status()}") from error

    def approvals(self):
        status = self.status()
        return None if status is None else status["approvals"]

    def quit(self):
        for run in self.runs:
            run.stop()
        if not self.started:
            return
        bridge_cli("quit", "--socket", self.socket)
        wait_until(lambda: self.status() is None, 10.0, "bridge exit")
        self.started = False

    def log_entries(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]


def stop_leftover_bridges():
    for path in glob.glob(os.path.join(tempfile.gettempdir(), f"{SOCKET_PREFIX}*.sock")):
        bridge_cli("quit", "--socket", path)


def event(name, session, **fields):
    return {"hook_event_name": name, "session_id": session, "cwd": "/tmp/fm13-project",
            "transcript_path": f"/tmp/fm13-project/{session}.jsonl", "permission_mode": "default", **fields}


def bash(command, description="test command"):
    return {"tool_name": "Bash", "tool_input": {"command": command, "description": description}}


def start_and_mark(bridge, card, timeout=5.0):
    before = card.mark()
    status = bridge.start()
    card.wait_for(lambda line: line == f"BLE state {status['desired']}", timeout, before)
    return card.mark()


def card_states(card, start):
    return [line.split()[1] for _, line in card.since(start) if line.startswith("STATE ")]


def ble_writes(card, start):
    return [line.split()[2] for _, line in card.since(start) if line.startswith("BLE state ")]


def card_lines(card, start, prefix):
    return [line for _, line in card.since(start) if line.startswith(prefix)]


def info_fields(line):
    return dict(p.split("=", 1) for p in line.split()[2:] if "=" in p)


def raw_exchange(path, data):
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(5.0)
    client.connect(path)
    try:
        client.sendall(data)
        client.shutdown(socket.SHUT_WR)
        reply = b""
        while chunk := client.recv(4096):
            reply += chunk
        return reply
    except (BrokenPipeError, ConnectionResetError):
        return b""
    finally:
        client.close()


def _try_info(watcher):
    try:
        return watcher.request("info", timeout=1.0)
    except TimeoutError:
        return None


@pytest.fixture(scope="module")
def artifacts(request):
    prefix = request.module.ARTIFACTS_PREFIX
    base = Path(os.environ.get("FM_ARTIFACTS") or ROOT / "artifacts" / f"{prefix}-{datetime.now():%Y%m%d-%H%M%S}")
    path = base.resolve() / request.module.ARTIFACTS_SUBDIR
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="module")
def card(artifacts):
    if not glob.glob("/dev/cu.usbmodem*"):
        pytest.skip("card USB serial port not found")
    require_bridge_app()
    resident, _ = bridge_cli("status")
    if resident.returncode == 0:
        pytest.fail("a bridge is running on the default socket and holds the card's only bluetooth connection; "
                    f"stop it with `{BRIDGE} quit` first")
    stop_leftover_bridges()
    watcher = CardWatcher()
    watcher.start()
    try:
        info = wait_until(lambda: _try_info(watcher), 10.0, "card info")
        (artifacts / "card-info.txt").write_text(info + "\n", encoding="utf-8")
        assert " ble=" in info, info
        watcher.request("frame auto")
        yield watcher
    finally:
        lines = [f"{t:.3f} {line}" for t, line in watcher.lines]
        (artifacts / "serial.log").write_text("\n".join(compact_log(lines)) + "\n", encoding="utf-8")
        watcher.close()


@pytest.fixture
def run_dir(artifacts, request):
    callspec = getattr(request.node, "callspec", None)
    way = callspec.params.get("way") if callspec else None
    path = artifacts / request.node.originalname
    if way:
        path = path / way
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def bridge(card, run_dir):
    instance = Bridge(run_dir)
    yield instance
    instance.quit()


def write_settings(path: Path, bridge: Bridge):
    command = f"{shlex.quote(str(BRIDGE))} hook --socket {shlex.quote(bridge.socket)}"
    hooks = {name: [{"hooks": [{"type": "command", "command": command,
                                "timeout": PERMISSION_HOOK_TIMEOUT_S if name == "PermissionRequest"
                                else HOOK_TIMEOUT_S}]}] for name in HOOK_EVENTS}
    path.write_text(json.dumps({"sandbox": {"enabled": False}, "hooks": hooks}, indent=2) + "\n", encoding="utf-8")


@contextmanager
def real_session(bridge, run_dir):
    bridge.start()
    settings = run_dir / "settings.json"
    write_settings(settings, bridge)
    workdir = Path(tempfile.mkdtemp(prefix="fm-claude-"))
    session = ClaudeSession(workdir, settings, run_dir)
    session.start()
    try:
        yield session, workdir / "fm-marker.txt"
    finally:
        session.close()
        shutil.rmtree(workdir, ignore_errors=True)


def wait_session_ready(session, bridge):
    session.pass_startup(lambda: (s := bridge.status()) and len(s["sessions"]) == 1)


def request_touch(session, marker):
    session.type_line(f"Run exactly this one shell command with the Bash tool and nothing else: touch {marker}  "
                      f"Then reply with the single word done.", "enter (prompt)")


def hook_times(bridge, name):
    return [e["t"] for e in bridge.log_entries() if e.get("kind") == "event" and e.get("event") == name]


def write_summary(run_dir, summary):
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
