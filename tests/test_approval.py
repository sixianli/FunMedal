import hashlib
import json
import os
import re
import socket
import tempfile
import threading
import time
import uuid

import pytest

from bridge_harness import (Bridge, artifacts, bash, bridge, bridge_cli, card, card_lines, card_states,  # noqa: F401
                            event, info_fields, real_session, request_touch, require_bridge_app, run_dir,
                            start_and_mark, wait_session_ready, wait_until, write_summary)

import cardlink  # noqa: E402

ARTIFACTS_PREFIX = "fm14"
ARTIFACTS_SUBDIR = "approval"
APPROVE_FIELDS = {"op", "event", "session", "tool_name", "tool_key", "summary"}
SUMMARY_MAX_BYTES = 240
PROMPT_LINE = re.compile(r"PROMPT (\d+) (\d+)/(\d+) (\S+)$")
SECURE = "encrypted,authenticated,bonded"
MIN_STACK_FREE_BYTES = 1024
REJECTION_HOLD_S = 3.0
PROMPT_CLEAR_LIMIT_S = 0.5
PERSON_SECONDS = os.environ.get("FM_PERSON_AT_CARD_SECONDS")
W, H = 240, 320
WAITING_BACKGROUND = cardlink.rgb888_to_565_rgb(0xFF9F1C)


def approve(session, tool_name, tool_input):
    return event("PermissionRequest", session, tool_name=tool_name, tool_input=tool_input,
                 permission_suggestions=[{"type": "addRules", "rules": [{"toolName": tool_name}]}])


def behavior(stdout):
    if stdout.strip() == "":
        return None
    output = json.loads(stdout)
    assert output["hookSpecificOutput"]["hookEventName"] == "PermissionRequest", output
    return output["hookSpecificOutput"]["decision"]


def wait_prompt(card, start, timeout=10.0, prompt_id=None):
    def matches(line):
        found = PROMPT_LINE.match(line)
        return found is not None and (prompt_id is None or found.group(1) == str(prompt_id))
    index, t, line = card.wait_for(matches, timeout, start)
    found = PROMPT_LINE.match(line)
    return {"index": index, "t": t, "id": int(found.group(1)), "position": int(found.group(2)),
            "count": int(found.group(3)), "tool": found.group(4)}


def press(card, key, how):
    assert card.request(f"key {key} {how}") == f"OK key {key} {how}"


def log_kinds(bridge, kind):
    return [e for e in bridge.log_entries() if e.get("kind") == kind]


def snapshot(card, run_dir, name):
    meta, width, height, data = card.snapshot()
    assert (width, height) == (W, H)
    pixels = cardlink.rgb565_wire_to_rgb(data)
    path = run_dir / f"{name}.png"
    cardlink.write_png(path, width, height, pixels)
    return {"meta": meta, "png": path.name, "sha256": hashlib.sha256(data).hexdigest(), "pixels": pixels}


def ink(pixels, rows, background, columns=(0, W)):
    return sum(1 for y in range(*rows) for x in range(*columns) if pixels[y * W + x] != background)


class FakeBridge:
    def __init__(self):
        self.path = os.path.join(tempfile.gettempdir(), f"fm-fake-{uuid.uuid4().hex[:8]}.sock")
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(self.path)
        self.listener.listen(4)
        self.listener.settimeout(10.0)

    def serve_once(self, replies, keep_open, received):
        client, _ = self.listener.accept()
        client.settimeout(10.0)
        data = b""
        try:
            while b"\n" not in data and (chunk := client.recv(65536)):
                data += chunk
            for reply in replies:
                client.sendall(reply)
            if not keep_open:
                client.shutdown(socket.SHUT_WR)
            while chunk := client.recv(65536):
                data += chunk
        except (ConnectionResetError, socket.timeout):
            pass
        finally:
            client.close()
            received.append(data)

    def run(self, payload, replies, keep_open=False, wait_limit=None):
        received = []
        server = threading.Thread(target=self.serve_once, args=(replies, keep_open, received))
        server.start()
        args = ["hook", "--socket", self.path] + ([] if wait_limit is None else ["--wait-limit", str(wait_limit)])
        result, elapsed = bridge_cli(*args, payload=json.dumps(payload).encode(), timeout=20.0)
        server.join(15.0)
        lines = [json.loads(line) for line in (received[0] if received else b"").splitlines() if line.strip()]
        return result, elapsed, lines

    def close(self):
        self.listener.close()
        os.unlink(self.path)


