import json

import pytest

from bridge_harness import (artifacts, bash, bridge, card, card_rows, event, hook_env, require_bridge_app,  # noqa: F401
                            run_dir, write_summary)
from test_rows import capture_hook_messages

ARTIFACTS_PREFIX = "sc21"
ARTIFACTS_SUBDIR = "project-repo"


def folders(base):
    (base / ".git").mkdir()
    (base / "workspace2" / ".git").mkdir(parents=True)
    workspace = base / "workspace"
    child = workspace / "child-repo"
    (child / ".git").mkdir(parents=True)
    (child / "src" / "deep").mkdir(parents=True)
    (child / "nested-repo" / ".git").mkdir(parents=True)
    (workspace / "plain-folder").mkdir(parents=True)
    root = base / "rootrepo"
    (root / ".git" / "worktrees" / "w3").mkdir(parents=True)
    (root / "sub").mkdir(parents=True)
    worktree = root / ".claude" / "worktrees" / "w3"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: ../../../.git/worktrees/w3\n", encoding="utf-8")
    (base / "outside" / ".git").mkdir(parents=True)
    link = base / "ws-link"
    link.symlink_to(workspace)
    return {"workspace": workspace, "child": child, "root": root, "worktree": worktree, "outside": base / "outside",
            "link": link}


def at(name, session, cwd, **fields):
    return event(name, session, cwd=str(cwd), **fields)


@pytest.mark.parametrize("case", ["[SC-21] the project name is the nearest git repository above the hook's cwd, "
                                  "searched no higher than CLAUDE_PROJECT_DIR, on every event"])
def test_nearest_repository_names(case, artifacts, tmp_path):
    require_bridge_app()
    f = folders(tmp_path)
    ws, child, root = f["workspace"], f["child"], f["root"]
    cases = [
        (at("SessionStart", "p", ws, source="startup"), ws, "workspace"),
        (at("UserPromptSubmit", "p", child, prompt="x"), ws, "child-repo"),
        (at("PreToolUse", "p", child / "src" / "deep", **bash("ls")), ws, "child-repo"),
        (at("PostToolUse", "p", child / "nested-repo", tool_use_id="t", tool_response={}, **bash("ls")), ws,
         "nested-repo"),
        (at("Stop", "p", ws / "plain-folder", stop_hook_active=False), ws, "workspace"),
        (at("UserPromptSubmit", "p", f["outside"], prompt="x"), ws, "workspace"),
        (at("UserPromptSubmit", "p", tmp_path / "workspace2", prompt="x"), ws, "workspace"),
        (event("UserPromptSubmit", "p", cwd=f"{child}/src/../src/deep/", prompt="x"), ws, "child-repo"),
        (event("UserPromptSubmit", "p", cwd=f"{child}/../plain-folder", prompt="x"), ws, "workspace"),
        (at("UserPromptSubmit", "r", root / "sub", prompt="x"), root, "rootrepo"),
        (at("UserPromptSubmit", "r", root, prompt="x"), root, "rootrepo"),
        (at("UserPromptSubmit", "w", f["worktree"], prompt="x"), f["worktree"], "rootrepo"),
        (at("UserPromptSubmit", "l", f["link"] / "child-repo", prompt="x"), f["link"], "child-repo"),
        (at("UserPromptSubmit", "l", child / "src", prompt="x"), f["link"], "child-repo"),
        (at("UserPromptSubmit", "l", f["link"], prompt="x"), f["link"], "ws-link"),
    ]
    sent = capture_hook_messages([(payload, hook_env(project)) for payload, project, _ in cases])
    (artifacts / "project-names.json").write_text(json.dumps(sent, indent=2) + "\n", encoding="utf-8")
    assert [m.get("project") for m in sent] == [expected for _, _, expected in cases], sent


@pytest.mark.parametrize("case", ["[SC-21] a session started in a parent folder shows the repository it works in on "
                                  "the card, follows it when the work moves, and a session started at a repository "
                                  "root keeps its name"])
def test_card_shows_working_repository(case, card, bridge, run_dir, tmp_path):
    f = folders(tmp_path)
    ws, child, root = f["workspace"], f["child"], f["root"]
    bridge.start()
    bridge.hook(at("SessionStart", "a", ws, source="startup"), project=ws)
    bridge.hook(at("SessionStart", "b", root, source="startup"), project=root)
    bridge.hook(at("UserPromptSubmit", "a", child / "src", prompt="x"), project=ws)
    bridge.hook(at("UserPromptSubmit", "b", root / "sub", prompt="x"), project=root)
    first = bridge.settle_rows([("running", "child-repo"), ("running", "rootrepo")], total=2)
    first_card = card_rows(card)
    bridge.hook(at("PreToolUse", "a", ws / "plain-folder", **bash("ls")), project=ws)
    moved = bridge.settle_rows([("running", "workspace"), ("running", "rootrepo")], total=2)
    moved_card = card_rows(card)
    write_summary(run_dir, {"first": first, "first_card": first_card, "moved": moved, "moved_card": moved_card})
    assert [(r["state"], r["name"]) for r in first_card["rows"]] == \
        [("running", "child-repo"), ("running", "rootrepo")]
    assert [(r["state"], r["name"]) for r in moved_card["rows"]] == \
        [("running", "workspace"), ("running", "rootrepo")]
