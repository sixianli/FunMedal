import json
import os
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from bridge_harness import (APP, HOOK_TIMEOUT_S, INSTALLED_BRIDGE, SOCKET_PREFIX, HookRun,  # noqa: F401
                            approve, artifacts, bash, bridge, bridge_cli, card, card_states, event, real_session,
                            require_bridge_app, run_dir, wait_session_ready, wait_until, write_summary)

ARTIFACTS_PREFIX = "ob9"
ARTIFACTS_SUBDIR = "bridge-start"
QUIET_S = 3.0
SETTLE_S = 5.0
SPEECH_DECIDED = {"authorized", "denied", "restricted"}
SPEECH_ANSWER_S = 120.0
PERSON_RESET_SPEECH = os.environ.get("FM_PERSON_RESET_SPEECH")


def socket_path(name):
    return os.path.join(tempfile.gettempdir(), f"{SOCKET_PREFIX}{name}-{uuid.uuid4().hex[:8]}.sock")


def serving(path):
    result, _ = bridge_cli("status", "--socket", path)
    return json.loads(result.stdout) if result.returncode == 0 else None


def serve_processes(path):
    rows = subprocess.run(["ps", "-axww", "-o", "pid=,ppid=,command="], capture_output=True, text=True,
                          check=True).stdout
    found = (row.strip().split(None, 2) for row in rows.splitlines())
    return [{"pid": int(pid), "ppid": int(ppid), "command": command} for pid, ppid, command in found
            if f" serve --socket {path}" in command]


def settled_processes(path):
    deadline = time.monotonic() + SETTLE_S
    while True:
        found = serve_processes(path)
        if len(found) <= 1 or time.monotonic() > deadline:
            return found
        time.sleep(0.1)


