import glob
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from cardwatch import CardWatcher  # noqa: E402
from claude_session import ClaudeSession  # noqa: E402

APP = ROOT / "mac" / "bridge" / "build" / "FunMedalBridge.app"
BRIDGE = APP / "Contents" / "MacOS" / "funmedal-bridge"
HOOK_EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest", "PostToolUse",
               "PostToolUseFailure", "Stop", "StopFailure", "SessionEnd"]
REJECTION_HOLD_S = 15.0
REJECT_KEYS = {"esc": ("\x1b", "esc (reject in terminal)"), "no": ("3", "3 (choose No in terminal)")}
HOOK_FIELDS = {"op", "event", "session", "tool_name", "tool_key", "source", "reason", "error"}
SOCKET_PREFIX = "fm-bridge-"
MIN_STACK_FREE_BYTES = 1024
ALREADY_RUNNING_EXIT = 3


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


class Bridge:
    def __init__(self, workdir: Path):
        self.socket = os.path.join(tempfile.gettempdir(), f"{SOCKET_PREFIX}{uuid.uuid4().hex[:8]}.sock")
        self.log = workdir / "bridge.jsonl"
        self.started = False

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

    def hook(self, payload):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return bridge_cli("hook", "--socket", self.socket, payload=data)

    def settle(self, expected, timeout=5.0):
        try:
            return wait_until(lambda: (s := self.status()) and s["desired"] == expected
                              and s["card_state"] == expected and s, timeout, f"card showing {expected}")
        except TimeoutError as error:
            raise TimeoutError(f"{error}; last status: {self.status()}") from error

    def quit(self):
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


def card_states(card, start):
    return [line.split()[1] for _, line in card.since(start) if line.startswith("STATE ")]


def ble_writes(card, start):
    return [line.split()[2] for _, line in card.since(start) if line.startswith("BLE state ")]


@pytest.fixture(scope="module")
def artifacts():
    base = Path(os.environ.get("FM_ARTIFACTS") or ROOT / "artifacts" / f"fm13-{datetime.now():%Y%m%d-%H%M%S}")
    path = base.resolve() / "bridge"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="module")
def card(artifacts):
    if not glob.glob("/dev/cu.usbmodem*"):
        pytest.skip("card USB serial port not found")
    if not BRIDGE.exists():
        pytest.fail(f"bridge app not built: run mac/bridge/build.sh ({BRIDGE} missing)")
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
        with (artifacts / "serial.log").open("w", encoding="utf-8") as f:
            for t, line in watcher.lines:
                f.write(f"{t:.3f} {line}\n")
        watcher.close()


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
    hooks = {name: [{"hooks": [{"type": "command", "command": command, "timeout": 5}]}] for name in HOOK_EVENTS}
    path.write_text(json.dumps({"sandbox": {"enabled": False}, "hooks": hooks}, indent=2) + "\n", encoding="utf-8")


def start_real_session(card, bridge, run_dir):
    bridge.start()
    settings = run_dir / "settings.json"
    write_settings(settings, bridge)
    workdir = Path(tempfile.mkdtemp(prefix="fm13-claude-"))
    marker = workdir / "fm13-marker.txt"
    session = ClaudeSession(workdir, settings, run_dir)
    session.start()
    return session, marker


def end_real_session(session):
    session.close()
    shutil.rmtree(session.workdir)


def wait_session_ready(session, bridge):
    session.pass_startup(lambda: (s := bridge.status()) and len(s["sessions"]) == 1)


def request_touch(session, marker):
    session.type_line(f"Run exactly this one shell command with the Bash tool and nothing else: touch {marker}  "
                      f"Then reply with the single word done.", "enter (prompt)")


def hook_times(bridge, name):
    return [e["t"] for e in bridge.log_entries() if e.get("kind") == "event" and e.get("event") == name]


