import errno
import json
import os
import re
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from bridge_harness import (BRIDGE, artifacts, bridge, bridge_cli, card, ink, newest_claude, press,  # noqa: F401
                            real_session, run_dir, snapshot, wait_session_ready, wait_until, write_summary)
from test_voice import (HEADER_ROWS, HINT_ROWS, IMAGE_COLUMNS, IMAGE_ROWS, RESULT_SHOW_S, RESULT_WAIT_S, SPEAKING_VOICE,
                        TITLE_ROWS, VOICE_BACKGROUND, FakeListener, built_in_speaker, wait_voice_screen, wake_speaker)

ARTIFACTS_PREFIX = "ob8"
ARTIFACTS_SUBDIR = "voice-mod"
SPOKEN = "你好，这是一条从卡片上说的测试消息，请你只回复收到两个字"
HOLD_MS = 10000
SECOND_HINT_ROWS = (196, 226)
VOICE_START = re.compile(r"VOICE start (\d+)$")
VOICE_RESULT = re.compile(r"VOICE result (\d+) (\w+)$")
STOPPING_VERSION = (2, 1, 299)
BUSY_PIPE = "busy.fifo"
BUSY_COMMAND = f"cat {BUSY_PIPE}"
PLUGIN_ORIGIN = {"kind": "plugin", "name": "funmedal", "asUser": True}
ESCAPE = "\x1b"
USAGE_EXIT = 64
FOLLOW_UP_S = 5.0
SPEAK_TRIES = 3
LISTEN_PARENT = """
import subprocess, sys, time
child = subprocess.Popen([sys.argv[1], "listen", "--socket", sys.argv[2]])
print(child.pid, flush=True)
time.sleep(600)
"""


def clip_result(clip):
    return lambda line: (found := VOICE_RESULT.match(line)) is not None and int(found.group(1)) == clip


def next_result(card, clip, after, timeout):
    index, t, line = card.wait_for(clip_result(clip), timeout, after)
    return {"index": index, "t": t, "result": VOICE_RESULT.match(line).group(2)}


def results_since(card, start, clip):
    return [VOICE_RESULT.match(line).group(2) for _, line in card.since(start) if clip_result(clip)(line)]


def speak_once(card):
    device = built_in_speaker()
    wake_speaker(device)
    start = card.mark()
    press(card, "up", f"hold {HOLD_MS}")
    began_index, _, began = card.wait_for(lambda line: VOICE_START.match(line) is not None, 3.0, start)
    clip = int(VOICE_START.match(began).group(1))
    speaker = subprocess.Popen(["say", "-a", device, "-v", SPEAKING_VOICE, SPOKEN])
    try:
        ended_index, ended_t, _ = card.wait_for(lambda line: line.startswith(f"VOICE end {clip} "),
                                                HOLD_MS / 1000 + 3.0, began_index)
        finished_in_time = speaker.poll() is not None
    finally:
        if speaker.poll() is None:
            speaker.kill()
        speaker.wait()
    first = next_result(card, clip, ended_index, RESULT_WAIT_S + 3.0)
    return {"clip": clip, "start": start, "ended_t": ended_t, "spoke_before_release": finished_in_time,
            "first": first["result"], "first_index": first["index"], "first_t": first["t"]}


def speak(card):
    unheard = []
    while True:
        said = speak_once(card)
        if said["first"] != "empty" or len(unheard) + 1 == SPEAK_TRIES:
            return {**said, "unheard_tries": unheard}
        unheard.append({k: said[k] for k in ("clip", "spoke_before_release")})
        wait_voice_screen(card, "none", RESULT_SHOW_S + 3.0)


def follow_up(card, said, timeout):
    return next_result(card, said["clip"], said["first_index"] + 1, timeout)


def screen(card, run_dir, name):
    pixels = (shot := snapshot(card, run_dir, name))["pixels"]
    return {"png": shot["png"], "header_ink": ink(pixels, HEADER_ROWS, VOICE_BACKGROUND),
            "title_ink": ink(pixels, TITLE_ROWS, VOICE_BACKGROUND), "hint_ink": ink(pixels, HINT_ROWS, VOICE_BACKGROUND),
            "second_hint_ink": ink(pixels, SECOND_HINT_ROWS, VOICE_BACKGROUND),
            "image_ink": ink(pixels, IMAGE_ROWS, VOICE_BACKGROUND, IMAGE_COLUMNS)}


