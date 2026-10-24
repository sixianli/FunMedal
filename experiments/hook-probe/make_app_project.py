import argparse
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVENTS = ["SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
          "PermissionRequest", "Notification", "Stop", "StopFailure"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=str(HERE / "app-project"))
    args = parser.parse_args()
    project = Path(args.dir).resolve()
    (project / ".claude").mkdir(parents=True, exist_ok=True)
    log = project / "hooks.jsonl"
    command = f"FM_HOOK_LOG={log} FM_PROBE_HOLD=0 python3 {HERE / 'probe.py'}"
    hooks = {name: [{"hooks": [{"type": "command", "command": command, "timeout": 30}]}] for name in EVENTS}
    (project / ".claude" / "settings.json").write_text(json.dumps({"hooks": hooks}, indent=2), encoding="utf-8")
    (project / "README.md").write_text("hook probe scratch project\n", encoding="utf-8")
    (project / ".gitignore").write_text("hooks.jsonl\n.claude/worktrees/\n", encoding="utf-8")
    if not (project / ".git").exists():
        subprocess.run(["git", "init", "-q", "--template="], cwd=project, check=True)
        subprocess.run(["git", "add", "."], cwd=project, check=True)
        subprocess.run(["git", "-c", "user.name=probe", "-c", "user.email=probe@localhost", "commit", "-qm", "init"],
                       cwd=project, check=True)
    print(project)


if __name__ == "__main__":
    main()