SUMMARY_CASES = [
    ("Bash", {"command": "git push origin main", "description": "push"}, "git push origin main"),
    ("Bash", {"command": "cd build\n\tmake\x01 all", "description": "build"}, "cd build\n make  all"),
    ("Write", {"file_path": "/tmp/fm14/a.txt", "content": "SECRET"}, "/tmp/fm14/a.txt"),
    ("Edit", {"file_path": "/tmp/fm14/b.py", "old_string": "SECRET", "new_string": "x"}, "/tmp/fm14/b.py"),
    ("MultiEdit", {"file_path": "/tmp/fm14/c.py", "edits": [{"old_string": "SECRET"}]}, "/tmp/fm14/c.py"),
    ("NotebookEdit", {"notebook_path": "/tmp/fm14/d.ipynb", "new_source": "SECRET"}, "/tmp/fm14/d.ipynb"),
    ("Read", {"file_path": "/etc/hosts"}, "/etc/hosts"),
    ("WebFetch", {"url": "https://example.com/page", "prompt": "SECRET"}, "https://example.com/page"),
    ("WebSearch", {"query": "esp32 nimble pairing"}, "esp32 nimble pairing"),
    ("Task", {"description": "find callers", "prompt": "SECRET"}, "find callers"),
    ("Agent", {"description": "review diff", "prompt": "SECRET"}, "review diff"),
    ("mcp__notes__save", {"text": "SECRET"}, None),
]


@pytest.mark.parametrize("case", ["[FM-14] hook sends a summary only for approvals and follows the rules"])
def test_hook_summary_rules(case, artifacts):
    require_bridge_app()
    fake = FakeBridge()
    captured = []
    try:
        for tool_name, tool_input, expected in SUMMARY_CASES:
            result, elapsed, lines = fake.run(approve("s", tool_name, tool_input), [b'{"ok":true,"id":1}\n'])
            message = lines[0]
            captured.append({"tool": tool_name, "exit": result.returncode, "s": elapsed, "sent": message})
            assert result.returncode == 0 and result.stdout == b"", result
            assert message["op"] == "approve" and message["event"] == "PermissionRequest", message
            assert set(message) <= APPROVE_FIELDS, message
            assert message["tool_name"] == tool_name and len(message["tool_key"]) == 64, message
            assert message.get("summary") == expected, (tool_name, message)
            assert "SECRET" not in json.dumps(message), message
        long_command = "echo " + "你" * 100
        result, _, lines = fake.run(approve("s", "Bash", {"command": long_command}), [b'{"ok":true,"id":2}\n'])
        summary = lines[0]["summary"]
        captured.append({"tool": "Bash long", "sent": lines[0]})
        assert len(summary.encode()) <= SUMMARY_MAX_BYTES, summary
        assert len(summary.encode()) > SUMMARY_MAX_BYTES - 3, summary
        assert long_command.startswith(summary), summary
        result, _, lines = fake.run(event("UserPromptSubmit", "s", prompt="SECRET"), [b'{"ok":true}\n'])
        assert lines[0]["op"] == "event" and "summary" not in lines[0], lines
    finally:
        fake.close()
        (artifacts / "summary-captured.json").write_text(json.dumps(captured, indent=2, ensure_ascii=False) + "\n",
                                                         encoding="utf-8")


