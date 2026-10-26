import json
import os
import signal
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

import pytest

from bridge_harness import (HEARTBEAT_FLAG, HIDDEN_MINUTES, Bridge, artifacts, bash, bridge, bridge_cli,  # noqa: F401
                            card, card_rows, event, hook_env, info_fields, require_bridge_app, rows_writes, run_dir, wait_until,
                            write_summary)
from host_render import H, compare_screen, scene_of, screen_tools, wait_quiet  # noqa: F401

ARTIFACTS_PREFIX = "sc"
ARTIFACTS_SUBDIR = "rows"
SHOW_WITHIN_S = 3.0
PID_GONE_WITHIN_S = 15.0
HEARTBEAT_EVERY_S = 30.0
SILENCE_S = 90.0
SILENCE_CHECK_S = 2.0
FRAME_MAX = 233
NAME_MAX = 32
TICK_US = 100_000
BENCH_RUNS = 5
ANIMATION_WATCH_S = 1.0
HELLO_SHOWS_S = 3.0
LYING_STILL_S = 5.0
THEME_SAVE_DELAY_S = 1.0
GROWN_H = 200
GROWN_THEMES = ["board", "arcade"]


def prompt(bridge, session, project, **extra):
    bridge.hook(event("SessionStart", session, source="startup"), project=project, **extra)
    bridge.hook(event("UserPromptSubmit", session, prompt="x"), project=project, **extra)


def fresh_rows(frame):
    return [row["name"] for row in frame["rows"] if row["fresh"]]


def capture_hook_messages(cases):
    path = os.path.join(tempfile.gettempdir(), f"fm-capture-{uuid.uuid4().hex[:8]}.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(4)
    listener.settimeout(5.0)
    sent = []
    try:
        for payload, env in cases:
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
            result, _ = bridge_cli("hook", "--socket", path, payload=json.dumps(payload).encode(), env=env)
            server.join(5.0)
            assert result.returncode == 0 and result.stdout == b"", result
            sent.append(json.loads(received["data"]))
    finally:
        listener.close()
        os.unlink(path)
    return sent


@pytest.mark.parametrize("case", ["[SC-3] the project name is the last part of CLAUDE_PROJECT_DIR, the main repository "
                                  "for a git worktree, and the session start cwd without the variable"])
def test_project_names(case, artifacts, tmp_path):
    require_bridge_app()
    main = tmp_path / "beta"
    (main / ".git" / "worktrees" / "w1").mkdir(parents=True)
    worktree = tmp_path / "elsewhere" / "w1"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'w1'}\n", encoding="utf-8")
    relative = tmp_path / "gamma" / ".claude" / "worktrees" / "w2"
    relative.mkdir(parents=True)
    (tmp_path / "gamma" / ".git" / "worktrees" / "w2").mkdir(parents=True)
    (relative / ".git").write_text("gitdir: ../../../.git/worktrees/w2\n", encoding="utf-8")
    plain = event("UserPromptSubmit", "p", prompt="x")
    cases = [
        (plain, hook_env(tmp_path / "alpha")),
        (plain, hook_env(worktree)),
        (plain, hook_env(relative)),
        (event("SessionStart", "p", source="startup", cwd=str(tmp_path / "delta")), hook_env()),
        (plain, hook_env()),
    ]
    sent = capture_hook_messages(cases)
    (artifacts / "project-names.json").write_text(json.dumps(sent, indent=2) + "\n", encoding="utf-8")
    assert [m.get("project") for m in sent] == ["alpha", "beta", "gamma", "delta", None], sent


@pytest.mark.parametrize("case", ["[SC-3] three projects with one session each show as three rows with their project "
                                  "names and states on the card within 3 seconds"])
def test_three_projects(case, card, bridge, run_dir, screen_tools):
    bridge.start()
    mark = card.mark()
    for session, project in (("a", "alpha"), ("b", "beta"), ("c", "gamma")):
        bridge.hook(event("SessionStart", session, source="startup"), project=project)
    hidden = bridge.status()
    started = time.monotonic()
    for session, project in (("a", "alpha"), ("b", "beta"), ("c", "gamma")):
        bridge.hook(event("UserPromptSubmit", session, prompt="x"), project=project)
    bridge.hook(event("Stop", "b", stop_hook_active=False), project="beta")
    status = bridge.settle_rows([("running", "alpha"), ("running", "gamma"), ("done", "beta")], total=3)
    shown_after = time.monotonic() - started
    wait_quiet(card)
    on_card = card_rows(card)
    screen = compare_screen(card, screen_tools, run_dir, "three", *scene_of(on_card), quiet=True)
    summary = {"hidden": hidden, "status": status, "shown_after_s": shown_after, "card": on_card,
               "card_writes": rows_writes(card, mark), "frames": bridge.frames(), "screen": screen}
    write_summary(run_dir, summary)
    assert screen["meta"]["mode"] == "rows" and screen["differing_pixels"] == 0, screen
    assert hidden["total"] == 0 and hidden["rows"] == [] and len(hidden["sessions"]) == 3, summary
    assert shown_after < SHOW_WITHIN_S, summary
    assert status["max_write"] >= FRAME_MAX and status["name_limit"] == NAME_MAX, summary
    assert on_card["reply"]["total"] == "3" and on_card["reply"]["shown"] == "3", summary
    assert [(r["state"], r["name"]) for r in on_card["rows"]] == [("running", "alpha"), ("running", "gamma"),
                                                                  ("done", "beta")], summary
    assert [r["row"] for r in on_card["rows"]] == [r["row"] for r in status["rows"]], summary
    assert len({r["row"] for r in status["rows"]}) == 3 and 0 not in {r["row"] for r in status["rows"]}, summary


