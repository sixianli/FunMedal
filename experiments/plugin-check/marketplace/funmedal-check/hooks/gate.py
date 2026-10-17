import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def owner_of(out: Path):
    try:
        return json.loads((out / "owner.json").read_text(encoding="utf-8"))["session_id"]
    except (OSError, ValueError, KeyError):
        return None


def claim(out: Path, data: dict, cwd_prefix: str) -> None:
    if not (out / "armed").exists() or not str(data.get("cwd", "")).startswith(cwd_prefix):
        return
    try:
        with open(out / "owner.json", "x", encoding="utf-8") as f:
            json.dump({"session_id": data.get("session_id"), "t": time.time(), "source": data.get("source"),
                       "claude_pid": os.getppid()}, f)
    except FileExistsError:
        pass


def main() -> int:
    raw = sys.stdin.buffer.read()
    try:
        cfg = json.loads((HERE.parent / "check.json").read_text(encoding="utf-8"))
        base = Path(cfg["base"])
        out = base / (base / "phase.txt").read_text(encoding="utf-8").strip()
        data = json.loads(raw)
    except (OSError, ValueError, KeyError):
        return 0
    event = data.get("hook_event_name")
    if event == "SessionStart":
        claim(out, data, cfg["cwd_prefix"])
    if data.get("session_id") != owner_of(out):
        return 0
    start = time.time()
    env = {**os.environ, "CLAUDE_PID": str(os.getppid())}
    done = subprocess.run([cfg["bridge"], "hook"], input=raw, capture_output=True, env=env)
    row = {"t": start, "event": event, "claude_pid": os.getppid(), "ms": round((time.time() - start) * 1000),
           "exit": done.returncode, "stdout": done.stdout.decode(errors="replace")[:300]}
    with open(out / "gate.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    sys.stdout.buffer.write(done.stdout)
    sys.stderr.buffer.write(done.stderr)
    return done.returncode


if __name__ == "__main__":
    sys.exit(main())