@pytest.mark.parametrize("case", ["[FM-14] hook prints the official answer for allow, deny and no answer"])
def test_hook_answers(case, artifacts):
    require_bridge_app()
    fake = FakeBridge()
    outcomes = {}
    request = approve("s", "Bash", {"command": "ls"})
    try:
        for name, replies in [("allow", [b'{"ok":true,"id":3}\n', b'{"decision":"allow"}\n']),
                              ("deny", [b'{"ok":true,"id":3}\n', b'{"decision":"deny"}\n']),
                              ("released", [b'{"ok":true,"id":3}\n', b'{"decision":null}\n']),
                              ("refused", [b'{"ok":false,"error":"x"}\n']),
                              ("closed", [])]:
            result, elapsed, lines = fake.run(request, replies)
            outcomes[name] = {"exit": result.returncode, "stdout": result.stdout.decode(), "s": elapsed,
                              "sent": lines}
        result, elapsed, lines = fake.run(request, [b'{"ok":true,"id":4}\n'], keep_open=True, wait_limit=1)
        outcomes["gave_up"] = {"exit": result.returncode, "stdout": result.stdout.decode(), "s": elapsed,
                               "sent": lines}
    finally:
        fake.close()
        (artifacts / "hook-answers.json").write_text(json.dumps(outcomes, indent=2) + "\n", encoding="utf-8")
    assert all(o["exit"] == 0 for o in outcomes.values()), outcomes
    assert behavior(outcomes["allow"]["stdout"]) == {"behavior": "allow"}
    denied = behavior(outcomes["deny"]["stdout"])
    assert denied["behavior"] == "deny" and "FunMedal card" in denied["message"], denied
    assert "interrupt" not in denied or denied["interrupt"] is False, denied
    for name in ("released", "refused", "closed", "gave_up"):
        assert outcomes[name]["stdout"] == "", (name, outcomes[name])
    assert 0.8 <= outcomes["gave_up"]["s"] < 3.0, outcomes["gave_up"]
    assert [m.get("op") for m in outcomes["gave_up"]["sent"]] == ["approve", "give_up"], outcomes["gave_up"]
    assert all(o["s"] < 2.0 for name, o in outcomes.items() if name != "gave_up"), outcomes


@pytest.mark.parametrize("case", ["[FM-14] approval hook exits at once with no output when no bridge is listening"])
def test_approval_without_bridge(case):
    require_bridge_app()
    missing = os.path.join(tempfile.gettempdir(), f"fm-absent-{uuid.uuid4().hex[:8]}.sock")
    result, elapsed = bridge_cli("hook", "--socket", missing,
                                 payload=json.dumps(approve("x", "Bash", {"command": "ls"})).encode())
    assert result.returncode == 0 and result.stdout == b"", result
    assert elapsed < 1.0, elapsed


@pytest.mark.parametrize("case", ["[FM-14] bluetooth link is encrypted, authenticated and bonded"])
def test_link_is_secure(case, card, bridge, run_dir):
    start = card.mark()
    bridge.start()
    first = info_fields(card.request("info"))
    bridge.quit()
    wait_until(lambda: info_fields(card.request("info"))["ble"] == "advertising", 10.0, "card advertising again")
    (run_dir / "again").mkdir(exist_ok=True)
    again = Bridge(run_dir / "again")
    try:
        again.start()
        second = info_fields(card.request("info"))
    finally:
        again.quit()
    lines = [line for _, line in card.since(start) if line.startswith(("BLE sec", "PAIR"))]
    write_summary(run_dir, {"first": first, "second": second, "lines": lines})
    assert first["sec"] == SECURE and second["sec"] == SECURE, (first, second)
    assert not any(line.startswith("PAIR") for line in lines), lines
    assert lines.count("BLE sec encrypted=1 authenticated=1 bonded=1 key_size=16") >= 2, lines


