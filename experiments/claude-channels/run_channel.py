import argparse
import fcntl
import json
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "claude-hooks"))
from drive import INHERITED_PREFIXES, plain  # noqa: E402

SCENARIOS = {
    "push-allow": {"protocol": "2025-06-18", "decision": "allow", "wait": 5, "answer_at": None},
    "push-deny": {"protocol": "2025-06-18", "decision": "deny", "wait": 5, "answer_at": None},
    "push-terminal-first": {"protocol": "2025-06-18", "decision": "allow", "wait": 15, "answer_at": 3, "linger": 20},
}
SCREEN_MARKS = {
    "trust dialog": "trustthisfolder",
    "development channel dialog": "localdevelopment",
    "channel notice": "injectdirectlyinthissession",
    "permission dialog": "Doyouwanttoproceed",
}


DOWN = "\x1b[B"
DIALOG_KEYS = {
    "trust dialog": [(DOWN, "down"), ("\r", "enter")],
    "development channel dialog": [("\r", "enter")],
}


def squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text)


def channel_log(path: Path) -> list[dict]:
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
    control = workdir.parent / "channel-control" / args.scenario
    control.mkdir(parents=True, exist_ok=True)
    (control / "push.json").unlink(missing_ok=True)
    marker = workdir.parent / "channel-markers" / f"{args.scenario}-done.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.unlink(missing_ok=True)
    log_path = out / "channel.jsonl"
    log_path.unlink(missing_ok=True)

    settings_path = out / "settings.json"
    settings_path.write_text(json.dumps({"sandbox": {"enabled": False}}, indent=2), encoding="utf-8")
    mcp_path = out / "mcp.json"
    mcp_path.write_text(json.dumps({"mcpServers": {"fmprobe": {
        "command": "python3",
        "args": [str(HERE / "channel_probe.py")],
        "env": {
            "FM_CHANNEL_LOG": str(log_path),
            "FM_CHANNEL_CONTROL": str(control),
            "FM_CHANNEL_PROTOCOL": scenario["protocol"],
            "FM_CHANNEL_PERMISSION_WAIT": str(scenario["wait"]),
            "FM_CHANNEL_PERMISSION_DECISION": scenario["decision"],
        },
    }}}, indent=2), encoding="utf-8")

    env = {k: v for k, v in os.environ.items() if not k.startswith(INHERITED_PREFIXES)}
    env["TERM"] = "xterm-256color"
    argv = ["claude", "--settings", str(settings_path), "--strict-mcp-config", "--mcp-config", str(mcp_path),
            "--dangerously-load-development-channels", "server:fmprobe",
            "--model", args.model, "--permission-mode", "default"]

    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(workdir)
        os.execvpe(argv[0], argv, env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))

    start = time.time()
    raw = open(out / "terminal.raw", "wb", buffering=0)
    timeline: list[dict] = []
    text = ""
    seen: dict[str, float] = {}
    handled: set[str] = set()
    pushed = False
    answered = False
    marker_time = None
    last_output = time.time()

    def note(event: str, **fields) -> None:
        timeline.append({"t": round(time.time() - start, 3), "event": event, **fields})

    def send(keys: str, label: str) -> None:
        os.write(fd, keys.encode())
        note("sent", keys=label)

    def read_available(timeout: float) -> bool:
        nonlocal text, last_output
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return True
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            return False
        if not chunk:
            return False
        raw.write(struct.pack(">dI", time.time() - start, len(chunk)) + chunk)
        text += plain(chunk)
        last_output = time.time()
        flat = squeeze(text)
        for name, needle in SCREEN_MARKS.items():
            if name not in seen and needle in flat:
                seen[name] = round(time.time() - start, 3)
                note("screen", mark=name)
        return True

    content = (f"Please run exactly this one shell command with the Bash tool and nothing else: "
               f"touch {marker}  Then reply with the single word done.")

    while time.time() - start < args.timeout:
        if not read_available(0.2):
            note("pty closed")
            break
        quiet = time.time() - last_output
        for name, keys in DIALOG_KEYS.items():
            if name in seen and name not in handled and quiet > 1.0:
                handled.add(name)
                for key, label in keys:
                    send(key, f"{label} ({name})")
                    time.sleep(0.3)
        events = channel_log(log_path)
        connected = any(e["dir"] == "in" and e["msg"].get("method") == "notifications/initialized" for e in events)
        if not pushed and connected and quiet > 3.0:
            pushed = True
            (control / "push.json").write_text(json.dumps({"content": content}), encoding="utf-8")
            note("push requested")
        request = next((e for e in events if e["dir"] == "in"
                        and e["msg"].get("method") == "notifications/claude/channel/permission_request"), None)
        if (scenario["answer_at"] is not None and not answered and request
                and time.time() - request["t"] >= scenario["answer_at"]):
            answered = True
            send("1", "1 (yes in terminal)")
            time.sleep(0.3)
            send("\r", "enter (confirm)")
        if marker_time is None and marker.exists():
            marker_time = round(time.time() - start, 3)
            note("marker file created")
        if marker_time is not None and time.time() - start - marker_time > scenario.get("linger", 8):
            break
        verdicts = [e for e in events if e["dir"] == "out"
                    and e["msg"].get("method") == "notifications/claude/channel/permission"]
        if verdicts and marker_time is None and time.time() - verdicts[-1]["t"] > 15:
            break
        if not request and time.time() - start > 75:
            break

    for keys in ("\x03", "\x03"):
        try:
            send(keys, "ctrl-c")
        except OSError:
            break
        until = time.time() + 1.0
        while time.time() < until and read_available(0.1):
            pass
    exit_status = None
    for sig in (None, signal.SIGTERM, signal.SIGKILL):
        if sig is not None:
            try:
                os.kill(pid, sig)
                note("signal", signal=int(sig))
            except ProcessLookupError:
                pass
        until = time.time() + 2.0
        while time.time() < until and read_available(0.1):
            pass
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            exit_status = status
            break
    os.close(fd)
    if exit_status is None:
        exit_status = os.waitpid(pid, 0)[1]
    raw.close()
    (out / "terminal.txt").write_text(text, encoding="utf-8")

    events = channel_log(log_path)
    for e in events:
        e["t_rel"] = round(e["t"] - start, 3)
    summary = {
        "scenario": args.scenario,
        "config": scenario,
        "argv": argv,
        "claude_version": os.popen("claude --version").read().strip(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(start)),
        "screen_first_seen_s": seen,
        "marker_created_s": marker_time,
        "exit_status": exit_status,
        "timeline": timeline,
        "channel_events": [{"t": e["t_rel"], "dir": e["dir"],
                            "method": e["msg"].get("method") if isinstance(e["msg"], dict) else None,
                            "msg": e["msg"]} for e in events],
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("scenario", "screen_first_seen_s", "marker_created_s")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
