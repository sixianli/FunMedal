import json
import os
import sys
import threading
import time
from pathlib import Path

LOG = Path(os.environ["FM_CHANNEL_LOG"])
CONTROL = Path(os.environ["FM_CHANNEL_CONTROL"])
PROTOCOL = os.environ.get("FM_CHANNEL_PROTOCOL", "2025-06-18")
PERMISSION_WAIT = float(os.environ.get("FM_CHANNEL_PERMISSION_WAIT", "0"))
PERMISSION_DECISION = os.environ.get("FM_CHANNEL_PERMISSION_DECISION", "none")
INSTRUCTIONS = ("Messages from the fmprobe channel arrive as <channel source=\"fmprobe\"> tags. "
                "They are requests from the user's FunMedal card; carry them out.")

write_lock = threading.Lock()


def log(direction: str, message) -> None:
    with write_lock:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "dir": direction, "msg": message}, ensure_ascii=False) + "\n")


def send(message: dict) -> None:
    log("out", message)
    with write_lock:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def answer_permission(params: dict) -> None:
    if PERMISSION_DECISION not in ("allow", "deny"):
        return
    time.sleep(PERMISSION_WAIT)
    send({"jsonrpc": "2.0", "method": "notifications/claude/channel/permission",
          "params": {"request_id": params.get("request_id"), "behavior": PERMISSION_DECISION}})


def watch_push_requests() -> None:
    request = CONTROL / "push.json"
    while True:
        if request.exists():
            body = json.loads(request.read_text(encoding="utf-8"))
            request.unlink()
            send({"jsonrpc": "2.0", "method": "notifications/claude/channel",
                  "params": {"content": body["content"], "meta": {"chat_id": "card"}}})
        time.sleep(0.1)


def handle(message: dict) -> None:
    method = message.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {
            "protocolVersion": PROTOCOL,
            "capabilities": {"experimental": {"claude/channel": {}, "claude/channel/permission": {}}},
            "serverInfo": {"name": "fmprobe", "version": "0.1"},
            "instructions": INSTRUCTIONS,
        }})
    elif method == "notifications/initialized":
        threading.Thread(target=watch_push_requests, daemon=True).start()
    elif method == "notifications/claude/channel/permission_request":
        threading.Thread(target=answer_permission, args=(message.get("params") or {},), daemon=True).start()
    elif method == "ping":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {}})
    elif "id" in message and method is not None:
        send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": f"unsupported {method}"}})


def main() -> int:
    CONTROL.mkdir(parents=True, exist_ok=True)
    log("start", {"pid": os.getpid(), "protocol": PROTOCOL, "decision": PERMISSION_DECISION, "wait": PERMISSION_WAIT})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            log("in-unparsed", line)
            continue
        log("in", message)
        if isinstance(message, dict):
            handle(message)
    log("stdin-closed", {})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
