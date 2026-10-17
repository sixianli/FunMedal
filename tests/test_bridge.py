import json
import os
import re
import socket
import tempfile
import threading
import time
import uuid

import pytest

from bridge_harness import (artifacts, bash, ble_writes, bridge, bridge_cli, card, card_lines,  # noqa: F401
                            card_states, event, hook_times, info_fields, raw_exchange, real_session, request_touch,
                            require_bridge_app, run_dir, start_and_mark, wait_session_ready, wait_until, write_summary)

ARTIFACTS_PREFIX = "fm13"
ARTIFACTS_SUBDIR = "bridge"
HOOK_FIELDS = {"op", "event", "session", "tool_name", "tool_key", "source", "reason", "error", "claude_pid"}
MIN_STACK_FREE_BYTES = 1024
ALREADY_RUNNING_EXIT = 3
FIXED_CHANGED_HANDLE = 3
TOGGLE_SECONDS = os.environ.get("FM_BLUETOOTH_TOGGLE_SECONDS")
RECONNECT_S = 60.0


@pytest.mark.parametrize("case", ["[FM-13] real session shows working, waiting, working, done, idle"])
def test_real_session_states(case, card, bridge, run_dir):
    summary = {}
    try:
        with real_session(bridge, run_dir) as (session, marker):
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
        write_summary(run_dir, summary)
    assert summary["states"] == ["working", "waiting", "working", "done", "idle"], summary
    assert summary["writes"] == summary["states"], summary
    assert len(summary["permission_request_t"]) == 1, summary
    assert 0 <= summary["latency_s"] < 1.0, summary


@pytest.mark.parametrize("case", ["[FM-13] connecting writes the current state over what the card showed"])
def test_connect_writes_current_state(case, card, bridge):
    assert card.request("state error") == "OK state error"
    start = card.mark()
    bridge.start()
    card.wait_for(lambda line: line == "STATE idle", 5.0, start)
    assert ble_writes(card, start) == ["idle"]


@pytest.mark.parametrize("case", ["[FM-13] several sessions show the most urgent state"])
def test_sessions_aggregate(case, card, bridge, run_dir):
    start = start_and_mark(bridge, card)
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
        result, elapsed = bridge.hook(payload, wait_limit=0)
        bridge.settle(expected)
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
    start = start_and_mark(bridge, card)
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
        result, _ = bridge.hook(payload, wait_limit=0)
        assert result.returncode == 0
        bridge.settle(expected)
    expected_states = ["working", "waiting", "working", "waiting", "working", "waiting", "done"]
    (run_dir / "summary.json").write_text(json.dumps({"states": card_states(card, start),
                                                      "status": bridge.status()}, indent=2) + "\n",
                                          encoding="utf-8")
    assert card_states(card, start) == expected_states


@pytest.mark.parametrize("case", ["[FM-13] a hook for an event other than session start exits at once with no "
                                  "output when no bridge is listening"])
def test_hook_without_bridge(case, tmp_path):
    require_bridge_app()
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
    require_bridge_app()
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
    assert set(free) == {"keys", "display", "console", "nimble_host", "voice"}, line
    assert all(value >= MIN_STACK_FREE_BYTES for value in free.values()), free


@pytest.mark.parametrize("case", ["[FM-13] card reboot is followed by reconnect and the current state"])
def test_card_reboot_restores_state(case, card, bridge, run_dir):
    bridge.start()
    for payload, expected in [(event("SessionStart", "s1", source="startup"), "idle"),
                              (event("UserPromptSubmit", "s1"), "working"),
                              (event("PermissionRequest", "s1", **bash("make test")), "waiting")]:
        result, _ = bridge.hook(payload, wait_limit=0)
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
        result, _ = bridge.hook(payload, wait_limit=0)
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

def reboot_and_read_layout(card):
    mark = card.mark()
    assert card.request("reboot") == "OK reboot"
    ready, _, _ = card.wait_for(lambda line: line.startswith("READY "), 20.0, mark)
    _, _, layout = card.wait_for(lambda line: line.startswith("GATT layout "), 10.0, mark)
    return {"mark": mark, "ready": ready, "layout": layout}


@pytest.mark.parametrize("case", ["[FM-13] the layout-change notice sits first, where no other service can move it"])
def test_changed_notice_position_fixed(case, card, run_dir):
    info = info_fields(card.request("info"))
    write_summary(run_dir, {"info": info})
    assert info.get("gatt_changed") == str(FIXED_CHANGED_HANDLE), info