@pytest.mark.parametrize("case", ["[FM-14] approval screen shows the tool and command"])
def test_approval_screen(case, card, bridge, run_dir):
    bridge.start()
    card.request("frame 0")
    shots = {}
    first = bridge.hook_async(approve("v", "Bash", {"command": "echo 你好 && ls -la /tmp/fm14-screen"}))
    shown = wait_prompt(card, card.mark())
    info = info_fields(card.request("info"))
    shots["chinese"] = snapshot(card, run_dir, "prompt-chinese")
    bridge.hook(event("PostToolUse", "v", tool_use_id="t1", tool_response={}, tool_name="Bash",
                      tool_input={"command": "echo 你好 && ls -la /tmp/fm14-screen"}))
    first_result = first.result()
    mark = card.mark()
    second = bridge.hook_async(approve("v", "Bash", {"command": "echo ?? && ls -la /tmp/fm14-screen"}))
    wait_prompt(card, mark)
    shots["marks"] = snapshot(card, run_dir, "prompt-marks")
    bridge.hook(event("PostToolUse", "v", tool_use_id="t2", tool_response={}, tool_name="Bash",
                      tool_input={"command": "echo ?? && ls -la /tmp/fm14-screen"}))
    second.result()
    bridge.hook(approve("v", "Bash", {"command": "ls"}), wait_limit=0)
    bridge.settle("waiting")
    wait_until(lambda: info_fields(card.request("info"))["prompt"] == "none", 5.0, "prompt cleared")
    shots["plain"] = snapshot(card, run_dir, "waiting-plain")
    card.request("frame auto")
    log_text = bridge.log.read_text(encoding="utf-8")
    pixels = shots["chinese"]["pixels"]
    record = {"shown": shown, "info": info, "first_result": first_result,
              "shots": {k: {"png": v["png"], "sha256": v["sha256"], "meta": v["meta"]} for k, v in shots.items()},
              "tool_ink": ink(pixels, (52, 80), WAITING_BACKGROUND),
              "body_ink": ink(pixels, (80, 250), WAITING_BACKGROUND),
              "footer_ink": ink(pixels, (258, 316), WAITING_BACKGROUND)}
    write_summary(run_dir, record)
    assert shown["tool"] == "Bash" and (shown["position"], shown["count"]) == (1, 1), shown
    assert info["prompt"] == str(shown["id"]), info
    assert first_result["stdout"] == "" and first_result["exit"] == 0, first_result
    assert shots["chinese"]["sha256"] == shots["marks"]["sha256"]
    assert shots["chinese"]["sha256"] != shots["plain"]["sha256"]
    assert record["tool_ink"] > 50 and record["body_ink"] > 300 and record["footer_ink"] > 200, record
    assert "ls -la" not in log_text and "fm14-screen" not in log_text


LONG_TOOL = "mcp__claude-in-chrome__javascript_tool"
TOOL_ROWS = (52, 80)
TEXT_RIGHT = 230


@pytest.mark.parametrize("case", ["[FM-14] a long tool name is cut with an ellipsis inside the screen"])
def test_long_tool_name_stays_on_screen(case, card, bridge, run_dir):
    start = start_and_mark(bridge, card)
    card.request("frame 0")
    bridge.hook_async(approve("w", LONG_TOOL, {"command": "ls"}))
    shown = wait_prompt(card, start)
    shot = snapshot(card, run_dir, "prompt-long-tool")
    card.request("frame auto")
    pixels = shot["pixels"]
    record = {"shown": shown, "png": shot["png"], "sha256": shot["sha256"], "meta": shot["meta"],
              "margin_ink": ink(pixels, TOOL_ROWS, WAITING_BACKGROUND, (TEXT_RIGHT, W)),
              "text_ink": ink(pixels, TOOL_ROWS, WAITING_BACKGROUND, (0, TEXT_RIGHT))}
    write_summary(run_dir, record)
    assert shown["tool"] == LONG_TOOL[:31], shown
    assert record["margin_ink"] == 0, record
    assert record["text_ink"] > 50, record


@pytest.mark.parametrize("case", ["[FM-14] two waiting requests are answered one after another"])
def test_two_waiting_requests(case, card, bridge, run_dir):
    start = start_and_mark(bridge, card)
    tests = bridge.hook_async(approve("p", "Bash", {"command": "npm test"}))
    first = wait_prompt(card, start)
    write = bridge.hook_async(approve("p", "Write", {"file_path": "/tmp/fm14/out.txt", "content": "x"}))
    both = wait_prompt(card, first["index"] + 1, prompt_id=first["id"])
    bridge.settle("waiting")
    press(card, "ok", "long")
    card.wait_for(lambda line: line == f"DECISION {first['id']} allow", 5.0, both["index"])
    tests_result = tests.result()
    second = wait_prompt(card, both["index"] + 1)
    press(card, "down", "press")
    card.wait_for(lambda line: line == f"DECISION {second['id']} deny", 5.0, second["index"])
    card.wait_for(lambda line: line == "PROMPT none", 5.0, second["index"])
    write_result = write.result()
    after = bridge.settle("working")
    summary = {"prompts": card_lines(card, start, "PROMPT"), "decisions": card_lines(card, start, "DECISION"),
               "tests": tests_result, "write": write_result, "status": after}
    write_summary(run_dir, summary)
    assert (first["position"], first["count"], first["tool"]) == (1, 1, "Bash"), first
    assert (both["position"], both["count"]) == (1, 2), both
    assert (second["position"], second["count"], second["tool"]) == (1, 1, "Write"), second
    assert second["id"] != first["id"]
    assert behavior(tests_result["stdout"]) == {"behavior": "allow"}, tests_result
    assert behavior(write_result["stdout"])["behavior"] == "deny", write_result
    assert summary["prompts"][-1] == "PROMPT none", summary
    assert after["approvals"] == [] and after["sessions"]["p"]["pending"] == 0, after


