import argparse
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tools"))
from claude_session import ClaudeSession

EVENTS = ["SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
          "PermissionRequest", "Notification", "Stop", "StopFailure"]
SAY_HI = "Reply with the single word hi and do nothing else."


def entries(log: Path) -> list:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def wait_event(log: Path, predicate, timeout: float, after: int = 0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = [e for e in entries(log)[after:] if predicate(e)]
        if found:
            return found[0]
        time.sleep(0.2)
    raise TimeoutError(f"no matching hook event within {timeout} s")


def is_start(name):
    return lambda e: e["event"] == name and e["phase"] == "start"


def write_settings(path: Path, permission_timeout: int) -> None:
    command = f"python3 {HERE / 'probe.py'}"
    hooks = {}
    for name in EVENTS:
        hook = {"type": "command", "command": command}
        if name == "PermissionRequest":
            hook["timeout"] = permission_timeout
        hooks[name] = [{"hooks": [hook]}]
    path.write_text(json.dumps({"sandbox": {"enabled": False}, "hooks": hooks}, indent=2), encoding="utf-8")


def scenario_ask(s, log):
    mark = len(entries(log))
    s.type_line("Use the AskUserQuestion tool exactly once to ask me which color I prefer, with the two options "
                "Red and Blue. Do nothing else.", "enter (prompt)")
    wait_event(log, lambda e: e["event"] == "PreToolUse" and e["tool_name"] == "AskUserQuestion", 120, mark)
    s.note("ask tool started")
    time.sleep(10)
    s.send("\r", "enter (pick first option)")
    time.sleep(1.0)
    s.send("\r", "enter (submit answers)")
    wait_event(log, is_start("Stop"), 120, mark)


def scenario_clear(s, log):
    mark = len(entries(log))
    s.type_line(SAY_HI, "enter (first prompt)")
    wait_event(log, is_start("Stop"), 120, mark)
    s.wait_quiet(2.0, 30)
    mark = len(entries(log))
    s.type_line("/clear", "enter (/clear)")
    time.sleep(5)
    s.type_line(SAY_HI, "enter (second prompt)")
    wait_event(log, is_start("Stop"), 120, mark)


def make_kill(sig):
    def run(s, log):
        mark = len(entries(log))
        s.type_line(SAY_HI, "enter (prompt)")
        wait_event(log, is_start("Stop"), 120, mark)
        s.wait_quiet(2.0, 30)
        s.proc.send_signal(sig)
        s.note("signal sent", signal=int(sig))
        time.sleep(8)
    return run


def scenario_timeout(s, log, marker: Path):
    mark = len(entries(log))
    s.type_line(f"Run exactly this one shell command with the Bash tool and nothing else: touch {marker}  "
                f"Then reply with the single word done.", "enter (prompt)")
    wait_event(log, is_start("PermissionRequest"), 120, mark)
    s.note("permission hook started")
    wait_event(log, lambda e: e["event"] == "PermissionRequest" and e["phase"] in ("signal", "end"), 700, mark)
    s.note("permission hook ended")
    time.sleep(15)
    s.note("marker before answering", exists=marker.exists())
    s.send("1", "1 (yes in terminal)")
    time.sleep(0.3)
    s.send("\r", "enter (confirm)")
    deadline = time.time() + 30
    while time.time() < deadline and not marker.exists():
        time.sleep(0.2)
    s.note("marker after answering", exists=marker.exists())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=["ask", "clear", "kill-term", "kill-hup", "kill-kill", "timeout"])
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    if not (workdir / ".git").exists():
        subprocess.run(["git", "init", "-q", str(workdir)], check=True)
    log = out / "hooks.jsonl"
    log.unlink(missing_ok=True)
    marker = workdir.parent / f"{workdir.name}-marker.txt"
    marker.unlink(missing_ok=True)
    timeout_run = args.scenario == "timeout"
    settings = out / "settings.json"
    write_settings(settings, 600 if timeout_run else 5)
    env = {"FM_HOOK_LOG": str(log), "FM_PROBE_HOLD": "900" if timeout_run else "0"}
    session = ClaudeSession(workdir, settings, out, env=env)
    session.start()
    result = {"scenario": args.scenario, "claude_version": subprocess.run(["claude", "--version"], capture_output=True,
                                                                          text=True).stdout.strip()}
    try:
        session.pass_startup(lambda: any(e["event"] == "SessionStart" for e in entries(log)), timeout=90)
        runs = {"ask": scenario_ask, "clear": scenario_clear, "kill-term": make_kill(signal.SIGTERM),
                "kill-hup": make_kill(signal.SIGHUP), "kill-kill": make_kill(signal.SIGKILL)}
        if timeout_run:
            scenario_timeout(session, log, marker)
        else:
            runs[args.scenario](session, log)
        result["completed"] = True
    except Exception as error:
        result["completed"] = False
        result["error"] = repr(error)
    finally:
        session.exit()
        session.close()
    start = session.start_time
    result["hooks"] = [{**e, "t_rel": round(e["t"] - start, 2)} for e in entries(log)]
    result["timeline"] = [{**e, "t_rel": round(e["t"] - start, 2)} for e in session.timeline]
    (out / "summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"scenario": args.scenario, "completed": result["completed"], "error": result.get("error"),
                      "hooks": len(result["hooks"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