@pytest.mark.parametrize("case", ["[FM-13] real session shows working, waiting, working, done, idle"])
def test_real_session_states(case, card, bridge, run_dir):
    session, marker = start_real_session(card, bridge, run_dir)
    summary = {}
    try:
        wait_session_ready(session, bridge)
        start = card.mark()
        request_touch(session, marker)
        _, waiting_t, _ = card.wait_for(lambda line: line == "STATE waiting", 120.0, start)
        session.wait_screen("Do you want to proceed", 10.0)
        session.send("1", "1 (yes in terminal)")
        time.sleep(0.3)
        session.send("\r", "enter (confirm)")
        wait_until(marker.exists, 30.0, "marker file")
        card.wait_for(lambda line: line == "STATE done", 90.0, start)
        session.exit()
        card.wait_for(lambda line: line == "STATE idle", 20.0, start)
        permission_t = hook_times(bridge, "PermissionRequest")
        summary = {"states": card_states(card, start), "writes": ble_writes(card, start),
                   "permission_request_t": permission_t, "card_waiting_t": waiting_t,
                   "latency_s": waiting_t - permission_t[0] if permission_t else None,
                   "status": bridge.status()}
    finally:
        end_real_session(session)
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    assert summary["states"] == ["working", "waiting", "working", "done", "idle"], summary
    assert summary["writes"] == summary["states"], summary
    assert len(summary["permission_request_t"]) == 1, summary
    assert 0 <= summary["latency_s"] < 1.0, summary


@pytest.mark.parametrize("way,case", [
    ("esc", "[FM-13] rejecting with Esc in the terminal keeps waiting until the next prompt"),
    ("no", "[FM-13] choosing No in the terminal keeps waiting until the next prompt"),
])
def test_real_session_terminal_rejection(way, case, card, bridge, run_dir):
    session, marker = start_real_session(card, bridge, run_dir)
    summary = {}
    try:
        wait_session_ready(session, bridge)
        start = card.mark()
        request_touch(session, marker)
        card.wait_for(lambda line: line == "STATE waiting", 120.0, start)
        session.wait_screen("Do you want to proceed", 10.0)
        session.send(*REJECT_KEYS[way])
        session.wait_screen("Interrupted", 10.0)
        time.sleep(REJECTION_HOLD_S)
        held = card_states(card, start)
        events_after_rejection = [e["event"] for e in bridge.log_entries() if e.get("kind") == "event"]
        session.type_line("Reply with the single word done.", "enter (next prompt)")
        card.wait_for(lambda line: line == "STATE done", 90.0, start)
        session.exit()
        card.wait_for(lambda line: line == "STATE idle", 20.0, start)
        summary = {"held_states": held, "events_until_next_prompt": events_after_rejection,
                   "states": card_states(card, start), "marker_exists": marker.exists()}
    finally:
        end_real_session(session)
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    assert not summary["marker_exists"], summary
    assert summary["held_states"] == ["working", "waiting"], summary
    assert summary["states"] == ["working", "waiting", "working", "done", "idle"], summary


@pytest.mark.parametrize("case", ["[FM-13] connecting writes the current state over what the card showed"])
def test_connect_writes_current_state(case, card, bridge):
    assert card.request("state error") == "OK state error"
    start = card.mark()
    bridge.start()
    card.wait_for(lambda line: line == "STATE idle", 5.0, start)
    assert ble_writes(card, start) == ["idle"]