@pytest.mark.parametrize("case", ["[FM-14] hook gives up at its wait limit and the card keeps waiting"])
def test_hook_gives_up(case, card, bridge, run_dir):
    start = start_and_mark(bridge, card)
    run = bridge.hook_async(approve("g", "Bash", {"command": "sleep 1"}), wait_limit=2)
    shown = wait_prompt(card, start)
    result = run.result()
    card.wait_for(lambda line: line == "PROMPT none", 5.0, shown["index"])
    status = bridge.settle("waiting")
    closed = log_kinds(bridge, "approval_closed")
    write_summary(run_dir, {"result": result, "status": status, "closed": closed})
    assert result["exit"] == 0 and result["stdout"] == "", result
    assert 1.8 <= result["s"] < 4.0, result
    assert status["approvals"] == [] and status["sessions"]["g"]["pending"] == 1, status
    assert [(c["id"], c["how"]) for c in closed] == [(shown["id"], "gave_up")], closed


HOLD_MS = 1500


@pytest.mark.parametrize("case,way", [
    ("[FM-14] a long press only approves the request shown when the key went down: request changes", "changes"),
    ("[FM-14] a long press only approves the request shown when the key went down: request appears", "appears"),
])
def test_long_press_binds_to_shown_request(case, way, card, bridge, run_dir):
    start = start_and_mark(bridge, card)
    first_tool = bash("make first")
    first = approve("h", first_tool["tool_name"], first_tool["tool_input"])
    runs = {}
    if way == "changes":
        runs["first"] = bridge.hook_async(first)
        shown_at_press = wait_prompt(card, start)
        runs["second"] = bridge.hook_async(approve("h", "Bash", {"command": "make second"}))
        wait_prompt(card, shown_at_press["index"] + 1, prompt_id=shown_at_press["id"])
    bridge.settle("waiting" if way == "changes" else "idle")
    mark = card.mark()
    press(card, "ok", f"hold {HOLD_MS}")
    pressed, _, _ = card.wait_for(lambda line: line == "KEY press ok", 2.0, mark)
    if way == "changes":
        bridge.hook(event("PostToolUse", "h", tool_use_id="t1", tool_response={}, **first_tool), wait_limit=0)
    else:
        runs["first"] = bridge.hook_async(first)
    shown_at_long = wait_prompt(card, pressed + 1, timeout=3.0)
    long_index, _, _ = card.wait_for(lambda line: line == "KEY long ok", 3.0, pressed)
    released, _, _ = card.wait_for(lambda line: line == "KEY release ok", 3.0, long_index)
    held = {"keys": card_lines(card, mark, "KEY"), "prompts": card_lines(card, start, "PROMPT"),
            "decisions": card_lines(card, mark, "DECISION"), "status": bridge.status(), "shown_at_long": shown_at_long}
    write_summary(run_dir, {"held": held})
    assert shown_at_long["index"] < long_index, held
    assert held["decisions"] == [], held
    assert [a["id"] for a in held["status"]["approvals"]] == [shown_at_long["id"]], held
    press(card, "ok", "long")
    card.wait_for(lambda line: line == f"DECISION {shown_at_long['id']} allow", 5.0, released)
    approved = runs["second" if way == "changes" else "first"].result()
    write_summary(run_dir, {"held": held, "approved": approved})
    assert behavior(approved["stdout"]) == {"behavior": "allow"}, approved


