import json
import time

import pytest

from bridge_harness import (artifacts, bash, bridge, card, card_states, event, real_session,  # noqa: F401
                            run_dir, shown_states, start_and_mark, wait_session_ready, wait_until, write_summary)

ARTIFACTS_PREFIX = "sc20"
ARTIFACTS_SUBDIR = "agents"
CHECK_SECONDS = "1"
WRAP_UP = "mcp__ccd_turn__wrap_up"
LONG_COMMAND_SECONDS = 40
LONG_COMMAND = "sh long-step.sh"
SLOW_SECONDS = 1.5
FAKE_CLAUDE = """#!/bin/sh
printf '%s\\n' "$*" >> "{calls}"
if [ "$*" != "agents --json" ]; then exit 1; fi
if [ -s "{queue}" ]; then
  head -n 1 "{queue}" > "{answer}.next"
  tail -n +2 "{queue}" > "{queue}.rest"
  mv "{queue}.rest" "{queue}"
  mv "{answer}.next" "{answer}"
fi
if [ -f "{fail}" ]; then exit 3; fi
case "$(head -c 5 "{answer}")" in
  FAIL*) exit 3;;
  SLOW*) sleep {slow}; tail -c +6 "{answer}"; exit 0;;
esac
cat "{answer}"
"""


class FakeClaude:
    def __init__(self, folder):
        self.program = folder / "claude"
        self.calls = folder / "calls.txt"
        self.answer = folder / "answer.json"
        self.fail = folder / "fail"
        self.queue = folder / "queue.txt"
        self.program.write_text(FAKE_CLAUDE.format(calls=self.calls, answer=self.answer, fail=self.fail,
                                                   queue=self.queue, slow=SLOW_SECONDS), encoding="utf-8")
        self.program.chmod(0o755)
        self.says()

    def says(self, text=None, **statuses):
        if text is None:
            text = statuses_text(statuses)
        self.answer.write_text(text, encoding="utf-8")
        return self.count()

    def plays(self, *answers):
        lines = []
        for answer in answers:
            if answer == "FAIL":
                lines.append("FAIL")
            elif isinstance(answer, tuple) and answer[0] == "SLOW":
                lines.append("SLOW " + statuses_text(answer[1]))
            else:
                lines.append(statuses_text(answer))
        self.queue.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        return self.count()

    def count(self):
        return len(self.calls.read_text(encoding="utf-8").splitlines()) if self.calls.exists() else 0

    def wait_calls(self, after, more):
        return wait_until(lambda: (n := self.count()) >= after + more and n, 10.0, f"{more} more agents checks")

    def args(self):
        return self.calls.read_text(encoding="utf-8").splitlines() if self.calls.exists() else []


def statuses_text(statuses):
    return json.dumps([session_entry(session, status) for session, status in statuses.items()])


def session_entry(session, status):
    entry = {"pid": 4242, "cwd": "/tmp/fm13-project", "kind": "interactive", "startedAt": 1792830252587,
             "sessionId": session, "status": status}
    if status == "waiting":
        entry["waitingFor"] = "permission prompt"
    return entry


@pytest.fixture
def fake(run_dir):
    return FakeClaude(run_dir)


def start(bridge, card, fake, seconds=CHECK_SECONDS):
    return start_and_mark(bridge, card, "--claude", str(fake.program), "--agents-seconds", seconds)


def hold_still(bridge, fake, expected, checks):
    after = fake.count()
    while fake.count() < after + checks:
        status = bridge.status()
        assert shown_states(status) == expected, status
        time.sleep(0.2)
    status = bridge.status()
    assert shown_states(status) == expected, status
    return status


def written_rows(bridge):
    return [frame["rows"] for frame in bridge.frames()]


@pytest.mark.parametrize("case", ["[SC-20] a session the check sees waiting shows waiting with an alert, and two "
                                  "busy or two idle answers in a row take the stale waiting away"])
def test_check_sees_waiting_then_busy_then_idle(case, card, bridge, run_dir, fake):
    begin = start(bridge, card, fake)
    bridge.hook(event("UserPromptSubmit", "s", prompt="x"))
    bridge.settle(["running"])
    fake.says(s="waiting")
    waiting = bridge.settle(["waiting"], timeout=5.0)
    fresh = [row["fresh"] for rows in written_rows(bridge) for row in rows if row["state"] == "waiting"]
    fake.says(s="busy")
    running = bridge.settle(["running"], timeout=6.0)
    fake.says(s="idle")
    done = bridge.settle(["done"], timeout=6.0)
    card.wait_for(lambda line: line == "STATE done", 5.0, begin)
    write_summary(run_dir, {"waiting": waiting, "running": running, "done": done, "fresh": fresh,
                            "states": card_states(card, begin), "calls": fake.args()})
    assert card_states(card, begin) == ["working", "waiting", "working", "done"]
    assert fresh and fresh[0] is True, fresh
    assert set(fake.args()) == {"agents --json"}, fake.args()
    assert done["agents_check"] == "ok", done