@pytest.mark.parametrize("case", ["[FM-13] several sessions show the most urgent state"])
def test_sessions_aggregate(case, card, bridge, run_dir):
    bridge.start()
    start = card.mark()
    steps = [
        (event("SessionStart", "a", source="startup"), "idle"),
        (event("SessionStart", "b", source="startup"), "idle"),
        (event("UserPromptSubmit", "a"), "working"),
        (event("UserPromptSubmit", "b"), "working"),
        (event("Stop", "b", stop_hook_active=False), "done"),
        (event("PermissionRequest", "a", **bash("rm -rf build")), "waiting"),
        (event("PostToolUse", "a", tool_use_id="t1", tool_response={}, **bash("rm -rf build")), "done"),
        (event("SessionEnd", "b", reason="prompt_input_exit"), "working"),
        (event("StopFailure", "a", error="rate_limit"), "error"),
        (event("UserPromptSubmit", "a"), "working"),
        (event("PreToolUse", "c", tool_use_id="t2", **bash("ls")), "working"),
        (event("Stop", "a", stop_hook_active=False), "done"),
        (event("SessionStart", "a", source="compact"), "done"),
        (event("SessionEnd", "a", reason="other"), "working"),
        (event("SessionEnd", "c", reason="clear"), "idle"),
    ]
    trail = []
    for payload, expected in steps:
        result, elapsed = bridge.hook(payload)
        status = bridge.settle(expected)
        trail.append({"event": payload["hook_event_name"], "session": payload["session_id"], "expected": expected,
                      "hook_exit": result.returncode, "hook_s": elapsed, "stdout": result.stdout.decode()})
    final = bridge.status()
    (run_dir / "summary.json").write_text(json.dumps({"trail": trail, "status": final,
                                                      "states": card_states(card, start)}, indent=2) + "\n",
                                          encoding="utf-8")
    assert all(t["hook_exit"] == 0 and t["stdout"] == "" for t in trail), trail
    assert final["sessions"] == {}, final
    expected_states = ["working", "done", "waiting", "done", "working", "error", "working", "done", "working", "idle"]
    assert card_states(card, start) == expected_states
    assert ble_writes(card, start) == expected_states


@pytest.mark.parametrize("case", ["[FM-13] parallel approvals keep waiting until each one resolves"])
def test_parallel_approvals(case, card, bridge, run_dir):
    bridge.start()
    start = card.mark()
    first = {"tool_name": "Bash", "tool_input": {"command": "npm test", "description": "run tests"}}
    first_reordered = {"tool_name": "Bash", "tool_input": {"description": "run tests", "command": "npm test"}}
    second = {"tool_name": "Write", "tool_input": {"file_path": "/tmp/fm13-project/a.txt", "content": "hello"}}
    steps = [
        (event("UserPromptSubmit", "p"), "working"),
        (event("PermissionRequest", "p", **first), "waiting"),
        (event("PermissionRequest", "p", **second), "waiting"),
        (event("PostToolUse", "p", tool_use_id="t1", tool_response={}, **first_reordered), "waiting"),
        (event("PostToolUseFailure", "p", tool_use_id="t2", error="Exit code 1", **second), "working"),
        (event("PermissionRequest", "p", **bash("git push")), "waiting"),
        (event("UserPromptSubmit", "p"), "working"),
        (event("PermissionRequest", "p", **bash("git push")), "waiting"),
        (event("Stop", "p", stop_hook_active=False), "done"),
        (event("Notification", "p", notification_type="idle_prompt", message="Claude is waiting for your input"),
         "done"),
    ]
    for payload, expected in steps:
        result, _ = bridge.hook(payload)
        assert result.returncode == 0
        bridge.settle(expected)
    expected_states = ["working", "waiting", "working", "waiting", "working", "waiting", "done"]
    (run_dir / "summary.json").write_text(json.dumps({"states": card_states(card, start),
                                                      "status": bridge.status()}, indent=2) + "\n",
                                          encoding="utf-8")
    assert card_states(card, start) == expected_states