@pytest.mark.parametrize("case,way,fields,why", [
    ("[FM-14] the next prompt releases waiting hooks", "UserPromptSubmit", {}, "prompt"),
    ("[FM-14] the end of a turn releases waiting hooks", "Stop", {"stop_hook_active": False}, "stop"),
    ("[FM-14] a failed turn releases waiting hooks", "StopFailure", {"error": "rate_limit"}, "stop_failure"),
    ("[FM-14] the end of the session releases waiting hooks", "SessionEnd", {"reason": "other"}, "session_end"),
])
def test_session_events_release_waiting_hooks(case, way, fields, why, card, bridge, run_dir):
    start = start_and_mark(bridge, card)
    run = bridge.hook_async(approve("r", "Bash", {"command": "make release"}))
    shown = wait_prompt(card, start)
    bridge.hook(event(way, "r", **fields), wait_limit=0)
    result = run.result()
    card.wait_for(lambda line: line == "PROMPT none", 5.0, shown["index"])
    status = bridge.status()
    answers = log_kinds(bridge, "answer")
    closed = log_kinds(bridge, "approval_closed")
    write_summary(run_dir, {"result": result, "status": status, "answers": answers, "closed": closed})
    assert result["exit"] == 0 and result["stdout"] == "", result
    assert result["s"] < 5.0, result
    assert [(a["id"], a["decision"], a["why"]) for a in answers] == [(shown["id"], "released", why)], answers
    assert [(c["id"], c["how"]) for c in closed] == [(shown["id"], "answered")], closed
    assert status["approvals"] == [], status


def deny_until_done(card, start, limit=3):
    answered = []
    while True:
        index, _, line = card.wait_for(lambda l: l == "STATE done" or PROMPT_LINE.match(l), 120.0, start)
        if line == "STATE done":
            return answered
        prompt_id = PROMPT_LINE.match(line).group(1)
        press(card, "down", "press")
        card.wait_for(lambda l: l == f"DECISION {prompt_id} deny", 5.0, index)
        answered.append(int(prompt_id))
        assert len(answered) <= limit, answered
        start = index + 1


def collapse(states):
    out = []
    for state in states:
        if not out or out[-1] != state:
            out.append(state)
    return out


@pytest.mark.parametrize("case", ["[FM-14] approving on the card runs the command"])
def test_card_approves_real_request(case, card, bridge, run_dir):
    summary = {}
    try:
        with real_session(bridge, run_dir) as (session, marker):
            wait_session_ready(session, bridge)
            start = card.mark()
            request_touch(session, marker)
            shown = wait_prompt(card, start, 120.0)
            session.wait_screen("Do you want to proceed", 10.0)
            press(card, "ok", "long")
            card.wait_for(lambda line: line == f"DECISION {shown['id']} allow", 5.0, shown["index"])
            wait_until(marker.exists, 30.0, "marker file")
            session.wait_screen("Allowed by PermissionRequest hook", 30.0)
            card.wait_for(lambda line: line == "STATE done", 90.0, start)
            approvals = bridge.approvals()
            session.exit()
            card.wait_for(lambda line: line == "STATE idle", 20.0, start)
            summary = {"shown": shown, "states": card_states(card, start),
                       "prompts": card_lines(card, start, "PROMPT"), "approvals_after": approvals,
                       "answers": log_kinds(bridge, "answer"), "closed": log_kinds(bridge, "approval_closed")}
    finally:
        write_summary(run_dir, summary)
    assert summary["shown"]["tool"] == "Bash", summary
    assert summary["states"] == ["working", "waiting", "working", "done", "idle"], summary
    assert summary["prompts"] == [f"PROMPT {summary['shown']['id']} 1/1 Bash", "PROMPT none"], summary
    assert summary["approvals_after"] == [], summary
    assert [(a["decision"], a["why"]) for a in summary["answers"]] == [("allow", "card")], summary
    assert [c["how"] for c in summary["closed"]] == ["answered"], summary


