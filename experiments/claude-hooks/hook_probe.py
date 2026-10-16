import json
import os
import signal
import sys
import time


def record(event: str, **fields) -> None:
    entry = {"t": time.time(), "pid": os.getpid(), "event": event, **fields}
    with open(os.environ["FM_HOOK_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps(entry, ensure_ascii=False) + "\n")


def on_signal(signum, frame) -> None:
    record("signal", signal=signum)
    sys.exit(128 + signum)


def main() -> int:
    kind = sys.argv[1]
    payload = json.load(sys.stdin)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, on_signal)
    record("start", kind=kind, input=payload)
    if kind != "permission":
        return 0
    wait = float(os.environ.get("FM_HOOK_WAIT", "0"))
    decision = os.environ.get("FM_HOOK_DECISION", "none")
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(0.2)
    record("end", decision=decision)
    if decision in ("allow", "deny"):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                                 "decision": {"behavior": decision}}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