@pytest.mark.parametrize("case", ["[FM-13] hook exits at once with no output when no bridge is listening"])
def test_hook_without_bridge(case, tmp_path):
    if not BRIDGE.exists():
        pytest.fail(f"bridge app not built: run mac/bridge/build.sh ({BRIDGE} missing)")
    missing = os.path.join(tempfile.gettempdir(), f"fm-absent-{uuid.uuid4().hex[:8]}.sock")
    stale = os.path.join(tempfile.gettempdir(), f"fm-stale-{uuid.uuid4().hex[:8]}.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(stale)
    listener.close()
    try:
        payload = json.dumps(event("UserPromptSubmit", "x")).encode()
        for path in (missing, stale):
            result, elapsed = bridge_cli("hook", "--socket", path, payload=payload)
            assert result.returncode == 0, (path, result)
            assert result.stdout == b"", (path, result.stdout)
            assert elapsed < 1.0, (path, elapsed)
    finally:
        os.unlink(stale)


@pytest.mark.parametrize("case", ["[FM-13] hook sends only the listed fields and never tool content"])
def test_hook_sends_only_listed_fields(case, artifacts):
    if not BRIDGE.exists():
        pytest.fail(f"bridge app not built: run mac/bridge/build.sh ({BRIDGE} missing)")
    secret = f"fm13-secret-{uuid.uuid4().hex}"
    path = os.path.join(tempfile.gettempdir(), f"fm-capture-{uuid.uuid4().hex[:8]}.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(4)
    listener.settimeout(5.0)
    write = {"tool_name": "Write", "tool_input": {"file_path": f"/tmp/{secret}.txt", "content": secret}}
    cases = [
        (event("PostToolUse", "c", tool_use_id="t1", tool_response={"output": secret}, **write),
         {"tool_name": "Write"}),
        (event("PermissionRequest", "c", permission_suggestions=[{"type": "addRules", "rules": [secret]}],
               **bash(f"echo {secret}")), {"tool_name": "Bash"}),
        (event("UserPromptSubmit", "c", prompt=secret), {}),
        (event("SessionStart", "c", source="compact", model="haiku"), {"source": "compact"}),
        (event("StopFailure", "c", error="rate_limit", error_details=secret), {"error": "rate_limit"}),
        (event("Stop", "c", stop_hook_active=False, last_assistant_message=secret), {}),
        (event("SessionEnd", "c", reason="prompt_input_exit"), {"reason": "prompt_input_exit"}),
    ]
    captured = []
    try:
        for payload, expected in cases:
            received = {}

            def serve_one():
                client, _ = listener.accept()
                data = b""
                while b"\n" not in data and (chunk := client.recv(65536)):
                    data += chunk
                client.sendall(b'{"ok":true}\n')
                client.close()
                received["data"] = data

            server = threading.Thread(target=serve_one)
            server.start()
            result, elapsed = bridge_cli("hook", "--socket", path, payload=json.dumps(payload).encode())
            server.join(5.0)
            data = received.get("data", b"")
            message = json.loads(data) if data else {}
            captured.append({"event": payload["hook_event_name"], "exit": result.returncode, "s": elapsed,
                             "stdout": result.stdout.decode(), "sent": message})
            assert result.returncode == 0 and result.stdout == b"", result
            assert secret.encode() not in data, data
            assert set(message) <= HOOK_FIELDS, message
            assert message["op"] == "event" and message["event"] == payload["hook_event_name"], message
            assert message["session"] == "c", message
            assert all(message.get(k) == v for k, v in expected.items()), (expected, message)
            assert ("tool_key" in message) == ("tool_input" in payload), message
    finally:
        listener.close()
        os.unlink(path)
        (artifacts / "hook-captured.json").write_text(json.dumps(captured, indent=2) + "\n", encoding="utf-8")


@pytest.mark.parametrize("case", ["[FM-13] hook survives bad and huge input without leaking tool content"])
def test_hook_bad_and_huge_input(case, card, bridge, run_dir):
    bridge.start()
    before = bridge.status()
    secret = f"fm13-secret-{uuid.uuid4().hex}"
    huge = event("PostToolUse", "h", tool_use_id="t9", tool_name="Write",
                 tool_input={"file_path": "/tmp/fm13-project/big.txt", "content": secret},
                 tool_response={"output": secret + "x" * 5_000_000})
    results = {}
    for name, payload in [("empty", b""), ("text", b"not json"), ("array", b"[1, 2]"),
                          ("no-session", json.dumps({"hook_event_name": "Stop"}).encode()),
                          ("huge", json.dumps(huge).encode())]:
        result, elapsed = bridge.hook(payload)
        results[name] = {"exit": result.returncode, "stdout": result.stdout.decode(), "s": elapsed}
    after = bridge.settle("working")
    replies = {}
    for name, data in [("garbage", b"garbage\n"), ("unknown-op", b'{"op": "nope"}\n'),
                       ("event-without-fields", b'{"op": "event"}\n'), ("oversized", b"a" * 2_000_000)]:
        replies[name] = raw_exchange(bridge.socket, data).decode("utf-8", "replace")
    last = bridge.status()
    log_text = bridge.log.read_text(encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps({"results": results, "replies": replies, "before": before,
                                                      "after": after, "last": last}, indent=2) + "\n",
                                          encoding="utf-8")
    assert all(r["exit"] == 0 and r["stdout"] == "" for r in results.values()), results
    assert all(r["s"] < 1.0 for name, r in results.items() if name != "huge"), results
    assert results["huge"]["s"] < 1.0, results
    assert after["events"] - before["events"] == 1, (before, after)
    assert after["rejected"] == before["rejected"], (before, after)
    assert all(json.loads(r)["ok"] is False for name, r in replies.items() if name != "oversized"), replies
    assert last is not None and last["rejected"] - after["rejected"] == 4, (after, last)
    assert last["card"] == "connected" and last["card_state"] == "working", last
    assert secret not in log_text


@pytest.mark.parametrize("case", ["[FM-13] stale socket file is replaced and a second bridge refuses to start"])
def test_stale_socket_and_second_instance(case, card, bridge, run_dir):
    leftover = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    leftover.bind(bridge.socket)
    leftover.close()
    bridge.start()
    result, elapsed = bridge_cli("serve", "--socket", bridge.socket, "--log", str(run_dir / "second.jsonl"))
    status = bridge.status()
    (run_dir / "summary.json").write_text(json.dumps({"second_exit": result.returncode,
                                                      "second_stderr": result.stderr.decode(),
                                                      "second_s": elapsed, "status": status}, indent=2) + "\n",
                                          encoding="utf-8")
    assert result.returncode == ALREADY_RUNNING_EXIT, result
    assert b"already running" in result.stderr, result.stderr
    assert status is not None and status["card"] == "connected", status


@pytest.mark.parametrize("case", ["[FM-13] card tasks keep stack headroom after the bluetooth tests"])
def test_card_stack_headroom(case, card, artifacts):
    line = card.request("stacks")
    info = card.request("info")
    (artifacts / "card-stacks.txt").write_text(line + "\n" + info + "\n", encoding="utf-8")
    free = {name: int(value) for name, value in (p.split("=", 1) for p in line.split()[2:])}
    assert set(free) == {"keys", "display", "console", "nimble_host"}, line
    assert all(value >= MIN_STACK_FREE_BYTES for value in free.values()), free


@pytest.mark.parametrize("case", ["[FM-13] card reboot is followed by reconnect and the current state"])
def test_card_reboot_restores_state(case, card, bridge, run_dir):
    bridge.start()
    for payload, expected in [(event("SessionStart", "s1", source="startup"), "idle"),
                              (event("UserPromptSubmit", "s1"), "working"),
                              (event("PermissionRequest", "s1", **bash("make test")), "waiting")]:
        result, _ = bridge.hook(payload)
        assert result.returncode == 0
        bridge.settle(expected)
    start = card.mark()
    rebooted_at = time.time()
    assert card.request("reboot") == "OK reboot"
    ready_index, ready_t, _ = card.wait_for(lambda line: line.startswith("READY "), 20.0, start)
    _, restored_t, _ = card.wait_for(lambda line: line == "STATE waiting", 30.0, ready_index)
    restored = info_fields(card.request("info"))
    after_reboot = card.mark()
    for payload, expected in [(event("PostToolUse", "s1", tool_use_id="t1", tool_response={}, **bash("make test")),
                               "working"),
                              (event("Stop", "s1", stop_hook_active=False), "done")]:
        result, _ = bridge.hook(payload)
        assert result.returncode == 0
        bridge.settle(expected)
    summary = {"ready_after_s": ready_t - rebooted_at, "restored_after_s": restored_t - rebooted_at,
               "restored_info": restored, "status": bridge.status(),
               "states_after_reboot": card_states(card, after_reboot)}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    assert int(restored["uptime_s"]) < 60, summary
    assert restored["state"] == "waiting" and restored["ble"] == "connected", summary
    assert summary["states_after_reboot"] == ["working", "done"], summary
    assert summary["status"]["connects"] >= 2, summary
