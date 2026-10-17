import json
import os
import subprocess
from pathlib import Path

import pytest

from bridge_harness import ROOT, artifacts, require_bridge_app, run_dir, write_summary  # noqa: F401
from test_plugin import PLUGIN_ID, USER_FILES, installed_copy, own_env, sha256

ARTIFACTS_PREFIX = "ob12"
ARTIFACTS_SUBDIR = "uninstall"
MARKETPLACE = "funmedal"
UNINSTALL = ROOT / "uninstall.sh"
PLUGIN_LISTED = json.dumps([{"id": PLUGIN_ID, "scope": "user", "enabled": True}, {"id": "other@elsewhere"}])
MARKETPLACE_LISTED = json.dumps([{"name": MARKETPLACE, "source": "directory"}, {"name": "claude-plugins-official"}])
NOTHING_LISTED = json.dumps([{"id": "other@elsewhere"}])
OTHER_MARKETPLACES = json.dumps([{"name": "claude-plugins-official"}])
FAKE_CLAUDE = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_CLAUDE_LOG"
if [ -n "$FAKE_CLAUDE_FAIL" ] && [ "$*" = "$FAKE_CLAUDE_FAIL" ]; then exit 1; fi
if [ "$*" = "plugin list --json" ]; then printf '%s\\n' "$FAKE_CLAUDE_PLUGINS"; fi
if [ "$*" = "plugin marketplace list --json" ]; then printf '%s\\n' "$FAKE_CLAUDE_MARKETPLACES"; fi
exit 0
"""
LIST_CALLS = ["plugin list --json", "plugin marketplace list --json"]
REMOVE_CALLS = [f"plugin uninstall {PLUGIN_ID}", f"plugin marketplace remove {MARKETPLACE}"]


def run_uninstall(tmp_path, plugins=PLUGIN_LISTED, marketplaces=MARKETPLACE_LISTED, fail="", with_claude=True):
    home = tmp_path / "home"
    home.mkdir()
    tools = tmp_path / "bin"
    tools.mkdir()
    calls = tmp_path / "claude-calls.txt"
    if with_claude:
        fake = tools / "claude"
        fake.write_text(FAKE_CLAUDE, encoding="utf-8")
        fake.chmod(0o755)
    env = {"HOME": str(home), "PATH": f"{tools}:/usr/bin:/bin", "LC_CTYPE": "C.UTF-8", "FAKE_CLAUDE_LOG": str(calls),
           "FAKE_CLAUDE_PLUGINS": plugins, "FAKE_CLAUDE_MARKETPLACES": marketplaces, "FAKE_CLAUDE_FAIL": fail}
    result = subprocess.run(["/bin/sh", str(UNINSTALL)], env=env, capture_output=True, text=True, timeout=60)
    return {"home": str(home), "exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
            "calls": sorted(calls.read_text(encoding="utf-8").splitlines()) if calls.exists() else []}


def next_steps_in_order(stdout, home):
    quit_command = f'"{installed_copy(Path(home))}" quit'
    marks = [stdout.find("/reload-plugins"), stdout.find(quit_command), stdout.find("忽略")]
    return all(mark >= 0 for mark in marks) and marks == sorted(marks)


SITUATIONS = {
    "installed": (PLUGIN_LISTED, MARKETPLACE_LISTED, REMOVE_CALLS),
    "nothing": (NOTHING_LISTED, OTHER_MARKETPLACES, []),
    "marketplace-only": (NOTHING_LISTED, MARKETPLACE_LISTED, [f"plugin marketplace remove {MARKETPLACE}"]),
}


@pytest.mark.parametrize("case, way", [
    ("[OB-12] uninstall.sh removes the installed plugin and its marketplace, then lists the steps left to the user",
     "installed"),
    ("[OB-12] uninstall.sh run again with nothing installed removes nothing and still lists the steps left",
     "nothing"),
    ("[OB-12] uninstall.sh removes the marketplace left behind when the plugin is already gone", "marketplace-only"),
])
def test_uninstall_removes_what_is_there(case, way, run_dir, tmp_path):
    plugins, marketplaces, removes = SITUATIONS[way]
    result = run_uninstall(tmp_path, plugins=plugins, marketplaces=marketplaces)
    write_summary(run_dir, result)
    assert result["exit"] == 0, result
    assert result["calls"] == sorted(LIST_CALLS + removes), result
    assert next_steps_in_order(result["stdout"], result["home"]), result


FAILURES = {
    "plugin-list": ("plugin list --json", ["plugin list --json"]),
    "marketplace-list": ("plugin marketplace list --json", LIST_CALLS),
    "uninstall": (f"plugin uninstall {PLUGIN_ID}", LIST_CALLS + [f"plugin uninstall {PLUGIN_ID}"]),
}


@pytest.mark.parametrize("case, way", [
    ("[OB-12] uninstall.sh stops without removing anything when the plugin list cannot be read", "plugin-list"),
    ("[OB-12] uninstall.sh stops without removing anything when the marketplace list cannot be read",
     "marketplace-list"),
    ("[OB-12] uninstall.sh stops when uninstalling the plugin fails and keeps the marketplace", "uninstall"),
])
def test_uninstall_stops_on_failure(case, way, run_dir, tmp_path):
    fail, made = FAILURES[way]
    result = run_uninstall(tmp_path, fail=fail)
    write_summary(run_dir, result)
    assert result["exit"] != 0, result
    assert result["calls"] == sorted(made), result
    assert "/reload-plugins" not in result["stdout"], result


@pytest.mark.parametrize("case", ["[OB-12] uninstall.sh with no claude on PATH says so and fails"])
def test_uninstall_needs_claude(case, run_dir, tmp_path):
    result = run_uninstall(tmp_path, with_claude=False)
    write_summary(run_dir, result)
    assert result["exit"] != 0, result
    assert "claude" in result["stderr"], result
    assert result["calls"] == [], result


@pytest.mark.parametrize("case", ["[OB-12] with the real claude, uninstall.sh after install.sh leaves no trace of "
                                  "FunMedal in the settings and can run again"])
def test_uninstall_with_real_claude(case, run_dir, tmp_path):
    require_bridge_app()
    home = tmp_path / "home"
    home.mkdir()
    env = own_env(home)
    owner = [os.path.expanduser(f"~/{p}") for p in USER_FILES]
    before = {p: sha256(Path(p)) for p in owner}
    runs = []
    for script in ("install.sh", "uninstall.sh", "uninstall.sh"):
        done = subprocess.run(["/bin/sh", str(ROOT / script)], env=env, capture_output=True, text=True, timeout=120)
        runs.append({"script": script, "exit": done.returncode, "stdout": done.stdout, "stderr": done.stderr})
    plugins = subprocess.run(["claude", "plugin", "list", "--json"], env=env, capture_output=True, text=True,
                             timeout=60)
    marketplaces = subprocess.run(["claude", "plugin", "marketplace", "list", "--json"], env=env, capture_output=True,
                                  text=True, timeout=60)
    settings = {p: (home / p).read_text(encoding="utf-8") if (home / p).exists() else None for p in USER_FILES}
    after = {p: sha256(Path(p)) for p in owner}
    summary = {"runs": runs, "plugins": plugins.stdout, "plugins_exit": plugins.returncode,
               "marketplaces": marketplaces.stdout, "marketplaces_exit": marketplaces.returncode,
               "settings": settings, "owner_files_unchanged": before == after}
    write_summary(run_dir, summary)
    assert [r["exit"] for r in runs] == [0, 0, 0], summary
    assert all(next_steps_in_order(r["stdout"], home) for r in runs[1:]), summary
    assert summary["plugins_exit"] == 0 and summary["marketplaces_exit"] == 0, summary
    assert PLUGIN_ID not in [p["id"] for p in json.loads(summary["plugins"])], summary
    assert MARKETPLACE not in [m["name"] for m in json.loads(summary["marketplaces"])], summary
    assert summary["settings"][".claude/settings.json"] is not None, summary
    assert all("funmedal" not in (text or "").lower() for text in summary["settings"].values()), summary
    assert summary["owner_files_unchanged"], summary