@pytest.mark.parametrize("case", ["[FM-14] denying on the card blocks the command"])
def test_card_denies_real_request(case, card, bridge, run_dir):
    summary = {}
    try:
        with real_session(bridge, run_dir) as (session, marker):
            wait_session_ready(session, bridge)
            start = card.mark()
            request_touch(session, marker)
            wait_prompt(card, start, 120.0)
            session.wait_screen("Do you want to proceed", 10.0)
            denied = deny_until_done(card, start)
            session.wait_screen("Denied by PermissionRequest hook", 10.0)
            ran = marker.exists()
            session.exit()
            card.wait_for(lambda line: line == "STATE idle", 20.0, start)
            summary = {"denied": denied, "marker_exists": ran, "states": card_states(card, start),
                       "answers": log_kinds(bridge, "answer")}
    finally:
        write_summary(run_dir, summary)
    assert not summary["marker_exists"], summary
    assert 1 <= len(summary["denied"]) <= 3, summary
    assert collapse(summary["states"]) == ["working"] + ["waiting", "working"] * len(summary["denied"]) + \
        ["done", "idle"], summary
    assert {(a["decision"], a["why"]) for a in summary["answers"]} == {("deny", "card")}, summary


@pytest.mark.parametrize("case", ["[FM-14] answering yes in the terminal clears the card prompt"])
def test_terminal_yes_clears_prompt(case, card, bridge, run_dir):
    summary = {}
    try:
        with real_session(bridge, run_dir) as (session, marker):
            wait_session_ready(session, bridge)
            start = card.mark()
            request_touch(session, marker)
            shown = wait_prompt(card, start, 120.0)
            session.wait_screen("Do you want to proceed", 10.0)
            session.send("1", "1 (yes in terminal)")
            time.sleep(0.3)
            session.send("\r", "enter (confirm)")
            wait_until(marker.exists, 30.0, "marker file")
            card.wait_for(lambda line: line == "PROMPT none", 10.0, shown["index"])
            card.wait_for(lambda line: line == "STATE done", 90.0, start)
            approvals = bridge.approvals()
            session.exit()
            card.wait_for(lambda line: line == "STATE idle", 20.0, start)
            summary = {"shown": shown, "states": card_states(card, start), "approvals_after": approvals,
                       "decisions": card_lines(card, start, "DECISION"), "answers": log_kinds(bridge, "answer"),
                       "closed": log_kinds(bridge, "approval_closed")}
    finally:
        write_summary(run_dir, summary)
    assert summary["states"] == ["working", "waiting", "working", "done", "idle"], summary
    assert summary["decisions"] == [], summary
    assert summary["approvals_after"] == [], summary
    assert [(a["decision"], a["why"]) for a in summary["answers"]] == [("released", "post_tool_use")], summary
    assert [c["how"] for c in summary["closed"]] == ["answered"], summary


REJECT_KEYS = {"esc": ("\x1b", "esc (reject in terminal)"), "no": ("3", "3 (choose No in terminal)")}


@pytest.mark.parametrize("way,case", [
    ("esc", "[FM-14] rejecting with Esc in the terminal clears the prompt and shows idle"),
    ("no", "[FM-14] choosing No in the terminal clears the prompt and shows idle"),
])
def test_terminal_rejection(way, case, card, bridge, run_dir):
    summary = {}
    try:
        with real_session(bridge, run_dir) as (session, marker):
            wait_session_ready(session, bridge)
            start = card.mark()
            request_touch(session, marker)
            shown = wait_prompt(card, start, 120.0)
            session.wait_screen("Do you want to proceed", 10.0)
            session.send(*REJECT_KEYS[way])
            rejected_t = session.timeline[-1]["t"]
            _, cleared_t, _ = card.wait_for(lambda line: line == "PROMPT none", 5.0, shown["index"])
            card.wait_for(lambda line: line == "STATE idle", 5.0, shown["index"])
            session.wait_screen("Interrupted", 10.0)
            after_clear = card.mark()
            press(card, "ok", "long")
            time.sleep(REJECTION_HOLD_S)
            held = card_states(card, start)
            late = card_lines(card, after_clear, "DECISION")
            session.type_line("Reply with the single word done.", "enter (next prompt)")
            card.wait_for(lambda line: line == "STATE done", 90.0, start)
            session.exit()
            card.wait_for(lambda line: line == "STATE idle", 20.0, after_clear)
            summary = {"shown": shown, "clear_after_s": cleared_t - rejected_t, "held_states": held,
                       "late_decisions": late, "states": card_states(card, start), "marker_exists": marker.exists(),
                       "closed": log_kinds(bridge, "approval_closed")}
    finally:
        write_summary(run_dir, summary)
    assert not summary["marker_exists"], summary
    assert 0 <= summary["clear_after_s"] < PROMPT_CLEAR_LIMIT_S, summary
    assert summary["held_states"] == ["working", "waiting", "idle"], summary
    assert summary["late_decisions"] == [], summary
    assert summary["states"] == ["working", "waiting", "idle", "working", "done", "idle"], summary
    assert [c["how"] for c in summary["closed"]] == ["hung_up"], summary