def log_kind(bridge, kind):
    return [e for e in bridge.log_entries() if e.get("kind") == kind]


def followed(bridge):
    return [(e["clip"], e["result"]) for e in log_kind(bridge, "voice_followed")]


def wait_listener(bridge):
    return wait_until(lambda: (s := bridge.status()) and s["channels"] and s, 30.0, "the mod's listener")


def hooked_processes(bridge):
    return sorted(int(pid) for pid in json.loads(bridge.activity.read_text(encoding="utf-8")))


def listener_processes(parent):
    rows = subprocess.run(["ps", "-axww", "-o", "pid=,ppid=,command="], capture_output=True, text=True,
                          check=True).stdout
    return [int(pid) for pid, ppid, command in (row.strip().split(None, 2) for row in rows.splitlines())
            if int(ppid) == parent and command.endswith("funmedal-bridge listen")]


def process_gone(pid):
    return subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode != 0


def transcript_prompts(session_id):
    paths = sorted((Path.home() / ".claude" / "projects").glob(f"*/{session_id}.jsonl"))
    entries = [json.loads(line) for path in paths for line in path.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    prompts = []
    for entry in entries:
        if entry.get("type") != "user" or entry.get("origin") != PLUGIN_ORIGIN:
            continue
        content = entry["message"]["content"]
        prompts.append(content if isinstance(content, str)
                       else "".join(part.get("text", "") for part in content if isinstance(part, dict)))
    return {"files": [str(p) for p in paths], "plugin_prompts": prompts}


@contextmanager
def busy_pipe(workdir):
    pipe = workdir / BUSY_PIPE
    os.mkfifo(pipe)
    try:
        yield pipe
    finally:
        try:
            os.close(os.open(pipe, os.O_WRONLY | os.O_NONBLOCK))
        except OSError as error:
            if error.errno != errno.ENXIO:
                raise


def make_busy(session, bridge):
    since = time.time()
    session.type_line(f"Use the Bash tool to run exactly this command and nothing else: {BUSY_COMMAND}  "
                      "Then reply with the single word done.", "enter (busy)")
    wait_until(lambda: any(e.get("kind") == "event" and e.get("event") == "PreToolUse" and e.get("tool_name") == "Bash"
                           and e["t"] > since for e in bridge.log_entries()), 60.0, "Claude running the busy command")
    time.sleep(1.0)
    return bridge.status()["approvals"]


def wait_done(card, start):
    return card.wait_for(lambda line: line == "STATE done", 90.0, start)


def said(bridge, *args):
    result, _ = bridge_cli("said", "--socket", bridge.socket, *args)
    return {"args": list(args), "exit": result.returncode, "stdout": result.stdout.decode(),
            "stderr": result.stderr.decode()}


def chars_of(bridge, clip):
    return next(e.get("chars") for e in log_kind(bridge, "voice_result") if e["clip"] == clip)


class ListenParent:
    def __init__(self, socket_path):
        self.proc = subprocess.Popen([sys.executable, "-c", LISTEN_PARENT, str(BRIDGE), socket_path],
                                     stdout=subprocess.PIPE, text=True)
        self.listener = int(self.proc.stdout.readline())

    def kill(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGKILL)
        self.proc.wait()

    def close(self):
        self.kill()
        if not process_gone(self.listener):
            os.kill(self.listener, signal.SIGKILL)


@pytest.mark.parametrize("case", ["[OB-8] a sentence said on the card enters an idle session through the plugin's mod "
                                  "as the person's words, the card shows queued then submitted, and a sentence a "
                                  "settings hook blocks shows as not taken"])
def test_mod_delivers(case, card, bridge, run_dir):
    block = run_dir / "block-prompts"
    blocker = run_dir / "block.sh"
    blocker.write_text(f'if [ -e "{block}" ]; then echo "the test blocks prompts now" >&2; exit 2; fi\nexit 0\n',
                       encoding="utf-8")
    settings = {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": f'/bin/sh "{blocker}"'}]}]}}
    summary = {}
    try:
        with real_session(bridge, run_dir, settings_extra=settings) as (session, _):
            wait_session_ready(session, bridge)
            listening = wait_listener(bridge)["channels"]
            hooked = hooked_processes(bridge)
            entered = speak(card)
            entered_follow = follow_up(card, entered, 30.0)
            entered_screen = screen(card, run_dir, "submitted")
            wait_done(card, entered["start"])
            again = said(bridge, "--clip", str(entered["clip"]), "--result", "submitted")
            block.write_text("", encoding="utf-8")
            blocked = speak(card)
            blocked_follow = follow_up(card, blocked, 30.0)
            blocked_screen = screen(card, run_dir, "refused")
            session_id = next(iter(bridge.status()["sessions"]))
            summary = {"argv": session.argv, "listening": listening, "hooked": hooked,
                       "listener_processes": listener_processes(hooked[0]),
                       "entered": entered, "entered_follow": entered_follow, "entered_screen": entered_screen,
                       "said_again": again, "blocked": blocked, "blocked_follow": blocked_follow,
                       "blocked_screen": blocked_screen, "voice_results": log_kind(bridge, "voice_result"),
                       "followed": followed(bridge), "transcript": transcript_prompts(session_id)}
    finally:
        write_summary(run_dir, summary)
    assert summary["listening"] == summary["hooked"] and len(summary["hooked"]) == 1, summary
    assert len(summary["listener_processes"]) == 1, summary
    assert (summary["entered"]["first"], summary["entered_follow"]["result"]) == ("queued", "submitted"), summary
    assert summary["entered_follow"]["t"] - summary["entered"]["first_t"] < 5.0, summary
    retried = [t["clip"] for said in (summary["entered"], summary["blocked"]) for t in said["unheard_tries"]]
    assert [r["result"] for r in summary["voice_results"] if r["clip"] in retried] == ["empty"] * len(retried), summary
    assert [(r["clip"], r["result"], r.get("pid")) for r in summary["voice_results"] if r["clip"] not in retried] == [
        (summary["entered"]["clip"], "queued", summary["hooked"][0]),
        (summary["blocked"]["clip"], "queued", summary["hooked"][0])], summary
    assert summary["followed"] == [(summary["entered"]["clip"], "submitted"),
                                   (summary["blocked"]["clip"], "refused")], summary
    assert summary["said_again"]["exit"] == 1 and "not waiting" in summary["said_again"]["stdout"], summary
    assert (summary["blocked"]["first"], summary["blocked_follow"]["result"]) == ("queued", "refused"), summary
    prompts = summary["transcript"]["plugin_prompts"]
    assert len(prompts) == 1 and len(prompts[0]) == chars_of(bridge, summary["entered"]["clip"]) > 0, summary
    shown = summary["entered_screen"]
    assert shown["header_ink"] > 0 and shown["image_ink"] > 0, summary
    refused = summary["blocked_screen"]
    assert refused["header_ink"] == 0 and refused["title_ink"] > 200 and refused["hint_ink"] > 100, summary


@pytest.mark.parametrize("case", ["[OB-8] while Claude works the card's sentences wait in the queue, Stop clears them "
                                  "and the card says so, after Stop the card's words are turned away until the person "
                                  "types, and when the Claude Code process ends the card says not connected"])
def test_mod_through_stop_and_exit(case, card, bridge, run_dir):
    program, version = newest_claude()
    assert version >= STOPPING_VERSION, f"needs Claude Code {STOPPING_VERSION} or newer; newest found {version}"
    settings = {"permissions": {"allow": [f"Bash({BUSY_COMMAND})"]}}
    summary = {"program": program, "version": version}
    try:
        with real_session(bridge, run_dir, settings_extra=settings, program=program) as (session, marker), \
                busy_pipe(marker.parent):
            wait_session_ready(session, bridge)
            hooked = wait_listener(bridge)["channels"]
            listener = listener_processes(hooked[0])
            busy_approvals = make_busy(session, bridge)
            older = speak(card)
            queued_screen = screen(card, run_dir, "queued")
            newer = speak(card)
            before_stop = {"older": results_since(card, older["start"], older["clip"]),
                           "newer": results_since(card, newer["start"], newer["clip"])}
            stop_mark = card.mark()
            session.send(ESCAPE, "escape (stop)")
            newer_follow = follow_up(card, newer, 20.0)
            cleared_screen = screen(card, run_dir, "cleared")
            wait_until(lambda: len(followed(bridge)) >= 2, 10.0, "both cleared sentences reported")
            older_after_stop = results_since(card, stop_mark, older["clip"])
            turned_away = speak(card)
            turned_away_follow = follow_up(card, turned_away, 20.0)
            stopped_screen = screen(card, run_dir, "stopped")
            typed_mark = card.mark()
            session.type_line("请只回复：好", "enter (person types)")
            wait_done(card, typed_mark)
            recovered = speak(card)
            recovered_follow = follow_up(card, recovered, 30.0)
            wait_done(card, recovered["start"])
            make_busy(session, bridge)
            orphaned = speak(card)
            os.kill(session.proc.pid, signal.SIGKILL)
            orphaned_follow = follow_up(card, orphaned, 15.0)
            gone_screen = screen(card, run_dir, "not-connected")
            channels_after = wait_until(lambda: (s := bridge.status()) and not s["channels"] and s, 10.0,
                                        "listener gone")["channels"]
            listener_gone = [process_gone(pid) for pid in listener]
            unheard = speak(card)
            session_id = next(iter(bridge.status()["sessions"]))
            summary.update({"argv": session.argv, "hooked": hooked, "listener": listener,
                            "busy_approvals": busy_approvals, "older": older, "newer": newer,
                            "queued_screen": queued_screen, "before_stop": before_stop, "newer_follow": newer_follow,
                            "cleared_screen": cleared_screen, "older_after_stop": older_after_stop,
                            "turned_away": turned_away, "turned_away_follow": turned_away_follow,
                            "stopped_screen": stopped_screen, "recovered": recovered,
                            "recovered_follow": recovered_follow, "orphaned": orphaned,
                            "orphaned_follow": orphaned_follow, "gone_screen": gone_screen,
                            "channels_after": channels_after, "listener_gone": listener_gone, "unheard": unheard,
                            "followed": followed(bridge), "voice_results": log_kind(bridge, "voice_result"),
                            "transcript": transcript_prompts(session_id)})
    finally:
        write_summary(run_dir, summary)
    assert summary["busy_approvals"] == [], summary
    assert len(summary["listener"]) == 1, summary
    assert summary["older"]["first"] == summary["newer"]["first"] == "queued", summary
    assert summary["before_stop"] == {"older": ["queued"], "newer": ["queued"]}, summary
    assert summary["queued_screen"]["header_ink"] > 0 and summary["queued_screen"]["image_ink"] > 0, summary
    assert summary["newer_follow"]["result"] == "cleared", summary
    assert summary["older_after_stop"] == [], summary
    clips = {name: summary[name]["clip"] for name in ("older", "newer", "turned_away", "recovered", "orphaned")}
    assert sorted(summary["followed"][:2]) == sorted([(clips["older"], "cleared"), (clips["newer"], "cleared")]), summary
    assert summary["followed"][2:] == [(clips["turned_away"], "stopped"), (clips["recovered"], "submitted"),
                                       (clips["orphaned"], "no_session")], summary
    assert (summary["turned_away"]["first"], summary["turned_away_follow"]["result"]) == ("queued", "stopped"), summary
    assert (summary["recovered"]["first"], summary["recovered_follow"]["result"]) == ("queued", "submitted"), summary
    assert (summary["orphaned"]["first"], summary["orphaned_follow"]["result"]) == ("queued", "no_session"), summary
    assert summary["channels_after"] == [] and summary["listener_gone"] == [True], summary
    assert summary["unheard"]["first"] == "no_session", summary
    for name in ("cleared_screen", "stopped_screen", "gone_screen"):
        shown = summary[name]
        assert shown["header_ink"] == 0 and shown["title_ink"] > 200, (name, shown)
        assert shown["hint_ink"] > 100 and shown["second_hint_ink"] > 100, (name, shown)
    prompts = summary["transcript"]["plugin_prompts"]
    assert len(prompts) == 1 and len(prompts[0]) == chars_of(bridge, clips["recovered"]) > 0, summary


@pytest.mark.parametrize("case", ["[OB-16] a listener that registers without promising results is still waited "
                                  "on, so the card gets a not-connected follow-up when it goes away before reporting"])
def test_every_listener_is_waited_on(case, card, bridge, run_dir):
    bridge.start()
    listener = FakeListener(bridge.socket, 1111)
    try:
        heard = speak(card)
        waiting_open = bridge.status()["waiting"]
        said_lines = listener.collect()
        later = card.mark()
    finally:
        listener.close()
    clip = heard["clip"]
    summary = {"heard": heard, "waiting_open": waiting_open, "said_clips": [m["clip"] for m in said_lines]}
    try:
        wait_until(lambda: log_kind(bridge, "unlisten"), 5.0, "listener unlistened")
        summary["follow_up"] = next_result(card, clip, later, FOLLOW_UP_S)["result"]
    finally:
        summary |= {"waiting_closed": bridge.status()["waiting"], "followed": followed(bridge),
                    "card_after_close": results_since(card, later, clip)}
        write_summary(run_dir, summary)
    assert heard["first"] == "queued" and summary["said_clips"] == [clip], summary
    assert summary["waiting_open"] == [clip] and summary["waiting_closed"] == [], summary
    assert summary["followed"] == [(clip, "no_session")] and summary["card_after_close"] == ["no_session"], summary


@pytest.mark.parametrize("case", ["[OB-8] the listen mode registers under its parent process and exits when that "
                                  "parent ends"])
def test_listen_follows_parent(case, card, bridge, run_dir):
    bridge.start()
    parent = ListenParent(bridge.socket)
    summary = {}
    try:
        registered = wait_until(lambda: (s := bridge.status()) and s["channels"] and s, 10.0, "listener")["channels"]
        parent.kill()
        killed = time.monotonic()
        wait_until(lambda: process_gone(parent.listener), 10.0, "listener exit")
        exit_s = round(time.monotonic() - killed, 3)
        after = wait_until(lambda: (s := bridge.status()) and not s["channels"] and s, 5.0, "listener unregistered")
        summary = {"parent": parent.proc.pid, "listener": parent.listener, "registered": registered,
                   "exit_after_parent_s": exit_s, "channels_after": after["channels"]}
    finally:
        parent.close()
        write_summary(run_dir, summary)
    assert summary["registered"] == [summary["parent"]], summary
    assert summary["exit_after_parent_s"] < 5.0 and summary["channels_after"] == [], summary


@pytest.mark.parametrize("case", ["[OB-8] the listen mode waits for a bridge that is not running yet and connects "
                                  "again after the bridge restarts"])
def test_listen_reconnects(case, card, bridge, run_dir):
    parent = ListenParent(bridge.socket)
    summary = {}
    try:
        time.sleep(3.0)
        alive_before_bridge = not process_gone(parent.listener)
        bridge.start()
        first = wait_until(lambda: (s := bridge.status()) and s["channels"] and s, 10.0, "first connection")
        bridge.quit()
        time.sleep(3.0)
        alive_without_bridge = not process_gone(parent.listener)
        bridge.start()
        second = wait_until(lambda: (s := bridge.status()) and s["channels"] and s, 10.0, "second connection")
        summary = {"parent": parent.proc.pid, "alive_before_bridge": alive_before_bridge,
                   "first": first["channels"], "first_bridge": first["pid"],
                   "alive_without_bridge": alive_without_bridge, "second": second["channels"],
                   "second_bridge": second["pid"]}
    finally:
        parent.close()
        write_summary(run_dir, summary)
    assert summary["alive_before_bridge"] and summary["alive_without_bridge"], summary
    assert summary["first"] == summary["second"] == [summary["parent"]], summary
    assert summary["first_bridge"] != summary["second_bridge"], summary


@pytest.mark.parametrize("case", ["[OB-8] said turns down a clip nobody waits on and arguments it does not take"])
def test_said_refuses(case, card, bridge, run_dir):
    without_bridge = said(bridge, "--clip", "7", "--result", "submitted")
    bridge.start()
    runs = {"unknown clip": said(bridge, "--clip", "7", "--result", "submitted"),
            "unknown result": said(bridge, "--clip", "7", "--result", "sent"),
            "clip above 255": said(bridge, "--clip", "256", "--result", "submitted"),
            "clip not a number": said(bridge, "--clip", "seven", "--result", "submitted"),
            "no clip": said(bridge, "--result", "submitted"),
            "no result": said(bridge, "--clip", "7")}
    summary = {"without_bridge": without_bridge, "runs": runs, "status": bridge.status()}
    write_summary(run_dir, summary)
    assert without_bridge["exit"] == 1 and "no bridge is running" in without_bridge["stderr"], summary
    assert runs["unknown clip"]["exit"] == 1 and "not waiting" in runs["unknown clip"]["stdout"], summary
    assert {name: run["exit"] for name, run in runs.items() if name != "unknown clip"} == {
        name: USAGE_EXIT for name in runs if name != "unknown clip"}, summary
    assert summary["status"]["waiting"] == [], summary
