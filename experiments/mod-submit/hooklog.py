import json
import os
import sys
import time


def main() -> int:
    out = os.environ.get("FUNMEDAL_MODTEST_DIR")
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    if not out:
        return 0
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    row = {
        "t": time.time(),
        "event": sys.argv[1],
        "ppid": os.getppid(),
        "claude_pid_env": os.environ.get("CLAUDE_PID"),
        "session_id": payload.get("session_id"),
        "source": payload.get("source"),
        "prompt": payload.get("prompt"),
        "tool_name": payload.get("tool_name"),
        "command": tool_input.get("command"),
        "transcript_path": payload.get("transcript_path"),
    }
    with open(os.path.join(out, "hooks.jsonl"), "a", encoding="utf-8") as log:
        log.write(json.dumps(row, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