@pytest.mark.skipif(not PERSON_SECONDS, reason="needs a person at the card; set FM_PERSON_AT_CARD_SECONDS")
@pytest.mark.parametrize("case", ["[FM-14] person approves with a long OK press and denies with the down key"])
def test_person_presses_real_keys(case, card, bridge, run_dir):
    seconds = float(PERSON_SECONDS)
    summary = {}
    try:
        with real_session(bridge, run_dir) as (session, marker):
            wait_session_ready(session, bridge)
            start = card.mark()
            request_touch(session, marker)
            first = wait_prompt(card, start, 120.0)
            (run_dir / "ready-approve.txt").write_text(f"{time.time():.3f}\n", encoding="utf-8")
            card.wait_for(lambda line: line == f"DECISION {first['id']} allow", seconds, first["index"])
            wait_until(marker.exists, 30.0, "marker file")
            card.wait_for(lambda line: line == "STATE done", 90.0, start)
            second_marker = marker.with_name("fm-marker-2.txt")
            mark = card.mark()
            request_touch(session, second_marker)
            second = wait_prompt(card, mark, 120.0)
            (run_dir / "ready-deny.txt").write_text(f"{time.time():.3f}\n", encoding="utf-8")
            card.wait_for(lambda line: line == f"DECISION {second['id']} deny", seconds, second["index"])
            card.wait_for(lambda line: line == "STATE done", 120.0, mark)
            session.exit()
            summary = {"keys": card_lines(card, start, "KEY"), "decisions": card_lines(card, start, "DECISION"),
                       "injected": card_lines(card, start, "OK key"), "first_ran": marker.exists(),
                       "second_ran": second_marker.exists()}
    finally:
        write_summary(run_dir, summary)
    assert summary["injected"] == [], summary
    assert summary["first_ran"] and not summary["second_ran"], summary
    assert "KEY long ok" in summary["keys"] and "KEY press down" in summary["keys"], summary


@pytest.mark.parametrize("case", ["[FM-14] card tasks keep stack headroom after the approval tests"])
def test_card_stack_headroom(case, card, artifacts):
    line = card.request("stacks")
    (artifacts / "card-stacks.txt").write_text(line + "\n", encoding="utf-8")
    free = {name: int(value) for name, value in (p.split("=", 1) for p in line.split()[2:])}
    assert set(free) == {"keys", "display", "console", "nimble_host"}, line
    assert all(value >= MIN_STACK_FREE_BYTES for value in free.values()), free


@pytest.mark.parametrize("case", ["[FM-14] card reboot keeps the waiting prompt and the answer still works"])
def test_reboot_keeps_prompt(case, card, bridge, run_dir):
    start = start_and_mark(bridge, card)
    run = bridge.hook_async(approve("r", "Bash", {"command": "make test"}))
    shown = wait_prompt(card, start)
    rebooted = card.mark()
    assert card.request("reboot") == "OK reboot"
    ready, _, _ = card.wait_for(lambda line: line.startswith("READY "), 20.0, rebooted)
    again = wait_prompt(card, ready, 30.0, prompt_id=shown["id"])
    restored = info_fields(card.request("info"))
    press(card, "ok", "long")
    card.wait_for(lambda line: line == f"DECISION {shown['id']} allow", 5.0, again["index"])
    result = run.result()
    write_summary(run_dir, {"shown": shown, "again": again, "restored": restored, "result": result,
                            "status": bridge.status()})
    assert (again["position"], again["count"], again["tool"]) == (1, 1, "Bash"), again
    assert restored["prompt"] == str(shown["id"]) and restored["sec"] == SECURE, restored
    assert behavior(result["stdout"]) == {"behavior": "allow"}, result