@pytest.mark.parametrize("case", ["[SC-20] one busy answer keeps a stale waiting row waiting, and two idle answers "
                                  "in a row take it straight to done"])
def test_single_busy_keeps_waiting_and_two_idle_clear_it(case, card, bridge, run_dir, fake):
    begin = start(bridge, card, fake)
    bridge.hook(event("UserPromptSubmit", "s", prompt="x"))
    bridge.settle(["running"])
    after = fake.plays({"s": "waiting"}, {"s": "busy"}, {"s": "idle"}, {"s": "idle"})
    done = bridge.settle(["done"], timeout=10.0)
    card.wait_for(lambda line: line == "STATE done", 5.0, begin)
    write_summary(run_dir, {"done": done, "states": card_states(card, begin), "calls": fake.count() - after})
    assert card_states(card, begin) == ["working", "waiting", "done"]


@pytest.mark.parametrize("case", ["[SC-20] a failed check between two idle answers starts the count again, so the "
                                  "session keeps showing running"])
def test_failed_check_resets_count(case, card, bridge, run_dir, fake):
    begin = start(bridge, card, fake)
    bridge.hook(event("UserPromptSubmit", "s", prompt="x"))
    bridge.settle(["running"])
    fake.says(s="busy")
    after = fake.plays({"s": "idle"}, "FAIL", {"s": "idle"}, {"s": "busy"})
    fake.wait_calls(after, 6)
    status = bridge.status()
    write_summary(run_dir, {"status": status, "states": card_states(card, begin), "calls": fake.args()})
    assert shown_states(status) == ["running"], status
    assert card_states(card, begin) == ["working"]


@pytest.mark.parametrize("case", ["[SC-20] an answer to a check that began before the session's latest hook event "
                                  "does not count"])
def test_check_older_than_event_does_not_count(case, card, bridge, run_dir, fake):
    begin = start(bridge, card, fake)
    bridge.hook(event("UserPromptSubmit", "s", prompt="x"))
    bridge.settle(["running"])
    fake.says(s="busy")
    after = fake.plays(("SLOW", {"s": "idle"}), {"s": "idle"}, {"s": "busy"})
    fake.wait_calls(after, 1)
    sent = time.monotonic()
    bridge.hook(event("PostToolUse", "s", tool_use_id="t1", tool_response={}, **bash("ls")))
    took = time.monotonic() - sent
    fake.wait_calls(after, 5)
    status = bridge.status()
    entries = [e for e in bridge.log_entries() if e.get("kind") == "agents"]
    write_summary(run_dir, {"took": took, "status": status, "states": card_states(card, begin), "checks": entries})
    assert took < SLOW_SECONDS / 2, took
    assert shown_states(status) == ["running"], status
    assert card_states(card, begin) == ["working"]


@pytest.mark.parametrize("case", ["[SC-20] a busy session, or one that is idle only once between busy answers, "
                                  "keeps showing running"])
def test_busy_and_single_idle_keep_running(case, card, bridge, run_dir, fake):
    begin = start(bridge, card, fake)
    bridge.hook(event("UserPromptSubmit", "s", prompt="x"))
    bridge.settle(["running"])
    fake.says(s="busy")
    hold_still(bridge, fake, ["running"], 4)
    flips = []
    for status in ["idle", "busy", "idle", "busy", "idle", "busy"]:
        after = fake.says(s=status)
        fake.wait_calls(after, 1)
        flips.append(shown_states(bridge.status()))
    write_summary(run_dir, {"flips": flips, "states": card_states(card, begin), "status": bridge.status()})
    assert all(rows == ["running"] for rows in flips), flips
    assert card_states(card, begin) == ["working"]


@pytest.mark.parametrize("case", ["[SC-20] a hook event that arrives during a check makes that check not count, "
                                  "so a session still sending events stays running"])
def test_events_reset_idle_answers(case, card, bridge, run_dir, fake):
    begin = start(bridge, card, fake, seconds="2")
    bridge.hook(event("UserPromptSubmit", "s", prompt="x"))
    bridge.settle(["running"])
    fake.says(s="idle")
    seen = []
    for _ in range(5):
        after = fake.count()
        bridge.hook(event("PostToolUse", "s", tool_use_id="t1", tool_response={}, **bash("ls")))
        fake.wait_calls(after, 1)
        seen.append(shown_states(bridge.status()))
    done = bridge.settle(["done"], timeout=6.0)
    card.wait_for(lambda line: line == "STATE done", 5.0, begin)
    write_summary(run_dir, {"seen": seen, "done": done, "states": card_states(card, begin)})
    assert all(rows == ["running"] for rows in seen), seen
    assert card_states(card, begin) == ["working", "done"]


@pytest.mark.parametrize("case", ["[SC-20] a permission request whose hook is still waiting and a next-step box "
                                  "question both stay waiting whatever the check says"])