@pytest.mark.parametrize("case", ["[FM-13] a changed bluetooth layout is announced until the Mac confirms it, then not again"])
def test_layout_change_announced_once(case, card, bridge, run_dir):
    assert card.request("ble layout forget") == "OK ble layout forget"
    first = reboot_and_read_layout(card)
    second = reboot_and_read_layout(card)
    status = bridge.start()
    _, _, sent = card.wait_for(lambda line: line.startswith("GATT changed sent"), 30.0, second["mark"])
    card.wait_for(lambda line: line == "GATT layout saved", 30.0, second["mark"])
    third = reboot_and_read_layout(card)
    card.wait_for(lambda line: line == f"BLE state {status['desired']}", 30.0, third["ready"])
    summary = {"first_boot": first["layout"], "second_boot": second["layout"], "third_boot": third["layout"],
               "sent": sent, "announced": card_lines(card, first["mark"], "GATT changed"),
               "announced_after_third_boot": card_lines(card, third["mark"], "GATT changed"),
               "subscriptions": card_lines(card, first["mark"], "BLE subscribe changed"),
               "bridge_kinds": sorted({entry["kind"] for entry in bridge.log_entries()}),
               "status": bridge.status()}
    write_summary(run_dir, summary)
    assert re.fullmatch(r"GATT layout changed subscribers=[1-9][0-9]*", first["layout"]), summary
    assert second["layout"] == first["layout"], summary
    assert sent == "GATT changed sent range=0001-ffff", summary
    assert "GATT changed acked" in summary["announced"], summary
    assert third["layout"] == "GATT layout same", summary
    assert summary["announced_after_third_boot"] == [], summary
    assert summary["status"]["card"] == "connected", summary


def bluetooth_states(bridge):
    return [e["state"] for e in bridge.log_entries() if e.get("kind") == "bluetooth"]


def turned_back_on(states):
    return "poweredOff" in states and states[-1] == "poweredOn"


@pytest.mark.skipif(not TOGGLE_SECONDS, reason="needs a person to turn the Mac's Bluetooth off and on; set FM_BLUETOOTH_TOGGLE_SECONDS")
@pytest.mark.parametrize("case", ["[FM-13] after the Mac's Bluetooth goes off and on, the bridge reconnects and shows the state that changed meanwhile"])
def test_mac_bluetooth_off_and_on(case, card, bridge, run_dir):
    seconds = float(TOGGLE_SECONDS)
    start = start_and_mark(bridge, card)
    bridge.hook(event("UserPromptSubmit", "a", prompt="x"))
    card.wait_for(lambda line: line == "BLE state working", 5.0, start)
    before = bridge.settle("working")
    (run_dir / "ready-off.txt").write_text(f"{time.time():.3f}\n", encoding="utf-8")
    wait_until(lambda: "poweredOff" in bluetooth_states(bridge), seconds, "Mac Bluetooth off")
    card.wait_for(lambda line: line.startswith("BLE disconnected"), 30.0, start)
    off = bridge.status()
    bridge.hook(event("Stop", "a", stop_hook_active=False))
    during = bridge.status()
    mark = card.mark()
    (run_dir / "ready-on.txt").write_text(f"{time.time():.3f}\n", encoding="utf-8")
    wait_until(lambda: turned_back_on(bluetooth_states(bridge)), seconds, "Mac Bluetooth on")
    card.wait_for(lambda line: line == "BLE state done", RECONNECT_S, mark)
    after = bridge.settle("done")
    summary = {"before": before, "off": off, "during": during, "after": after,
               "bluetooth": bluetooth_states(bridge), "card_lines": card_lines(card, start, "BLE "),
               "states_after_on": card_states(card, mark)}
    write_summary(run_dir, summary)
    assert before["card"] == "connected" and before["card_state"] == "working", summary
    assert off["card"] == "off" and off["card_state"] is None, summary
    assert during["desired"] == "done" and during["card"] == "off", summary
    assert after["card"] == "connected" and after["card_state"] == "done", summary
    lines = summary["card_lines"]
    dropped = next(i for i, line in enumerate(lines) if line.startswith("BLE disconnected"))
    assert "BLE connected" in lines[dropped:], summary
    assert summary["states_after_on"] == ["done"], summary
