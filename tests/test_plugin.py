import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from bridge_harness import (APP_CLAUDES, BRIDGE, HOOK_EVENTS, HOOK_TIMEOUT_S, INSTALLED_BRIDGE,  # noqa: F401
                            PERMISSION_HOOK_TIMEOUT_S, PLUGIN, ROOT, artifacts, bridge, bridge_cli, card, card_states,
                            claude_version, event, press, real_session,
                            request_touch, require_bridge_app, run_dir, wait_prompt, wait_session_ready, wait_until,
                            write_summary)
from claude_session import INHERITED_PREFIXES

ARTIFACTS_PREFIX = "ob7"
ARTIFACTS_SUBDIR = "plugin"
PLUGIN_ID = "funmedal@funmedal"
NEEDED_VERSION = "2.1.292"
INSTALLED_COMMAND = '"$HOME/Applications/FunMedalBridge.app/Contents/MacOS/funmedal-bridge" hook'
BLOCKING_EXIT = 2
USER_FILES = [".claude/settings.json", ".claude/plugins/known_marketplaces.json", ".claude/plugins/installed_plugins.json"]
FAKE_CLAUDE = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_CLAUDE_LOG"
if [ "$*" = "--version" ]; then printf '%s\\n' "$FAKE_CLAUDE_VERSION"; fi
if [ -n "$FAKE_CLAUDE_FAIL" ] && [ "$*" = "$FAKE_CLAUDE_FAIL" ]; then exit 1; fi
exit 0
"""


LABELED_CLAUDE = """#!/bin/sh
printf '%s\\n' "{label}: $*" >> "$FAKE_CLAUDE_LOG"
if [ "$*" = "--version" ]; then {version_answer}; fi
if [ -n "$FAKE_CLAUDE_FAIL" ] && [ "$*" = "$FAKE_CLAUDE_FAIL" ]; then exit 1; fi
if [ "$*" = "plugin list --json" ]; then printf '%s\\n' "$FAKE_CLAUDE_PLUGINS"; fi
if [ "$*" = "plugin marketplace list --json" ]; then printf '%s\\n' "$FAKE_CLAUDE_MARKETPLACES"; fi
exit 0
"""
TERMINAL = "terminal"


def desktop_claude_path(home, version_dir):
    return (home / "Library" / "Application Support" / "Claude" / "claude-code" / version_dir / "a1b2c3d4e5f6"
            / "claude.app" / "Contents" / "MacOS" / "claude")


def write_labeled_claude(path, label, version):
    path.parent.mkdir(parents=True, exist_ok=True)
    answer = f"printf '%s\\n' '{version}'" if version else "echo 'error: incomplete download' >&2; exit 1"
    path.write_text(LABELED_CLAUDE.format(label=label, version_answer=answer), encoding="utf-8")
    path.chmod(0o755)


def fake_claudes(tmp_path, terminal=None, desktop=None):
    home = tmp_path / "home"
    home.mkdir()
    tools = tmp_path / "bin"
    tools.mkdir()
    if terminal is not None:
        write_labeled_claude(tools / "claude", TERMINAL, terminal)
    for version_dir, version in (desktop or {}).items():
        write_labeled_claude(desktop_claude_path(home, version_dir), f"desktop {version_dir}", version)
    return home, tools


def run_labeled(tmp_path, script, terminal=None, desktop=None, extra_env=None):
    home, tools = fake_claudes(tmp_path, terminal, desktop)
    calls = tmp_path / "claude-calls.txt"
    env = {"HOME": str(home), "PATH": f"{tools}:/usr/bin:/bin", "LC_CTYPE": "C.UTF-8", "FAKE_CLAUDE_LOG": str(calls),
           "FAKE_CLAUDE_FAIL": "", **(extra_env or {})}
    result = subprocess.run(["/bin/sh", str(script)], env=env, capture_output=True, text=True, timeout=60)
    made = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return {"terminal": terminal, "desktop": desktop, "home": str(home), "exit": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr, "calls": made,
            "work_calls": [c for c in made if not c.endswith(": --version")],
            "installed_sha256": sha256(installed_copy(home))}


def require_desktop_claude():
    bundled = sorted(APP_CLAUDES.glob("*/*/claude.app/Contents/MacOS/claude"))
    readable = [(claude_version(str(p)), p) for p in bundled]
    readable = [pair for pair in readable if pair[0] is not None]
    if not readable:
        pytest.fail(f"no runnable Claude Code from the Claude desktop app under {APP_CLAUDES}")
    version, program = max(readable)
    return program.parents[2], version


def link_desktop_claude(home, app_bundle, version):
    target = desktop_claude_path(home, ".".join(map(str, version))).parents[2]
    target.parent.mkdir(parents=True)
    target.symlink_to(app_bundle)
    return target / "Contents" / "MacOS" / "claude"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def own_env(home):
    env = {k: v for k, v in os.environ.items() if not k.startswith(INHERITED_PREFIXES) and not k.startswith("FUNMEDAL")}
    env["HOME"] = str(home)
    return env


def plugin_hooks():
    return json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]


def installed_copy(home):
    return home / "Applications" / "FunMedalBridge.app" / "Contents" / "MacOS" / "funmedal-bridge"


def run_install(tmp_path, version, fail="", script=ROOT / "install.sh"):
    home = tmp_path / "home"
    home.mkdir()
    tools = tmp_path / "bin"
    tools.mkdir()
    calls = tmp_path / "claude-calls.txt"
    if version is not None:
        fake = tools / "claude"
        fake.write_text(FAKE_CLAUDE, encoding="utf-8")
        fake.chmod(0o755)
    env = {"HOME": str(home), "PATH": f"{tools}:/usr/bin:/bin", "FAKE_CLAUDE_LOG": str(calls),
           "FAKE_CLAUDE_VERSION": version or "", "FAKE_CLAUDE_FAIL": fail}
    result = subprocess.run(["/bin/sh", str(script)], env=env, capture_output=True, text=True, timeout=60)
    return {"version": version, "exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
            "calls": calls.read_text(encoding="utf-8").splitlines() if calls.exists() else [],
            "installed_sha256": sha256(installed_copy(home))}


def hook_processes(claude_pid):
    listing = subprocess.run(["ps", "-axww", "-o", "pid=,ppid=,command="], capture_output=True, text=True,
                             check=True).stdout
    parents, commands = {}, {}
    for row in listing.splitlines():
        pid, ppid, command = row.strip().split(None, 2)
        parents[int(pid)], commands[int(pid)] = int(ppid), command

    def under_claude(pid):
        while pid in parents and pid > 1:
            pid = parents[pid]
            if pid == claude_pid:
                return True
        return False

    return [{"pid": pid, "command": command} for pid, command in commands.items()
            if "funmedal-bridge" in command and under_claude(pid)]


@pytest.mark.parametrize("case, way", [
    ("[OB-7] the plugin passes claude plugin validate --strict", "plugin"),
    ("[OB-7] the marketplace passes claude plugin validate --strict", "marketplace"),
])
def test_validate(case, way, run_dir):
    target = PLUGIN if way == "plugin" else ROOT
    result = subprocess.run(["claude", "plugin", "validate", "--strict", "--json", str(target)], capture_output=True,
                            text=True, env=own_env(os.environ["HOME"]), timeout=60)
    (run_dir / "validate.json").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("case", ["[OB-7] the plugin hooks the nine events and runs the installed bridge, not a file "
                                  "in the repo"])
def test_plugin_hooks(case, run_dir):
    found = {name: [hook for group in groups for hook in group["hooks"]] for name, groups in plugin_hooks().items()}
    write_summary(run_dir, {"hooks": found})
    assert sorted(found) == sorted(HOOK_EVENTS), found
    for name, hooks in found.items():
        timeout = PERMISSION_HOOK_TIMEOUT_S if name == "PermissionRequest" else HOOK_TIMEOUT_S
        assert hooks == [{"type": "command", "command": INSTALLED_COMMAND, "timeout": timeout}], (name, hooks)


@pytest.mark.parametrize("case", ["[OB-7] with no bridge installed every plugin hook exits without blocking "
                                  "Claude Code"])
def test_hooks_without_installed_bridge(case, run_dir, tmp_path):
    runs = []
    for name, groups in plugin_hooks().items():
        for hook in (hook for group in groups for hook in group["hooks"]):
            result = subprocess.run(["/bin/sh", "-c", hook["command"]], input=json.dumps(event(name, "ob7")),
                                    capture_output=True, text=True, timeout=10,
                                    env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
            runs.append({"event": name, "exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
    write_summary(run_dir, {"runs": runs})
    assert sorted(r["event"] for r in runs) == sorted(HOOK_EVENTS), runs
    assert all(r["exit"] != BLOCKING_EXIT and r["stdout"] == "" for r in runs), runs


@pytest.mark.parametrize("case, way, version", [
    ("[OB-7] install.sh refuses a Claude Code older than 2.1.292: one patch behind", "patch", "2.1.291 (Claude Code)"),
    ("[OB-7] install.sh refuses a Claude Code older than 2.1.292: older minor with a larger patch", "minor",
     "2.0.999 (Claude Code)"),
    ("[OB-7] install.sh refuses a Claude Code older than 2.1.292: older major", "major", "1.9.400 (Claude Code)"),
    ("[OB-7] install.sh refuses a Claude Code older than 2.1.292: unreadable version", "unreadable",
     "error: something went wrong"),
    ("[OB-7] install.sh refuses a Claude Code older than 2.1.292: no claude on PATH", "missing", None),
])
def test_install_refuses_old_claude(case, way, version, run_dir, tmp_path):
    result = run_install(tmp_path, version)
    write_summary(run_dir, result)
    assert result["exit"] != 0, result
    assert NEEDED_VERSION in result["stderr"], result
    assert result["calls"] == ([] if version is None else ["--version"]), result
    assert result["installed_sha256"] is None, result


@pytest.mark.parametrize("case, way, version", [
    ("[OB-7] install.sh accepts Claude Code 2.1.292 and newer: the same version", "same", "2.1.292 (Claude Code)"),
    ("[OB-7] install.sh accepts Claude Code 2.1.292 and newer: a patch number with more digits", "digits",
     "2.1.1000 (Claude Code)"),
    ("[OB-7] install.sh accepts Claude Code 2.1.292 and newer: newer minor", "minor", "2.2.0 (Claude Code)"),
    ("[OB-7] install.sh accepts Claude Code 2.1.292 and newer: newer major", "major", "10.0.0 (Claude Code)"),
])
def test_install_accepts_new_claude(case, way, version, run_dir, tmp_path):
    require_bridge_app()
    result = run_install(tmp_path, version)
    write_summary(run_dir, result)
    assert result["exit"] == 0, result
    assert result["calls"] == ["--version", f"plugin marketplace add {ROOT}", f"plugin install {PLUGIN_ID}"], result
    assert result["installed_sha256"] == sha256(BRIDGE), result


CLAUDE_CHOICES = {
    "desktop-only": (None, {"2.1.280": "2.1.280 (Claude Code)", "2.1.299": "2.1.299 (Claude Code)"},
                     "desktop 2.1.299", []),
    "desktop-number-order": (None, {"2.1.999": "2.1.999 (Claude Code)", "2.1.1000": "2.1.1000 (Claude Code)"},
                             "desktop 2.1.1000", []),
    "desktop-newest-broken": (None, {"2.1.299": "2.1.299 (Claude Code)", "2.1.305": None}, "desktop 2.1.299", []),
    "terminal-old": ("2.1.291 (Claude Code)", {"2.1.299": "2.1.299 (Claude Code)"}, "desktop 2.1.299",
                     ["2.1.291", "claude update"]),
    "desktop-old": ("2.1.302 (Claude Code)", {"2.1.290": "2.1.290 (Claude Code)"}, TERMINAL,
                    ["2.1.290", "Check for Updates"]),
    "both-new": ("2.1.302 (Claude Code)", {"2.1.299": "2.1.299 (Claude Code)"}, TERMINAL, []),
    "dev-build": ("2.1.299 (Claude Code)", {"2.1.300": "2.1.300-dev.20261001.t101500 (Claude Code)"},
                  "desktop 2.1.300", []),
}


@pytest.mark.parametrize("case, way", [
    ("[OB-17] install.sh with only the Claude desktop app uses its newest bundled Claude Code and ignores older "
     "folders the app keeps", "desktop-only"),
    ("[OB-17] install.sh compares bundled versions by number, not as text", "desktop-number-order"),
    ("[OB-17] install.sh skips a bundled Claude Code that cannot report its version", "desktop-newest-broken"),
    ("[OB-17] install.sh with an old terminal claude installs through the desktop app and says the terminal one is "
     "too old", "terminal-old"),
    ("[OB-17] install.sh with an old desktop app installs through the terminal claude and says the desktop one is "
     "too old", "desktop-old"),
    ("[OB-17] install.sh with both new enough uses the newer one and warns about nothing", "both-new"),
    ("[OB-17] install.sh reads a version with a build suffix the same way doctor does", "dev-build"),
])
def test_install_picks_claude(case, way, run_dir, tmp_path):
    require_bridge_app()
    terminal, desktop, chosen, warned = CLAUDE_CHOICES[way]
    result = run_labeled(tmp_path, ROOT / "install.sh", terminal, desktop)
    write_summary(run_dir, result)
    assert result["exit"] == 0, result
    assert result["work_calls"] == [f"{chosen}: plugin marketplace add {ROOT}",
                                    f"{chosen}: plugin install {PLUGIN_ID}"], result
    assert result["installed_sha256"] == sha256(BRIDGE), result
    assert ("注意" in result["stdout"]) == bool(warned), result
    assert all(word in result["stdout"] for word in warned), result


@pytest.mark.parametrize("case, way, terminal, desktop, words", [
    ("[OB-17] install.sh refuses when the only Claude Code is a desktop app older than 2.1.292 and says how to update "
     "the app", "desktop-old", None, {"2.1.291": "2.1.291 (Claude Code)"}, ["2.1.291", "Check for Updates"]),
    ("[OB-17] install.sh refuses when both copies are older than 2.1.292 and says how to update each",
     "both-old", "2.1.200 (Claude Code)", {"2.1.291": "2.1.291 (Claude Code)"}, ["claude update", "Check for Updates"]),
    ("[OB-17] install.sh with no Claude Code at all tells a desktop app user to open a Code session first", "none",
     None, {}, ["桌面 App", "Code"]),
])
def test_install_refuses_without_new_claude(case, way, terminal, desktop, words, run_dir, tmp_path):
    result = run_labeled(tmp_path, ROOT / "install.sh", terminal, desktop)
    write_summary(run_dir, result)
    assert result["exit"] != 0, result
    assert NEEDED_VERSION in result["stderr"], result
    assert all(word in result["stderr"] for word in words), result
    assert result["work_calls"] == [], result
    assert result["installed_sha256"] is None, result


@pytest.mark.parametrize("case", ["[OB-7] install.sh stops when installing the plugin fails"])
def test_install_reports_failed_plugin_install(case, run_dir, tmp_path):
    require_bridge_app()
    result = run_install(tmp_path, f"{NEEDED_VERSION} (Claude Code)", fail=f"plugin install {PLUGIN_ID}")
    write_summary(run_dir, result)
    assert result["exit"] != 0, result
    assert result["calls"][-1] == f"plugin install {PLUGIN_ID}", result


@pytest.mark.parametrize("case", ["[OB-7] install.sh asks for build.sh when the bridge is not built"])
def test_install_needs_built_bridge(case, run_dir, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(ROOT / "install.sh", repo / "install.sh")
    (repo / "tools").mkdir()
    shutil.copy(ROOT / "tools" / "find-claude.sh", repo / "tools" / "find-claude.sh")
    result = run_install(tmp_path, f"{NEEDED_VERSION} (Claude Code)", script=repo / "install.sh")
    write_summary(run_dir, result)
    assert result["exit"] != 0, result
    assert "mac/bridge/build.sh" in result["stderr"], result
    assert result["calls"] == ["--version"], result
    assert result["installed_sha256"] is None, result


@pytest.mark.parametrize("case", ["[OB-7] install.sh installs the plugin from this repository with the real claude "
                                  "and can run again"])
def test_install_with_real_claude(case, run_dir, tmp_path):
    require_bridge_app()
    home = tmp_path / "home"
    home.mkdir()
    env = own_env(home)
    owner = [os.path.expanduser(f"~/{p}") for p in USER_FILES]
    before = {p: sha256(Path(p)) for p in owner}
    runs = []
    for _ in range(2):
        done = subprocess.run(["/bin/sh", str(ROOT / "install.sh")], env=env, capture_output=True, text=True,
                              timeout=120)
        runs.append({"exit": done.returncode, "stdout": done.stdout, "stderr": done.stderr})
    listed = subprocess.run(["claude", "plugin", "list", "--json"], env=env, capture_output=True, text=True, timeout=60)
    plugins = {p["id"]: p for p in json.loads(listed.stdout)} if listed.returncode == 0 else {}
    after = {p: sha256(Path(p)) for p in owner}
    summary = {"runs": runs, "plugin": plugins.get(PLUGIN_ID), "installed_sha256": sha256(installed_copy(home)),
               "built_sha256": sha256(BRIDGE), "owner_files_unchanged": before == after}
    write_summary(run_dir, summary)
    assert [r["exit"] for r in runs] == [0, 0], summary
    entry = summary["plugin"]
    assert entry is not None and entry["enabled"] and entry["scope"] == "user", summary
    assert entry["readFromFolder"] == str(PLUGIN), summary
    assert summary["installed_sha256"] == summary["built_sha256"], summary
    assert summary["owner_files_unchanged"], summary


@pytest.mark.parametrize("case", ["[OB-17] with no claude command, install.sh installs the plugin through the Claude "
                                  "desktop app's own Claude Code and can run again"])
def test_install_with_desktop_claude_only(case, run_dir, tmp_path):
    require_bridge_app()
    app_bundle, version = require_desktop_claude()
    home = tmp_path / "home"
    home.mkdir()
    linked = link_desktop_claude(home, app_bundle, version)
    env = own_env(home) | {"PATH": "/usr/bin:/bin"}
    owner = [os.path.expanduser(f"~/{p}") for p in USER_FILES]
    before = {p: sha256(Path(p)) for p in owner}
    runs = []
    for _ in range(2):
        done = subprocess.run(["/bin/sh", str(ROOT / "install.sh")], env=env, capture_output=True, text=True,
                              timeout=120)
        runs.append({"exit": done.returncode, "stdout": done.stdout, "stderr": done.stderr})
    listed = subprocess.run([str(linked), "plugin", "list", "--json"], env=env, capture_output=True, text=True,
                            timeout=60)
    plugins = {p["id"]: p for p in json.loads(listed.stdout)} if listed.returncode == 0 else {}
    after = {p: sha256(Path(p)) for p in owner}
    summary = {"desktop_claude": str(app_bundle), "version": version, "runs": runs, "plugin": plugins.get(PLUGIN_ID),
               "installed_sha256": sha256(installed_copy(home)), "built_sha256": sha256(BRIDGE),
               "settings": json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8")),
               "owner_files_unchanged": before == after}
    write_summary(run_dir, summary)
    assert shutil.which("claude", path=env["PATH"]) is None, summary
    assert [r["exit"] for r in runs] == [0, 0], summary
    entry = summary["plugin"]
    assert entry is not None and entry["enabled"] and entry["scope"] == "user", summary
    assert entry["readFromFolder"] == str(PLUGIN), summary
    assert summary["settings"]["enabledPlugins"] == {PLUGIN_ID: True}, summary
    assert summary["installed_sha256"] == summary["built_sha256"], summary
    assert summary["owner_files_unchanged"], summary


@pytest.mark.parametrize("case", ["[OB-7] a session whose only hooks come from the plugin shows its states and takes "
                                  "a card approval"])
def test_plugin_session(case, card, bridge, run_dir):
    summary = {}
    try:
        with real_session(bridge, run_dir) as (session, marker):
            settings = json.loads((run_dir / "settings.json").read_text(encoding="utf-8"))
            through_env, _ = bridge_cli("status", env={**os.environ, "FUNMEDAL_SOCKET": bridge.socket})
            wait_session_ready(session, bridge)
            start = card.mark()
            request_touch(session, marker)
            shown = wait_prompt(card, start, 120.0)
            session.wait_screen("Do you want to proceed", 10.0)
            waiting = hook_processes(session.proc.pid)
            press(card, "ok", "long")
            card.wait_for(lambda line: line == f"DECISION {shown['id']} allow", 5.0, shown["index"])
            wait_until(marker.exists, 30.0, "marker file")
            session.wait_screen("Allowed by PermissionRequest hook", 30.0)
            card.wait_for(lambda line: line == "STATE done", 90.0, start)
            session.exit()
            card.wait_for(lambda line: line == "STATE idle", 20.0, start)
            summary = {"argv": session.argv, "settings": settings, "waiting_hooks": waiting,
                       "status_through_env": json.loads(through_env.stdout) if through_env.returncode == 0 else None,
                       "bridge_pid": bridge.status()["pid"], "states": card_states(card, start),
                       "answers": [e for e in bridge.log_entries() if e.get("kind") == "answer"]}
    finally:
        write_summary(run_dir, summary)
    assert "hooks" not in summary["settings"], summary
    assert summary["argv"][1:3] == ["--setting-sources", "project"], summary
    assert summary["argv"][summary["argv"].index("--plugin-dir") + 1] == str(PLUGIN), summary
    assert summary["status_through_env"]["pid"] == summary["bridge_pid"], summary
    assert sorted(h["command"] for h in summary["waiting_hooks"]) == [f"{INSTALLED_BRIDGE} hook",
                                                                       f"{INSTALLED_BRIDGE} listen"], summary
    assert summary["states"] == ["working", "waiting", "working", "done", "idle"], summary
    assert [(a["decision"], a["why"]) for a in summary["answers"]] == [("allow", "card")], summary