@pytest.mark.parametrize("case", ["[SC-4] a second session in the same project adds a row with the same name, and "
                                  "ending a session or clearing it removes its row within 3 seconds"])
def test_same_project_and_end(case, card, bridge, run_dir):
    bridge.start()
    prompt(bridge, "a", "app")
    prompt(bridge, "b", "app")
    two = bridge.settle_rows([("running", "app"), ("running", "app")], total=2)
    started = time.monotonic()
    bridge.hook(event("SessionEnd", "a", reason="other"), project="app")
    one = bridge.settle_rows([("running", "app")], total=1)
    ended_after = time.monotonic() - started
    bridge.hook(event("SessionEnd", "b", reason="clear"), project="app")
    bridge.hook(event("SessionStart", "b2", source="clear"), project="app")
    cleared = bridge.settle_rows([], total=0)
    bridge.hook(event("UserPromptSubmit", "b2", prompt="x"), project="app")
    again = bridge.settle_rows([("running", "app")], total=1)
    summary = {"two": two, "one": one, "ended_after_s": ended_after, "cleared": cleared, "again": again,
               "card": card_rows(card)}
    write_summary(run_dir, summary)
    assert two["rows"][0]["row"] != two["rows"][1]["row"], summary
    assert ended_after < SHOW_WITHIN_S, summary
    assert one["rows"][0]["row"] == two["sessions"]["b"]["row"], summary
    assert set(cleared["sessions"]) == {"b2"}, summary
    assert again["rows"][0]["row"] not in {r["row"] for r in two["rows"]}, summary
    assert [(r["state"], r["name"]) for r in summary["card"]["rows"]] == [("running", "app")], summary


@pytest.mark.parametrize("case", ["[SC-5] waiting and error move a row up, are flagged as new exactly once, and every "
                                  "way of waiting ends on its own signal"])
def test_waiting_and_error(case, card, bridge, run_dir):
    bridge.start()
    for session, project in (("w", "web"), ("x", "api"), ("y", "app")):
        prompt(bridge, session, project)
    bridge.settle_rows([("running", "web"), ("running", "api"), ("running", "app")])
    steps = {}
    bridge.hook(event("Notification", "w", notification_type="permission_prompt", message="needs network"),
                project="web")
    steps["notification"] = bridge.settle_rows([("waiting", "web"), ("running", "api"), ("running", "app")])
    bridge.hook(event("PostToolUse", "w", tool_use_id="t1", tool_response={}, **bash("curl x")), project="web")
    steps["notification_cleared"] = bridge.settle_rows([("running", "api"), ("running", "app"), ("running", "web")])
    plan = {"tool_name": "ExitPlanMode", "tool_input": {"plan": "do it"}}
    bridge.hook(event("PreToolUse", "x", tool_use_id="plan1", **plan), project="api")
    steps["plan"] = bridge.settle_rows([("waiting", "api"), ("running", "app"), ("running", "web")])
    bridge.hook(event("PreToolUse", "x", tool_use_id="t2", **bash("ls")), project="api")
    steps["plan_still"] = bridge.settle_rows([("waiting", "api"), ("running", "app"), ("running", "web")])
    bridge.hook(event("PostToolUse", "x", tool_use_id="plan1", tool_response={}, **plan), project="api")
    steps["plan_cleared"] = bridge.settle_rows([("running", "app"), ("running", "web"), ("running", "api")])
    bridge.hook(event("StopFailure", "y", error="rate_limit"), project="app")
    steps["error"] = bridge.settle_rows([("error", "app"), ("running", "web"), ("running", "api")])
    run = bridge.hook_async(event("PermissionRequest", "w", **bash("make deploy")), project="web")
    steps["permission"] = bridge.settle_rows([("waiting", "web"), ("error", "app"), ("running", "api")])
    bridge.hook(event("PreToolUse", "x", tool_use_id="t3", **bash("ls")), project="api")
    time.sleep(1.0)
    frames = bridge.frames()
    mark = card.mark()
    assert card.request("ble disconnect") == "OK ble disconnect"
    card.wait_for(lambda line: line.startswith("BLE rows "), 30.0, mark)
    resent = bridge.frames()[len(frames):]
    run.stop()
    write_summary(run_dir, {"steps": steps, "frames": frames, "resent": resent})
    flagged = [fresh_rows(f) for f in frames]
    assert flagged.count(["web"]) == 2 and flagged.count(["api"]) == 1 and flagged.count(["app"]) == 1, flagged
    assert sum(1 for f in flagged if f) == 4, flagged
    assert len(resent) == 1 and fresh_rows(resent[0]) == [], resent
    assert [(r["state"], r["name"]) for r in resent[0]["rows"]] == [("waiting", "web"), ("error", "app"),
                                                                    ("running", "api")], resent


