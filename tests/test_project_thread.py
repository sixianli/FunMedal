import json
import os
import time

import pytest

from bridge_harness import (Bridge, artifacts, bridge, card, card_rows, card_states, event, hook_env,  # noqa: F401
                            real_session, require_bridge_app, run_dir, wait_session_ready, write_summary)
from host_render import compare_screen, scene_of, screen_tools  # noqa: F401
from test_rows import capture_hook_messages

ARTIFACTS_PREFIX = "sc22"
ARTIFACTS_SUBDIR = "project-thread"
RELAY = '<relay from="coordinator" thread="t-1">secret-task-text: fix the login page</relay>'
THEMES = (1, 2, 3, 0)


def submit(session, prompt):
    return event("UserPromptSubmit", session, prompt=prompt)


def start_two(bridge):
    bridge.hook(event("SessionStart", "p", source="startup"), project="alpha")
    bridge.hook(event("SessionStart", "n", source="startup"), project="beta")
    bridge.hook(submit("p", RELAY), project="alpha")
    bridge.hook(submit("n", "fix the login page"), project="beta")


def marks(status):
    return [(r["project"], r["project_thread"]) for r in status["rows"]]


@pytest.mark.parametrize("case", ["[SC-22] the hook says project_thread only for a prompt that starts with the "
                                  "coordinator relay, and never passes on any prompt text"])
def test_hook_marks_relayed_prompt(case, artifacts):
    require_bridge_app()
    cases = [
        (submit("a", RELAY), True),
        (submit("a", '<relay from="coordinator">x</relay>'), True),
        (submit("a", "fix the login page"), False),
        (submit("a", f"please read this: {RELAY}"), False),
        (submit("a", '<relay from="someone-else">secret-task-text</relay>'), False),
        (submit("a", f" {RELAY}"), False),
        (event("SessionStart", "a", source="startup", prompt=RELAY), False),
        (event("Stop", "a", stop_hook_active=False, prompt=RELAY), False),
    ]
    sent = capture_hook_messages([(payload, hook_env("alpha")) for payload, _ in cases])
    (artifacts / "hook-messages.json").write_text(json.dumps(sent, indent=2) + "\n", encoding="utf-8")
    assert [m.get("project_thread") for m in sent] == [True if flag else None for _, flag in cases], sent
    text = json.dumps(sent)
    assert "secret-task-text" not in text and "relay" not in text and "login" not in text, sent
    assert all("prompt" not in m for m in sent), sent


@pytest.mark.parametrize("case", ["[SC-22] a project thread carries the 0x40 row bit and shows the orange P on the "
                                  "card in all four themes byte for byte as the host draws it, a plain local thread "
                                  "has neither, and the mark stays through later prompts, a stop and a restart"])
def test_card_marks_project_thread(case, card, run_dir, screen_tools):
    mark = card.mark()
    assert card.request("theme 0") == "OK theme 0"
    card.wait_for(lambda line: line.startswith("THEME saved "), 5.0, mark)
    first = Bridge(run_dir)
    summary = {}
    try:
        first.start()
        start_two(first)
        status = first.settle_rows([("running", "alpha"), ("running", "beta")], total=2)
        frame = first.frames()[-1]
        on_card = card_rows(card)
        screens = []
        for theme in THEMES:
            mark = card.mark()
            assert card.request(f"theme {theme}") == f"OK theme {theme}"
            card.wait_for(lambda line, n=theme: line == f"THEME {n}", 5.0, mark)
            screens.append(compare_screen(card, screen_tools, run_dir, f"theme-{theme}", *scene_of(card_rows(card))))
        first.hook(submit("p", "and the signup page too"), project="alpha")
        first.hook(event("Stop", "p", stop_hook_active=False), project="alpha")
        later = first.settle_rows([("running", "beta"), ("done", "alpha")], total=2)
        summary = {"status": status, "frame": frame, "card": on_card, "screens": screens, "later": later}
    finally:
        first.quit()
        write_summary(run_dir, summary)
    (run_dir / "again").mkdir(exist_ok=True)
    again = Bridge(run_dir / "again", socket_path=first.socket)
    try:
        after = again.start()
    finally:
        again.quit()
        again.forget()
    summary["after"] = after
    write_summary(run_dir, summary)
    assert marks(status) == [("alpha", True), ("beta", False)], summary
    assert [(r["name"], r["project_thread"]) for r in frame["rows"]] == [("alpha", True), ("beta", False)], summary
    assert [(r["name"], r["project_thread"]) for r in on_card["rows"]] == [("alpha", True), ("beta", False)], summary
    assert [s["differing_pixels"] for s in screens] == [0, 0, 0, 0], summary
    assert [s["theme"] for s in screens] == list(THEMES), summary
    assert marks(later) == [("beta", False), ("alpha", True)], summary
    assert marks(after) == marks(later), summary


@pytest.mark.parametrize("case", ["[SC-22] in a real Claude Code session a prompt that starts with the coordinator "
                                  "relay marks the session as a project thread on the card"])
def test_real_relayed_prompt(case, card, bridge, run_dir):
    summary = {}
    try:
        with real_session(bridge, run_dir) as (session, _):
            wait_session_ready(session, bridge)
            start = card.mark()
            session.type_line('<relay from="coordinator">Reply with the single word done and nothing else.</relay>',
                              "enter (prompt)")
            card.wait_for(lambda line: line == "STATE done", 90.0, start)
            status = bridge.status()
            summary = {"states": card_states(card, start), "status": status, "card": card_rows(card),
                       "events": [e for e in bridge.log_entries() if e.get("kind") == "event"]}
    finally:
        write_summary(run_dir, summary)
    assert [r["project_thread"] for r in summary["status"]["rows"]] == [True], summary
    assert [r["project_thread"] for r in summary["card"]["rows"]] == [True], summary
    submitted = [e for e in summary["events"] if e.get("event") == "UserPromptSubmit"]
    assert [e.get("project_thread") for e in submitted] == [True], summary


@pytest.mark.parametrize("case", ["[SC-22] a session table saved by the bridge before SC-22, without the project "
                                  "thread field, still loads and shows plain rows"])
def test_old_table_file_loads(case, card, run_dir):
    old = Bridge(run_dir)
    now = time.time()
    saved = {"next_row": 3, "sessions": {
        "a": {"activity": "running", "pid": os.getpid(), "project": "alpha", "row": 1, "since": now, "visible": True,
              "waiting": []},
        "b": {"activity": "done", "pid": os.getpid(), "project": "beta", "row": 2, "since": now, "visible": True,
              "waiting": []}}}
    old.saved.write_text(json.dumps(saved), encoding="utf-8")
    try:
        old.start()
        status = old.settle_rows([("running", "alpha"), ("done", "beta")], total=2)
        on_card = card_rows(card)
    finally:
        old.quit()
        old.forget()
    write_summary(run_dir, {"saved": saved, "status": status, "card": on_card})
    assert marks(status) == [("alpha", False), ("beta", False)], status
    assert [(r["name"], r["project_thread"]) for r in on_card["rows"]] == [("alpha", False), ("beta", False)], on_card