def test_live_waits_survive_check(case, card, bridge, run_dir, fake):
    begin = start(bridge, card, fake)
    bridge.hook(event("UserPromptSubmit", "p", prompt="x"))
    bridge.hook(event("UserPromptSubmit", "q", prompt="x"))
    bridge.settle(["running", "running"])
    run = bridge.hook_async(event("PermissionRequest", "p", **bash("rm -rf build")))
    asked = {"tool_name": WRAP_UP, "tool_input": {"recap": "r", "ask": "要提交吗？"}}
    bridge.hook(event("PostToolUse", "q", tool_use_id="w1", tool_response={}, **asked), wait_limit=0)
    bridge.hook(event("Stop", "q", stop_hook_active=False))
    bridge.settle(["waiting", "waiting"])
    fake.says(p="busy", q="busy")
    busy = hold_still(bridge, fake, ["waiting", "waiting"], 3)
    fake.says(p="idle", q="idle")
    idle = hold_still(bridge, fake, ["waiting", "waiting"], 3)
    fake.says(p="busy", q="idle")
    bridge.hook(event("PostToolUse", "p", tool_use_id="t1", tool_response={}, **bash("rm -rf build")), wait_limit=0)
    result = run.result()
    after = bridge.settle(["waiting", "running"])
    write_summary(run_dir, {"busy": busy, "idle": idle, "after": after, "result": result,
                            "states": card_states(card, begin)})
    assert result["exit"] == 0, result


@pytest.mark.parametrize("case", ["[SC-20] a session missing from the answer, a failing check and an unreadable "
                                  "answer change nothing and show in the status, and no check runs with no "
                                  "sessions"])
def test_check_failures_change_nothing(case, card, bridge, run_dir, fake):
    begin = start(bridge, card, fake)
    time.sleep(3.0)
    idle_calls = fake.count()
    bridge.hook(event("UserPromptSubmit", "s", prompt="x"))
    bridge.settle(["running"])
    answers = {}
    fake.says(other="idle")
    hold_still(bridge, fake, ["running"], 3)
    answers["missing"] = bridge.status()["agents_check"]
    fake.fail.write_text("", encoding="utf-8")
    hold_still(bridge, fake, ["running"], 3)
    answers["failing"] = bridge.status()["agents_check"]
    fake.fail.unlink()
    for name, text in [("garbage", "not json"), ("object", '{"sessions": []}'), ("no-status", '[{"sessionId": "s"}]')]:
        fake.says(text)
        hold_still(bridge, fake, ["running"], 3)
        answers[name] = bridge.status()["agents_check"]
    write_summary(run_dir, {"idle_calls": idle_calls, "answers": answers, "states": card_states(card, begin)})
    assert idle_calls == 0, idle_calls
    assert answers == {"missing": "ok", "failing": "failed", "garbage": "unreadable", "object": "unreadable",
                       "no-status": "ok"}, answers
    assert card_states(card, begin) == ["working"]


@pytest.mark.parametrize("case", ["[SC-20] the bridge finds the real claude and reads its answer, and a real "
                                  "session running a long command keeps showing running"])
def test_real_claude_long_command(case, card, bridge, run_dir):
    settings = {"permissions": {"allow": [f"Bash({LONG_COMMAND})"]}}
    summary = {}
    try:
        with real_session(bridge, run_dir, settings, bridge_args=("--agents-seconds", "2")) as (session, marker):
            (marker.parent / "long-step.sh").write_text(f"sleep {LONG_COMMAND_SECONDS}\n", encoding="utf-8")
            wait_session_ready(session, bridge)
            start = card.mark()
            session.type_line(f"Run exactly this one shell command with the Bash tool in the foreground and nothing "
                              f"else: {LONG_COMMAND}  Then reply with the single word done.", "enter (prompt)")
            card.wait_for(lambda line: line == "STATE done", LONG_COMMAND_SECONDS + 90.0, start)
            entries = bridge.log_entries()
            pre = [e["t"] for e in entries if e.get("event") == "PreToolUse" and e.get("tool_name") == "Bash"]
            post = [e["t"] for e in entries if e.get("event") == "PostToolUse" and e.get("tool_name") == "Bash"]
            checks = [e for e in entries if e.get("kind") == "agents"]
            during = [e for e in checks if pre and post and pre[0] < e["t"] < post[0]]
            summary = {"states": card_states(card, start), "pre": pre, "post": post, "checks": checks,
                       "during": during, "status": bridge.status()}
    finally:
        write_summary(run_dir, summary)
    assert summary["states"] == ["working", "done"], summary
    assert len(summary["pre"]) == 1 and len(summary["post"]) == 1, summary
    assert summary["post"][0] - summary["pre"][0] >= LONG_COMMAND_SECONDS - 1, summary
    assert all(e["result"] == "ok" and set(e["seen"].values()) <= {"busy"} for e in summary["during"]), summary
    assert sum(1 for e in summary["during"] if e["seen"]) >= 10, summary