def read_log(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def kinds(entries, kind):
    return [e for e in entries if e.get("kind") == kind]


def clean_up(path):
    bridge_cli("quit", "--socket", path)
    wait_until(lambda: serving(path) is None and serve_processes(path) == [], 10.0, "bridge exit")
    for leftover in (path, path + ".lock", str(Path(path).with_suffix(".activity.json"))):
        Path(leftover).unlink(missing_ok=True)


def session_start(session):
    return json.dumps(event("SessionStart", session, source="startup", model="haiku")).encode()


def hook_outcome(result, elapsed):
    return {"exit": result.returncode, "stdout": result.stdout.decode(), "stderr": result.stderr.decode(),
            "s": elapsed}


def other_events():
    first = bash("ls")
    return [
        ("UserPromptSubmit", event("UserPromptSubmit", "ob9-other", prompt="hello")),
        ("PreToolUse", event("PreToolUse", "ob9-other", tool_use_id="t1", **first)),
        ("PermissionRequest", approve("ob9-other", "Bash", {"command": "ls"})),
        ("PostToolUse", event("PostToolUse", "ob9-other", tool_use_id="t1", tool_response={}, **first)),
        ("PostToolUseFailure", event("PostToolUseFailure", "ob9-other", tool_use_id="t1", error="Exit code 1",
                                     **first)),
        ("Stop", event("Stop", "ob9-other", stop_hook_active=False)),
        ("StopFailure", event("StopFailure", "ob9-other", error="rate_limit")),
        ("SessionEnd", event("SessionEnd", "ob9-other", reason="prompt_input_exit")),
    ]


@pytest.mark.parametrize("case,way", [
    ("[OB-9] at session start with no socket file, the hook starts the bridge app and hands it the event",
     "missing"),
    ("[OB-9] at session start with the socket file of a bridge that died, the hook starts the bridge app and hands "
     "it the event", "stale"),
])
def test_session_start_starts_bridge(case, way, run_dir):
    require_bridge_app()
    path = socket_path(way)
    log = run_dir / "bridge.jsonl"
    if way == "stale":
        leftover = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        leftover.bind(path)
        leftover.close()
    summary = {}
    try:
        result, elapsed = bridge_cli("hook", "--socket", path, "--log", str(log), payload=session_start("ob9-start"),
                                     timeout=HOOK_TIMEOUT_S + 5)
        summary = {"hook": hook_outcome(result, elapsed), "status": serving(path),
                   "processes": settled_processes(path), "log": read_log(log)}
    finally:
        write_summary(run_dir, summary)
        clean_up(path)
    assert summary["hook"]["exit"] == 0 and summary["hook"]["stdout"] == "", summary
    assert summary["hook"]["s"] < HOOK_TIMEOUT_S, summary
    status = summary["status"]
    assert status is not None and "ob9-start" in status["sessions"], summary
    assert [(p["pid"], p["ppid"]) for p in summary["processes"]] == [(status["pid"], 1)], summary
    assert summary["processes"][0]["command"].startswith(str(APP)), summary
    assert [e["pid"] for e in kinds(summary["log"], "start")] == [status["pid"]], summary
    assert [(e["event"], e["session"]) for e in kinds(summary["log"], "event")] == [("SessionStart", "ob9-start")], \
        summary


@pytest.mark.parametrize("case", ["[OB-9] two sessions that start at once with no bridge share the one bridge they "
                                  "start, and a later session start leaves it alone"])
def test_session_starts_share_one_bridge(case, run_dir):
    require_bridge_app()
    path = socket_path("together")
    log = run_dir / "bridge.jsonl"
    args = ["hook", "--socket", path, "--log", str(log)]
    summary = {}
    try:
        runs = [HookRun(args, session_start(name)) for name in ("ob9-a", "ob9-b")]
        together = [run.result(timeout=HOOK_TIMEOUT_S + 5) for run in runs]
        first = serving(path)
        result, elapsed = bridge_cli(*args, payload=session_start("ob9-c"), timeout=HOOK_TIMEOUT_S + 5)
        summary = {"together": together, "first": first, "later": hook_outcome(result, elapsed),
                   "status": serving(path), "processes": settled_processes(path), "log": read_log(log)}
    finally:
        write_summary(run_dir, summary)
        clean_up(path)
    hooks = [*summary["together"], summary["later"]]
    assert all(h["exit"] == 0 and h["stdout"] == "" and h["s"] < HOOK_TIMEOUT_S for h in hooks), summary
    assert summary["first"] is not None and summary["status"]["pid"] == summary["first"]["pid"], summary
    assert [(p["pid"], p["ppid"]) for p in summary["processes"]] == [(summary["first"]["pid"], 1)], summary
    assert [e["pid"] for e in kinds(summary["log"], "start")] == [summary["first"]["pid"]], summary
    events = [(e["event"], e["session"]) for e in kinds(summary["log"], "event")]
    assert sorted(events[:2]) == [("SessionStart", "ob9-a"), ("SessionStart", "ob9-b")], summary
    assert events[2:] == [("SessionStart", "ob9-c")], summary


@pytest.mark.parametrize("case", ["[OB-9] every hook event other than session start still exits at once when no "
                                  "bridge is listening and starts nothing"])
def test_other_events_start_nothing(case, run_dir):
    require_bridge_app()
    path = socket_path("others")
    summary = {}
    try:
        outcomes = {}
        for name, payload in other_events():
            result, elapsed = bridge_cli("hook", "--socket", path, payload=json.dumps(payload).encode())
            outcomes[name] = hook_outcome(result, elapsed)
        time.sleep(QUIET_S)
        summary = {"hooks": outcomes, "status": serving(path), "processes": serve_processes(path),
                   "socket_file": os.path.exists(path)}
    finally:
        write_summary(run_dir, summary)
        clean_up(path)
    assert sorted(summary["hooks"]) == sorted(name for name, _ in other_events()), summary
    assert all(h["exit"] == 0 and h["stdout"] == "" and h["s"] < 1.0 for h in summary["hooks"].values()), summary
    assert summary["status"] is None and summary["processes"] == [] and not summary["socket_file"], summary


def start_app(path, log):
    subprocess.run(["open", "-n", "-g", str(APP), "--args", "serve", "--socket", path, "--log", str(log)], check=True)
    return wait_until(lambda: serving(path), 10.0, "bridge serving")


@pytest.mark.parametrize("case", ["[OB-9] the bridge records its speech access when it starts and asks macOS only "
                                  "while it is undecided"])
def test_speech_access_at_start(case, run_dir):
    require_bridge_app()
    path = socket_path("speech")
    log = run_dir / "bridge.jsonl"
    summary = {}
    try:
        status = start_app(path, log)
        recorded = wait_until(lambda: kinds(read_log(log), "speech"), 5.0, "speech access recorded")
        time.sleep(QUIET_S)
        summary = {"status": status, "first": recorded[0], "speech": kinds(read_log(log), "speech")}
    finally:
        write_summary(run_dir, summary)
        clean_up(path)
    first = summary["first"]["status"]
    assert first in SPEECH_DECIDED, (f"speech access for {APP.name} is {first}; answer the macOS dialog, "
                                     "or run this module with FM_PERSON_RESET_SPEECH set", summary)
    assert [e["status"] for e in summary["speech"]] == [first], summary
    assert summary["status"]["speech"] == first, summary


@pytest.mark.skipif(not PERSON_RESET_SPEECH,
                    reason="needs a person who has run `tccutil reset SpeechRecognition com.funmedal.bridge` and "
                           "will click Allow; set FM_PERSON_RESET_SPEECH")
@pytest.mark.parametrize("case", ["[OB-9] with speech access reset, the bridge asks macOS as soon as it starts and "
                                  "records the answer"])
def test_speech_asked_at_start(case, run_dir):
    require_bridge_app()
    path = socket_path("speech-ask")
    log = run_dir / "bridge.jsonl"
    summary = {}
    try:
        start_app(path, log)
        answered = wait_until(lambda: (found := kinds(read_log(log), "speech")) and len(found) == 2 and found,
                              SPEECH_ANSWER_S, "the person's answer to the speech dialog")
        summary = {"speech": answered, "status": serving(path)}
    finally:
        write_summary(run_dir, summary)
        clean_up(path)
    assert [e["status"] for e in summary["speech"]] == ["not_determined", "authorized"], summary
    assert summary["status"]["speech"] == "authorized", summary


@pytest.mark.parametrize("case", ["[OB-9] with no bridge running, a real session starts the installed bridge, which "
                                  "outlives the session, shows the session on the card and takes the mod's listener"])
def test_real_session_starts_bridge(case, card, bridge, run_dir):
    summary = {}
    try:
        with real_session(bridge, run_dir, start_bridge=False) as (session, _):
            wait_session_ready(session, bridge)
            started = bridge.status()
            connected = wait_until(lambda: (s := bridge.status()) and s["card"] == "connected"
                                   and s["card_state"] == s["desired"] and s, 30.0, "started bridge on the card")
            listening = wait_until(lambda: (s := bridge.status()) and s["channels"] and s, 30.0,
                                   "the mod's listener")["channels"]
            hooked = sorted(int(pid) for pid in json.loads(bridge.activity.read_text(encoding="utf-8")))
            processes = serve_processes(bridge.socket)
            start = card.mark()
            session.type_line("Reply with the single word ok.", "enter (prompt)")
            card.wait_for(lambda line: line == "STATE done", 90.0, start)
            session.exit()
            card.wait_for(lambda line: line == "STATE idle", 20.0, start)
            summary = {"started": started, "connected": connected, "listening": listening, "hooked": hooked,
                       "processes": processes, "states": card_states(card, start), "after_exit": bridge.status()}
    finally:
        write_summary(run_dir, summary)
    started = summary["started"]
    assert [(p["pid"], p["ppid"]) for p in summary["processes"]] == [(started["pid"], 1)], summary
    assert summary["processes"][0]["command"].startswith(f"{INSTALLED_BRIDGE} serve"), summary
    assert summary["listening"] == summary["hooked"] and len(summary["hooked"]) == 1, summary
    assert summary["states"] == ["working", "done", "idle"], summary
    assert summary["after_exit"] is not None and summary["after_exit"]["pid"] == started["pid"], summary
