import json
import os
import signal
import subprocess
import sys
import time

LOG_FALLBACK = os.path.expanduser("~/Library/Logs/funmedal-hook-probe.jsonl")


def process_line(pid: int) -> dict:
    out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    if not out:
        return {"pid": pid}
    ppid, _, comm = out.partition(" ")
    return {"pid": pid, "ppid": int(ppid), "comm": comm.strip()}


def ancestry(pid: int, depth: int = 4) -> list:
    chain = []
    for _ in range(depth):
        info = process_line(pid)
        chain.append(info)
        if "ppid" not in info or info["ppid"] <= 1:
            break
        pid = info["ppid"]
    return chain


def record(entry: dict) -> None:
    path = os.environ.get("FM_HOOK_LOG", LOG_FALLBACK)
    with open(path, "a", encoding="utf-8") as log:
        log.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> int:
    payload = json.load(sys.stdin)
    started = time.time()
    base = {
        "event": payload.get("hook_event_name"),
        "session_id": payload.get("session_id"),
        "source": payload.get("source"),
        "reason": payload.get("reason"),
        "tool_name": payload.get("tool_name"),
        "tool_use_id": payload.get("tool_use_id"),
        "notification_type": payload.get("notification_type"),
        "cwd": payload.get("cwd"),
        "project_dir": os.environ.get("CLAUDE_PROJECT_DIR"),
        "parents": ancestry(os.getppid()),
        "keys": sorted(payload),
    }

    def on_signal(signum, frame):
        record({**base, "phase": "signal", "signal": signum, "t": time.time(), "alive_s": round(time.time() - started, 2)})
        sys.exit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, on_signal)
    record({**base, "phase": "start", "t": started})
    if base["event"] == "PermissionRequest":
        hold = float(os.environ.get("FM_PROBE_HOLD", "0"))
        deadline = started + hold
        while time.time() < deadline:
            time.sleep(0.2)
        record({**base, "phase": "end", "t": time.time(), "alive_s": round(time.time() - started, 2)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
