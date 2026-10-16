import argparse
import fcntl
import json
import os
import pty
import re
import select
import signal
import struct
import termios
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CURSOR_FORWARD = re.compile(rb"\x1b\[(\d*)C")
ESCAPES = re.compile(rb"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[@-Z\\-_]")
DIALOG = "Do you want to proceed"
TRUST = "trust"
INHERITED_PREFIXES = ("CLAUDE", "ANTHROPIC", "GROK_", "USE_LOCAL_OAUTH", "USE_STAGING_OAUTH", "MCP_", "OTEL_")

SCENARIOS = {
    "wait-allow": {"wait": 20, "decision": "allow", "answer_at": None},
    "wait-then-terminal-yes": {"wait": 20, "decision": "allow", "answer_at": 6},
    "no-decision": {"wait": 0, "decision": "none", "answer_at": 4},
    "wait-deny": {"wait": 10, "decision": "deny", "answer_at": None},
    "terminal-yes-then-hook-deny": {"wait": 12, "decision": "deny", "answer_at": 3, "linger": 20},
}


def plain(data: bytes) -> str:
    data = CURSOR_FORWARD.sub(lambda m: b" " * int(m.group(1) or 1), data)
    return ESCAPES.sub(b"", data).decode("utf-8", "replace")


def hook_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=sorted(SCENARIOS))
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--model", default="haiku")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()

    scenario = SCENARIOS[args.scenario]
    workdir = Path(args.workdir).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)
    marker = workdir.parent / "hook-markers" / f"{args.scenario}-done.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.unlink(missing_ok=True)
    hook_log = out / "hook.jsonl"
    hook_log.unlink(missing_ok=True)

    probe = f"python3 {HERE / 'hook_probe.py'}"
    settings = {
        "sandbox": {"enabled": False},
        "hooks": {
            "PermissionRequest": [{"matcher": "Bash", "hooks": [
                {"type": "command", "command": f"{probe} permission", "timeout": 60}]}],
            "Notification": [{"hooks": [{"type": "command", "command": f"{probe} notification"}]}],
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": f"{probe} pretooluse"}]}],
            "PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": f"{probe} posttooluse"}]}],
        }
    }
    settings_path = out / "settings.json"
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")

    env = {k: v for k, v in os.environ.items() if not k.startswith(INHERITED_PREFIXES)}
    env.update({"FM_HOOK_LOG": str(hook_log), "FM_HOOK_WAIT": str(scenario["wait"]),
                "FM_HOOK_DECISION": scenario["decision"], "TERM": "xterm-256color"})
    argv = ["claude", "--settings", str(settings_path), "--model", args.model, "--permission-mode", "default"]

    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(workdir)
        os.execvpe(argv[0], argv, env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))

    start = time.time()
    raw = open(out / "terminal.raw", "wb", buffering=0)
    timeline: list[dict] = []
    text = ""
    seen_trust = False
    sent_prompt = False
    last_output = time.time()
    answered = False
    dialog_first = None
    marker_time = None

    def note(event: str, **fields) -> None:
        timeline.append({"t": round(time.time() - start, 3), "event": event, **fields})

    def send(keys: str, label: str) -> None:
        os.write(fd, keys.encode())
        note("sent", keys=label)

    prompt = (f"Run exactly this one shell command with the Bash tool and nothing else: "
              f"touch {marker}  Then reply with the single word done.")

    while time.time() - start < args.timeout:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                note("pty closed")
                break
            if not chunk:
                break
            raw.write(struct.pack(">dI", time.time() - start, len(chunk)) + chunk)
            text += plain(chunk)
            last_output = time.time()
        quiet = time.time() - last_output
        if not seen_trust and TRUST in text.lower() and "folder" in text.lower():
            seen_trust = True
            note("trust dialog seen")
            time.sleep(0.5)
            send("\r", "enter (trust)")
            continue
        if not sent_prompt and time.time() - start > 6 and quiet > 2.5:
            sent_prompt = True
            os.write(fd, prompt.encode())
            time.sleep(0.5)
            send("\r", "enter (prompt)")
            note("prompt sent")
            continue
        if sent_prompt and dialog_first is None and DIALOG in text:
            dialog_first = round(time.time() - start, 3)
            note("permission dialog seen")
        events = hook_events(hook_log)
        perm_start = next((e for e in events if e["event"] == "start" and e["kind"] == "permission"), None)
        if (scenario["answer_at"] is not None and not answered and perm_start
                and time.time() - perm_start["t"] >= scenario["answer_at"]):
            answered = True
            send("1", "1 (yes in terminal)")
            time.sleep(0.3)
            send("\r", "enter (confirm)")
        if marker_time is None and marker.exists():
            marker_time = round(time.time() - start, 3)
            note("marker file created")
        if marker_time is not None and time.time() - start - marker_time > scenario.get("linger", 8):
            break
        if sent_prompt and any(e["event"] == "end" and e.get("decision") == "deny" for e in events):
            if time.time() - max(e["t"] for e in events) > 10:
                break

    def drain(seconds: float) -> None:
        nonlocal text
        until = time.time() + seconds
        while time.time() < until:
            ready, _, _ = select.select([fd], [], [], 0.1)
            if not ready:
                continue
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            raw.write(struct.pack(">dI", time.time() - start, len(chunk)) + chunk)
            text += plain(chunk)

    for keys in ("\x03", "\x03"):
        try:
            send(keys, "ctrl-c")
        except OSError:
            break
        drain(1.0)
    exit_status = None
    for sig in (None, signal.SIGTERM, signal.SIGKILL):
        if sig is not None:
            try:
                os.kill(pid, sig)
                note("signal", signal=int(sig))
            except ProcessLookupError:
                pass
        drain(2.0)
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            exit_status = status
            break
    os.close(fd)
    if exit_status is None:
        exit_status = os.waitpid(pid, 0)[1]
    raw.close()
    (out / "terminal.txt").write_text(text, encoding="utf-8")

    events = hook_events(hook_log)
    for e in events:
        e["t_rel"] = round(e["t"] - start, 3)
        if isinstance(e.get("input"), dict):
            e["input"] = {k: e["input"][k] for k in ("hook_event_name", "tool_name", "tool_input",
                                                     "notification_type", "message", "permission_suggestions")
                          if k in e["input"]}
    summary = {
        "scenario": args.scenario,
        "config": scenario,
        "argv": argv,
        "claude_version": os.popen("claude --version").read().strip(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(start)),
        "permission_dialog_first_seen_s": dialog_first,
        "marker_created_s": marker_time,
        "exit_status": exit_status,
        "timeline": timeline,
        "hook_events": events,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("scenario", "permission_dialog_first_seen_s", "marker_created_s")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