@pytest.mark.parametrize("case", ["[SC-5] rejecting in the terminal after the permission prompt notification "
                                  "arrived brings the row back to running"])
def test_rejected_after_notification(case, card, bridge, run_dir):
    bridge.start()
    prompt(bridge, "r", "web")
    bridge.settle_rows([("running", "web")])
    run = bridge.hook_async(event("PermissionRequest", "r", **bash("make deploy")), project="web")
    waiting = bridge.settle_rows([("waiting", "web")])
    bridge.hook(event("Notification", "r", notification_type="permission_prompt", message="needs approval"),
                project="web")
    time.sleep(0.5)
    run.proc.kill()
    run.proc.wait(5.0)
    status = bridge.settle_rows([("running", "web")])
    write_summary(run_dir, {"waiting": waiting, "status": status, "frames": bridge.frames()})
    assert status["waits"] == [] and status["sessions"]["r"]["waiting"] == 0, status


@pytest.mark.parametrize("case", ["[SC-5] two sessions with the same name entering waiting one after the other are "
                                  "each flagged once"])
def test_same_name_flagged_each(case, card, bridge, run_dir):
    bridge.start()
    prompt(bridge, "a", "app")
    prompt(bridge, "b", "app")
    bridge.settle_rows([("running", "app"), ("running", "app")])
    first = bridge.hook_async(event("PermissionRequest", "a", **bash("make a")), project="app")
    status_a = bridge.settle_rows([("waiting", "app"), ("running", "app")])
    second = bridge.hook_async(event("PermissionRequest", "b", **bash("make b")), project="app")
    status_b = bridge.settle_rows([("waiting", "app"), ("waiting", "app")])
    frames = bridge.frames()
    first.stop()
    second.stop()
    row_a = status_a["sessions"]["a"]["row"]
    row_b = status_b["sessions"]["b"]["row"]
    flagged = [[r["row"] for r in f["rows"] if r["fresh"]] for f in frames]
    write_summary(run_dir, {"frames": frames, "flagged": flagged, "row_a": row_a, "row_b": row_b})
    assert [f for f in flagged if f] == [[row_a], [row_b]], flagged


@pytest.mark.parametrize("case", ["[SC-5] twenty permission requests sent at once are all kept and all released"])
def test_twenty_requests(case, card, bridge, run_dir):
    bridge.start()
    sessions = [f"s{i:02d}" for i in range(20)]
    for name in sessions:
        prompt(bridge, name, name)
    runs = []
    threads = [threading.Thread(target=lambda n=name: runs.append(
        bridge.hook_async(event("PermissionRequest", n, **bash(f"make {n}")), project=n))) for name in sessions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    waiting = wait_until(lambda: (s := bridge.status()) and len(s["waits"]) == 20 and s["synced"] and s, 10.0,
                         "twenty waits")
    for name in sessions:
        bridge.hook(event("PostToolUse", name, tool_use_id="t", tool_response={}, **bash(f"make {name}")), project=name)
    results = [run.result() for run in runs]
    released = bridge.settle(["running"] * 6)
    write_summary(run_dir, {"waiting": waiting, "released": released, "results": results})
    assert waiting["total"] == 20 and [r["state"] for r in waiting["rows"]] == ["waiting"] * 6, waiting
    assert all(waiting["sessions"][n]["state"] == "waiting" for n in sessions), waiting
    assert all(r["exit"] == 0 for r in results), results
    assert released["waits"] == [] and released["total"] == 20, released


@pytest.mark.parametrize("case", ["[SC-6][SC-23] seven sessions show six rows ordered waiting, error, running, done; "
                                  "the longest waiting and the earliest running come first, the latest done comes "
                                  "first, and the one left out is the done row that finished first"])
def test_seven_sessions(case, card, bridge, run_dir, screen_tools):
    bridge.start()
    for session in ("r1", "r2", "d1", "d2", "e1", "w1", "w2"):
        prompt(bridge, session, {"r1": "zeta", "r2": "Beta", "d1": "alpha", "d2": "omega", "e1": "eta",
                                 "w1": "zz", "w2": "aa"}[session])
    bridge.hook(event("Stop", "d1", stop_hook_active=False), project="alpha")
    bridge.hook(event("Stop", "d2", stop_hook_active=False), project="omega")
    bridge.hook(event("StopFailure", "e1", error="server_error"), project="eta")
    runs = [bridge.hook_async(event("PermissionRequest", "w1", **bash("x")), project="zz")]
    bridge.settle_rows([("waiting", "zz"), ("error", "eta"), ("running", "zeta"), ("running", "Beta"),
                        ("running", "aa"), ("done", "omega")], total=7)
    runs.append(bridge.hook_async(event("PermissionRequest", "w2", **bash("x")), project="aa"))
    status = bridge.settle_rows([("waiting", "zz"), ("waiting", "aa"), ("error", "eta"), ("running", "zeta"),
                                 ("running", "Beta"), ("done", "omega")], total=7)
    wait_quiet(card)
    on_card = card_rows(card)
    screen = compare_screen(card, screen_tools, run_dir, "seven", *scene_of(on_card), quiet=True)
    frame = bridge.frames()[-1]
    for run in runs:
        run.stop()
    write_summary(run_dir, {"status": status, "card": on_card, "frame": frame, "screen": screen})
    assert screen["differing_pixels"] == 0, screen
    assert frame["total"] == 7 and frame["shown"] == 6, frame
    assert on_card["reply"]["total"] == "7" and len(on_card["rows"]) == 6, on_card
    assert "alpha" not in [r["name"] for r in on_card["rows"]], on_card


def wait_on(bridge, session, project):
    run = bridge.hook_async(event("PermissionRequest", session, **bash("x")), project=project)
    wait_until(lambda: (s := bridge.status()) and s["sessions"][session]["state"] == "waiting" and s, 5.0,
               f"{session} waiting")
    return run


def screen_in(card, screen_tools, run_dir, theme, name):
    mark = card.mark()
    assert card.request(f"theme {theme}") == f"OK theme {theme}"
    card.wait_for(lambda line, n=theme: line == f"THEME {n}", 5.0, mark)
    wait_quiet(card)
    return compare_screen(card, screen_tools, run_dir, f"{name}-theme-{theme}", *scene_of(card_rows(card)), quiet=True)


@pytest.mark.parametrize("case", ["[SC-23] with more than five sessions waiting or failed the sessions of one project "
                                  "in one state share a row with their count, the card draws the count byte for byte "
                                  "as the host in the board and a grid theme, and every session is still counted"])
def test_merged_rows(case, card, bridge, run_dir, screen_tools):
    waiting = [("f1", "FunMedal"), ("f2", "FunMedal"), ("f3", "FunMedal"), ("s1", "SiBlog"), ("s2", "SiBlog"),
               ("a1", "agent-skills"), ("a2", "agent-skills")]
    bridge.start()
    for session, project in waiting + [("h", "hermes"), ("r", "shop"), ("n", "notes")]:
        prompt(bridge, session, project)
    bridge.hook(event("Stop", "n", stop_hook_active=False), project="notes")
    bridge.hook(event("StopFailure", "h", error="server_error"), project="hermes")
    runs = []
    screens = []
    try:
        for session, project in waiting:
            runs.append(wait_on(bridge, session, project))
        status = bridge.settle_rows([("waiting", "FunMedal"), ("waiting", "SiBlog"), ("waiting", "agent-skills"),
                                     ("error", "hermes"), ("running", "shop"), ("done", "notes")], total=10)
        frame = bridge.frames()[-1]
        on_card = card_rows(card)
        for theme in (0, 2):
            screens.append(screen_in(card, screen_tools, run_dir, theme, "merged"))
    finally:
        for run in runs:
            run.stop()
        card.request("theme 0")
    write_summary(run_dir, {"status": status, "frame": frame, "card": on_card, "screens": screens})
    assert [s["differing_pixels"] for s in screens] == [0, 0], screens
    assert (frame["total"], frame["urgent"], frame["shown"]) == (10, 8, 6), frame
    assert [r["count"] for r in frame["rows"]] == [3, 2, 2, 1, 1, 1], frame
    assert [r["count"] for r in status["rows"]] == [3, 2, 2, 1, 1, 1], status
    assert [(r["state"], r["name"], r["count"]) for r in on_card["rows"]] == [
        ("waiting", "FunMedal", 3), ("waiting", "SiBlog", 2), ("waiting", "agent-skills", 2), ("error", "hermes", 1),
        ("running", "shop", 1), ("done", "notes", 1)], on_card
    assert (on_card["reply"]["total"], on_card["reply"]["urgent"]) == ("10", "8"), on_card
    assert status["rows"][0]["row"] == status["sessions"]["f1"]["row"], status


@pytest.mark.parametrize("case", ["[SC-23] when seven different projects wait one after another the seventh, which "
                                  "does not fit, is still sent flagged and alerts on the card, and afterwards the six "
                                  "longest waiting stay on screen while the header counts the one left out"])
def test_squeezed_waiting_alerts(case, card, bridge, run_dir, screen_tools):
    projects = [f"p{i}" for i in range(1, 8)]
    bridge.start()
    for project in projects:
        prompt(bridge, project, project)
    runs = []
    screens = []
    try:
        for project in projects[:6]:
            runs.append(wait_on(bridge, project, project))
        bridge.settle_rows([("waiting", p) for p in projects[:6]], total=7)
        frames_before = len(bridge.frames())
        wait_quiet(card)
        mark = card.mark()
        runs.append(wait_on(bridge, "p7", "p7"))
        _, _, alert = card.wait_for(lambda line: line.startswith("EVENT "), SHOW_WITHIN_S + 2.0, mark)
        status = bridge.settle_rows([("waiting", p) for p in projects[:6]], total=7)
        frames = bridge.frames()[frames_before:]
        on_card = card_rows(card)
        for theme in (0, 2):
            screens.append(screen_in(card, screen_tools, run_dir, theme, "squeezed"))
    finally:
        for run in runs:
            run.stop()
        card.request("theme 0")
    flagged = [r["name"] for f in frames for r in f["rows"] if r["fresh"]]
    write_summary(run_dir, {"alert": alert, "frames": frames, "flagged": flagged, "status": status, "card": on_card,
                            "screens": screens})
    assert alert == "EVENT wait p7", alert
    assert flagged == ["p7"], flagged
    assert [r["name"] for r in on_card["rows"]] == projects[:6], on_card
    assert (on_card["reply"]["total"], on_card["reply"]["urgent"]) == ("7", "7"), on_card
    assert (status["total"], status["urgent"]) == (7, 7), status
    assert [s["differing_pixels"] for s in screens] == [0, 0], screens


def press_middle_key(card):
    assert card.request("key up press") == "OK key up press"


@pytest.mark.parametrize("case", ["[SC-8] the middle key turns board, arcade, comic, tower, board in order, each theme "
                                  "draws byte for byte as the host renderer, the card saves the theme a second after "
                                  "the last change, and the theme survives a reboot"])
def test_theme_key_and_reboot(case, card, bridge, run_dir, screen_tools):
    mark = card.mark()
    assert card.request("theme 0") == "OK theme 0"
    card.wait_for(lambda line: line.startswith("THEME saved "), 5.0, mark)
    bridge.start()
    for session, project in (("w", "web"), ("e", "api"), ("r", "app"), ("d", "docs")):
        prompt(bridge, session, project)
    bridge.hook(event("Stop", "d", stop_hook_active=False), project="docs")
    bridge.hook(event("StopFailure", "e", error="server_error"), project="api")
    run = bridge.hook_async(event("PermissionRequest", "w", **bash("x")), project="web")
    turns, screens = [], []
    try:
        bridge.settle_rows([("waiting", "web"), ("error", "api"), ("running", "app"), ("done", "docs")])
        for expected in (1, 2, 3, 0):
            mark = card.mark()
            press_middle_key(card)
            _, changed_t, _ = card.wait_for(lambda line, n=expected: line == f"THEME {n}", 5.0, mark)
            _, saved_t, saved = card.wait_for(lambda line: line.startswith("THEME saved "), 5.0, mark)
            turns.append({"theme": expected, "saved": saved, "save_after_s": saved_t - changed_t})
            wait_quiet(card)
            screens.append(compare_screen(card, screen_tools, run_dir, f"theme-{expected}", *scene_of(card_rows(card)), quiet=True))
        mark = card.mark()
        press_middle_key(card)
        press_middle_key(card)
        card.wait_for(lambda line: line.startswith("THEME saved "), 5.0, mark)
        time.sleep(THEME_SAVE_DELAY_S * 1.5)
        quick = [line for _, line in card.since(mark) if line.startswith("THEME")]
        mark = card.mark()
        assert card.request("reboot") == "OK reboot"
        card.wait_for(lambda line: line.startswith("READY "), 20.0, mark)
        rebooted = info_fields(card.request("info"))
    finally:
        run.stop()
        card.request("theme 0")
    write_summary(run_dir, {"turns": turns, "screens": screens, "quick": quick, "rebooted": rebooted})
    assert [s["theme"] for s in screens] == [1, 2, 3, 0], screens
    assert all(s["mode"] == "rows" and s["differing_pixels"] == 0 for s in screens), screens
    assert all(t["saved"] == f"THEME saved {t['theme']} rc=ESP_OK" for t in turns), turns
    assert all(THEME_SAVE_DELAY_S * 0.8 <= t["save_after_s"] <= THEME_SAVE_DELAY_S * 1.5 for t in turns), turns
    assert quick == ["THEME 1", "THEME 2", "THEME saved 2 rc=ESP_OK"], quick
    assert rebooted["theme"] == "2" and int(rebooted["uptime_s"]) < 60, rebooted


def bench(card, y0, rows):
    runs = [dict(p.split("=", 1) for p in card.request(f"bench {y0} {rows}").split()[2:]) for _ in range(BENCH_RUNS)]
    totals = sorted(int(r["total_us"]) for r in runs)
    return {"runs": runs, "median_total_us": totals[len(totals) // 2], "max_total_us": totals[-1]}


@pytest.mark.parametrize("case", ["[SC-6] with six sessions on the card one 36 pixel slot, one 80 pixel band and all "
                                  "six slots each redraw within one 100 ms animation step"])
def test_draw_speed(case, card, bridge, run_dir):
    bridge.start()
    projects = {"w": "waiting-project", "e": "eta", "r1": "zeta-a-rather-long-project-nm", "r2": "Beta",
                "d1": "alpha", "d2": "omega"}
    for session, project in projects.items():
        prompt(bridge, session, project)
    bridge.hook(event("Stop", "d1", stop_hook_active=False), project="alpha")
    bridge.hook(event("Stop", "d2", stop_hook_active=False), project="omega")
    bridge.hook(event("StopFailure", "e", error="server_error"), project="eta")
    run = bridge.hook_async(event("PermissionRequest", "w", **bash("x")), project="waiting-project")
    try:
        bridge.settle_rows([("waiting", "waiting-project"), ("error", "eta"),
                            ("running", "zeta-a-rather-long-project-nm"), ("running", "Beta"), ("done", "omega"),
                            ("done", "alpha")], total=6)
        on_card = card_rows(card)
        before = info_fields(card.request("info"))
        time.sleep(ANIMATION_WATCH_S)
        after = info_fields(card.request("info"))
        timing = {"slot": bench(card, 96, 36), "band_80": bench(card, 24, 80), "six_slots": bench(card, 24, 216)}
    finally:
        run.stop()
    animation = {"before": before, "after": after, "draws": int(after["draws"]) - int(before["draws"]),
                 "ticks": int(after["tick"]) - int(before["tick"])}
    write_summary(run_dir, {"card": on_card, "animation": animation, "timing": timing})
    assert 5 <= animation["draws"] <= animation["ticks"] + 1, animation
    assert after["shown_tick"] != before["shown_tick"] and after["draw_errors"] == before["draw_errors"], animation
    assert len(on_card["rows"]) == 6, on_card
    assert all(r["shown"] == "6" for t in timing.values() for r in t["runs"]), timing
    assert all(t["max_total_us"] < TICK_US for t in timing.values()), timing


@pytest.mark.parametrize("case", ["[SC-18] with two sessions on the card the character is drawn at its biggest size "
                                  "byte for byte as on the host, and the band from her top to the screen bottom and "
                                  "the whole screen each redraw within one 100 ms animation step, counted while she taps after the hello "
                                  "wave and the still lying pose"])
@pytest.mark.parametrize("way", GROWN_THEMES)
def test_grown_character_draw_speed(case, way, card, bridge, run_dir, screen_tools):
    theme = GROWN_THEMES.index(way)
    mark = card.mark()
    assert card.request(f"theme {theme}") == f"OK theme {theme}"
    card.wait_for(lambda line: line.startswith("THEME saved "), 5.0, mark)
    try:
        linked = card.mark()
        bridge.start()
        _, hello_t, _ = card.wait_for(lambda line: line.startswith("EVENT hello"), 5.0, linked)
        for session, project in (("a", "alpha"), ("b", "beta")):
            prompt(bridge, session, project)
        bridge.settle_rows([("running", "alpha"), ("running", "beta")], total=2)
        time.sleep(max(0.0, hello_t + HELLO_SHOWS_S + 0.3 - time.time()))
        wait_quiet(card)
        screen = compare_screen(card, screen_tools, run_dir, f"grown-{theme}", *scene_of(card_rows(card)), quiet=True)
        top = screen["character"][1]
        time.sleep(max(0.0, hello_t + HELLO_SHOWS_S + LYING_STILL_S + 0.1 - time.time()))
        before = info_fields(card.request("info"))
        time.sleep(ANIMATION_WATCH_S)
        after = info_fields(card.request("info"))
        timing = {"character_band": bench(card, top, H - top), "whole_screen": bench(card, 0, H)}
    finally:
        card.request("theme 0")
    animation = {"draws": int(after["draws"]) - int(before["draws"]), "ticks": int(after["tick"]) - int(before["tick"])}
    write_summary(run_dir, {"screen": screen, "animation": animation, "timing": timing})
    assert screen["theme"] == theme and screen["differing_pixels"] == 0, screen
    assert screen["character"][3] == GROWN_H, screen
    assert 5 <= animation["draws"] <= animation["ticks"] + 1, animation
    assert after["draw_errors"] == before["draw_errors"], (before, after)
    assert all(r["shown"] == "2" for t in timing.values() for r in t["runs"]), timing
    assert all(t["max_total_us"] < TICK_US for t in timing.values()), timing


@pytest.mark.parametrize("case", ["[SC-7] a done row carries the minutes since it finished, other rows carry none, "
                                  "and names are cut at a character boundary"])
def test_done_minutes_and_bytes(case, card, run_dir):
    finished = time.time() - 61 * 60 - 5
    long_name = "项目" * 6
    saved = {"next_row": 9, "sessions": {
        "d": {"row": 7, "project": long_name, "activity": "done", "since": finished, "pid": os.getpid(),
              "visible": True, "waiting": []},
        "r": {"row": 8, "project": "run", "activity": "running", "since": time.time(), "pid": os.getpid(),
              "visible": True, "waiting": []},
    }}
    bridge = Bridge(run_dir)
    bridge.saved.write_text(json.dumps(saved), encoding="utf-8")
    try:
        status = bridge.start()
        frame = bridge.frames()[-1]
        raw = next(e["frame"] for e in reversed(bridge.log_entries()) if e.get("what") == "rows")
        on_card = card_rows(card)
    finally:
        bridge.quit()
        bridge.forget()
    cut = "项目" * 5
    write_summary(run_dir, {"status": status, "frame": frame, "raw": raw, "card": on_card})
    assert raw == ("0202020000" + "0803ffff0103" + b"run".hex() + "07043d0001" + f"{len(cut.encode()):02x}"
                   + cut.encode().hex()), raw
    assert frame["rows"][1]["minutes"] == 61 and frame["rows"][0]["minutes"] == HIDDEN_MINUTES, frame
    assert [(r["row"], r["minutes"], r["name"]) for r in on_card["rows"]] == [(8, None, "run"), (7, 61, cut)], on_card


@pytest.mark.parametrize("case", ["[SC-10] with no sessions the bridge writes an empty table, and after it quits the "
                                  "card no longer counts itself linked"])
def test_empty_and_quit(case, card, bridge, run_dir, screen_tools):
    mark = card.mark()
    bridge.start()
    card.wait_for(lambda line: line.startswith("BLE rows "), 5.0, mark)
    frame = bridge.frames()[-1]
    wait_quiet(card)
    linked = card_rows(card)
    empty = compare_screen(card, screen_tools, run_dir, "empty", *scene_of(linked), quiet=True)
    bridge.quit()
    unlinked = wait_until(lambda: (r := card_rows(card)) and r["reply"]["linked"] == "0" and r, 10.0, "card unlinked")
    gone = compare_screen(card, screen_tools, run_dir, "unlinked", *scene_of(unlinked), quiet=True)
    write_summary(run_dir, {"frame": frame, "linked": linked, "unlinked": unlinked, "writes": rows_writes(card, mark),
                            "screens": [empty, gone]})
    assert frame == {"bytes": 5, "format": 2, "total": 0, "shown": 0, "flags": 0, "urgent": 0, "rows": []}, frame
    assert linked["reply"] == {"total": "0", "shown": "0", "urgent": "0", "linked": "1"}, linked
    assert (empty["meta"]["mode"], empty["differing_pixels"]) == ("empty", 0), empty
    assert (gone["meta"]["mode"], gone["differing_pixels"]) == ("unlinked", 0), gone


@pytest.mark.parametrize("case", ["[SC-10] the bridge writes the table only when it changes and a five byte heartbeat "
                                  "every 30 seconds"])
def test_only_changes_and_heartbeat(case, card, bridge, run_dir):
    bridge.start()
    prompt(bridge, "h", "beat")
    bridge.settle_rows([("running", "beat")])
    tables = len(bridge.frames())
    mark = card.mark()
    bridge.hook(event("PreToolUse", "h", tool_use_id="t1", **bash("ls")), project="beat")
    bridge.hook(event("PostToolUse", "h", tool_use_id="t1", tool_response={}, **bash("ls")), project="beat")
    _, first_t, _ = card.wait_for(lambda line: line == "BLE heartbeat", HEARTBEAT_EVERY_S + 5.0, mark)
    second = card.mark()
    _, second_t, _ = card.wait_for(lambda line: line == "BLE heartbeat", HEARTBEAT_EVERY_S + 5.0, second)
    status = wait_until(lambda: (s := bridge.status()) and s["heartbeats"] >= 2 and s, 5.0, "heartbeats acknowledged")
    beats = bridge.frames("heartbeat")
    summary = {"tables_before": tables, "tables_after": len(bridge.frames()), "beats": beats,
               "card": [l for _, l in card.since(mark)], "status": status, "beat_gap_s": second_t - first_t}
    write_summary(run_dir, summary)
    assert summary["tables_after"] == tables, summary
    assert HEARTBEAT_EVERY_S - 5.0 <= summary["beat_gap_s"] <= HEARTBEAT_EVERY_S + 5.0, summary
    assert beats and beats[0] == {"bytes": 5, "format": 2, "total": 1, "shown": 0, "flags": HEARTBEAT_FLAG,
                                  "urgent": 0, "rows": []}, summary
    assert summary["status"]["heartbeats"] >= 2, summary


@pytest.mark.parametrize("case", ["[SC-10] a bridge that stops writing while the link stays up is dropped by the "
                                  "card after 90 seconds of silence, and the card shows unlinked"])
def test_silent_bridge_unlinks(case, card, bridge, run_dir):
    started = bridge.start()
    prompt(bridge, "q", "quiet")
    bridge.settle_rows([("running", "quiet")])
    mark = card.mark()
    os.kill(started["pid"], signal.SIGSTOP)
    stopped = time.time()
    try:
        _, silent_t, silent = card.wait_for(lambda line: line.startswith("BLE silent"),
                                            SILENCE_S + SILENCE_CHECK_S + 5.0, mark)
        after = card_rows(card)
        shown = info_fields(card.request("info"))
    finally:
        os.kill(started["pid"], signal.SIGCONT)
    summary = {"silent": silent, "silent_after_s": silent_t - stopped, "rows": after, "info": shown,
               "card": [line for _, line in card.since(mark)]}
    write_summary(run_dir, summary)
    assert silent == "BLE silent rc=0", summary
    assert SILENCE_S - HEARTBEAT_EVERY_S - 1.0 <= summary["silent_after_s"] <= SILENCE_S + SILENCE_CHECK_S + 1.0, summary
    assert after["reply"]["linked"] == "0" and after["rows"] == [], summary
    assert shown["mode"] == "unlinked", summary


@pytest.mark.parametrize("case", ["[SC-4] a session whose Claude Code process is gone leaves the table within 15 "
                                  "seconds"])
def test_session_of_dead_process(case, card, bridge, run_dir):
    bridge.start()
    claude = subprocess.Popen(["sleep", "600"])
    try:
        prompt(bridge, "k", "killed", pid=claude.pid)
        prompt(bridge, "s", "stays")
        before = bridge.settle_rows([("running", "killed"), ("running", "stays")])
        claude.kill()
        claude.wait()
        killed = time.monotonic()
        after = bridge.settle_rows([("running", "stays")], timeout=PID_GONE_WITHIN_S + 5.0)
        gone_after = time.monotonic() - killed
    finally:
        if claude.poll() is None:
            claude.kill()
    write_summary(run_dir, {"before": before, "after": after, "gone_after_s": gone_after})
    assert before["sessions"]["k"]["pid"] == claude.pid, before
    assert gone_after < PID_GONE_WITHIN_S, gone_after


@pytest.mark.parametrize("case", ["[SC-3] a restarted bridge reads the session table back and shows the same rows"])
def test_restart_reads_table(case, card, run_dir):
    first = Bridge(run_dir)
    try:
        first.start()
        prompt(first, "a", "alpha")
        prompt(first, "b", "beta")
        first.hook(event("Stop", "b", stop_hook_active=False), project="beta")
        gave_up = first.hook_async(event("PermissionRequest", "a", **bash("make x")), project="alpha", wait_limit=1)
        gave_up.result()
        before = first.settle_rows([("waiting", "alpha"), ("done", "beta")], total=2)
    finally:
        first.quit()
    saved = json.loads(first.saved.read_text(encoding="utf-8"))
    (run_dir / "again").mkdir(exist_ok=True)
    again = Bridge(run_dir / "again", socket_path=first.socket)
    try:
        after = again.start()
    finally:
        again.quit()
        again.forget()
    write_summary(run_dir, {"before": before, "saved": saved, "after": after})
    assert [(r["row"], r["state"], r["project"]) for r in after["rows"]] == \
        [(r["row"], r["state"], r["project"]) for r in before["rows"]], (before, after)
    assert [r["state"] for r in after["rows"]] == ["waiting", "done"], after
