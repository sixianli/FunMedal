import argparse
import fcntl
import json
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PLUGIN = HERE / "plugin"
PLUGIN_NAME = "funmedal-mod-submit"
sys.path.insert(0, str(HERE.parent / "claude-hooks"))
from drive import INHERITED_PREFIXES, plain  # noqa: E402

BUSY_SECONDS = 20
DIALOG_WATCH_SECONDS = 15
SAFE_WATCH_SECONDS = 20


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def mod_events(out: Path) -> list[dict]:
    folder = out / "mod-events"
    if not folder.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


def first(rows: list[dict], after: float = 0.0, **match) -> dict | None:
    for row in rows:
        if row.get("t", 0) < after:
            continue
        if all(row.get(k) == v if not callable(v) else v(row.get(k)) for k, v in match.items()):
            return row
    return None


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Session:
    def __init__(self, out: Path, name: str, model: str, extra: list[str]):
        self.out = out
        self.raw = open(out / f"{name}-terminal.raw", "wb", buffering=0)
        self.text_path = out / f"{name}-terminal.txt"
        self.text = ""
        env = {k: v for k, v in os.environ.items() if not k.startswith(INHERITED_PREFIXES)}
        env.update({"TERM": "xterm-256color", "FUNMEDAL_MODTEST_DIR": str(out)})
        self.argv = ["claude", "--plugin-dir", str(PLUGIN), "--model", model, *extra]
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(REPO)
            os.execvpe(self.argv[0], self.argv, env)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 160, 0, 0))
        self.open = True
        self.exit_status = None

    def pump(self, seconds: float) -> None:
        end = time.time() + seconds
        while self.open:
            left = end - time.time()
            if left <= 0:
                return
            ready, _, _ = select.select([self.fd], [], [], min(left, 0.2))
            if not ready:
                continue
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                self.open = False
                return
            self.raw.write(chunk)
            self.text += plain(chunk)
        time.sleep(max(0.0, end - time.time()))

    def wait_for(self, check, timeout: float):
        end = time.time() + timeout
        while time.time() < end:
            value = check()
            if value:
                return value
            self.pump(0.3)
        return check()

    def type(self, text: str) -> None:
        os.write(self.fd, text.encode())
        self.pump(0.8)
        os.write(self.fd, b"\r")
        self.pump(0.5)

    def key(self, data: bytes) -> None:
        os.write(self.fd, data)
        self.pump(0.5)

    def close(self) -> None:
        if self.open:
            for data in (b"\x03", b"\x03"):
                try:
                    os.write(self.fd, data)
                except OSError:
                    break
                self.pump(1.0)
        for sig in (None, signal.SIGTERM, signal.SIGKILL):
            if sig is not None:
                try:
                    os.kill(self.pid, sig)
                except ProcessLookupError:
                    pass
            end = time.time() + 5
            while time.time() < end:
                done, status = os.waitpid(self.pid, os.WNOHANG)
                if done:
                    self.exit_status = status
                    break
                self.pump(0.2)
            if self.exit_status is not None:
                break
        try:
            os.close(self.fd)
        except OSError:
            pass
        self.raw.close()
        self.text_path.write_text(self.text, encoding="utf-8")


def write_settings(out: Path) -> Path:
    def hook(event: str) -> list[dict]:
        return [{"hooks": [{"type": "command", "command": f"python3 {HERE / 'hooklog.py'} {event}"}]}]

    settings = {
        "sandbox": {"enabled": False},
        "hooks": {event: hook(event) for event in
                  ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest", "Stop")},
    }
    path = out / "settings.json"
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return path


def send(out: Path, text: str) -> float:
    sent = time.time()
    with open(out / "inbox.txt", "a", encoding="utf-8") as inbox:
        inbox.write(text + "\n")
    return sent


def resolved(out: Path, text: str):
    return lambda: first(mod_events(out), event="submit.resolved", text=text) or first(
        mod_events(out), event="submit.rejected", text=text)


def stop_after(out: Path, t: float):
    return lambda: first(read_jsonl(out / "hooks.jsonl"), after=t, event="Stop")


def rel(t, base):
    return None if t is None else round(t - base, 2)


def run_main(out: Path, model: str, hold: float, results: dict) -> None:
    settings = write_settings(out)
    (out / "mod-events").mkdir()
    (out / "inbox.txt").write_text("", encoding="utf-8")
    allow = "Bash(python3 experiments/mod-submit/busy.py:*)"
    session = Session(out, "main", model, ["--permission-mode", "default", "--settings", str(settings),
                                           "--allowedTools", allow])
    results["main_argv"] = session.argv
    base = time.time()

    def hooks() -> list[dict]:
        return read_jsonl(out / "hooks.jsonl")

    checks = results["checks"]
    observed = results["observed"]
    try:
        start = session.wait_for(lambda: first(mod_events(out), event="mod.start"), 90)
        listener = session.wait_for(lambda: (out / "listener.pid").exists(), 30)
        ready = session.wait_for(lambda: "❯" in session.text, 60)
        checks["mod_loads_and_listener_starts"] = bool(start and listener and ready)
        if not checks["mod_loads_and_listener_starts"]:
            return
        session.pump(2.0)
        listener_ppid, listener_pid = (int(x) for x in (out / "listener.pid").read_text().split())
        results["main_session_id"] = start.get("session_id")
        observed["mod_session"] = {k: start.get(k) for k in ("session_id", "cwd", "surfaces")}
        checks["mod_records_session_and_surface"] = bool(
            start.get("session_id") and "terminal" in (start.get("surfaces") or []))
        observed["listener"] = {"pid": listener_pid, "ppid": listener_ppid, "claude_pid": session.pid,
                                "ppid_is_claude": listener_ppid == session.pid,
                                "hook_ppids": sorted({r["ppid"] for r in hooks()}),
                                "hook_claude_pid_env": sorted({str(r["claude_pid_env"]) for r in hooks()})}

        text1 = "实验一：会话空闲时送入。请只回复：收到一"
        sent1 = send(out, text1)
        r1 = session.wait_for(resolved(out, text1), 60)
        s1 = session.wait_for(stop_after(out, r1["t"]), 120) if r1 else None
        ups1 = first(hooks(), event="UserPromptSubmit", prompt=text1)
        checks["idle_submit_starts_turn"] = bool(r1 and r1["event"] == "submit.resolved" and not r1["dropped"] and s1)
        observed["idle"] = {"resolve_delay_s": rel(r1 and r1["t"], sent1),
                            "user_prompt_submit_hook_fired": bool(ups1), "turn_finished": bool(s1)}

        busy_prompt = (f"请用 Bash 工具运行这一条命令：python3 experiments/mod-submit/busy.py {BUSY_SECONDS} "
                       f"。命令结束后只回复：睡完了")
        typed = time.time()
        session.type(busy_prompt)
        pre = session.wait_for(lambda: first(hooks(), after=typed, event="PreToolUse",
                                             command=lambda c: bool(c and "busy.py" in c)), 90)
        observed["busy"] = {"busy_tool_started": bool(pre)}
        if pre:
            text2 = "实验二：Claude 在干活时送入。请只回复：收到二"
            sent2 = send(out, text2)
            r2 = session.wait_for(resolved(out, text2), BUSY_SECONDS + 120)
            post = first(hooks(), after=pre["t"], event="PostToolUse")
            busy_stop = first(hooks(), after=pre["t"], event="Stop")
            ups2 = first(hooks(), event="UserPromptSubmit", prompt=text2)
            s2 = session.wait_for(stop_after(out, r2["t"]), 120) if r2 else None
            checks["busy_submit_waits_for_turn_end"] = bool(
                r2 and busy_stop and r2["t"] >= busy_stop["t"] - 0.5 and s2)
            observed["busy"].update({
                "busy_tool_finished_after_s": rel(post and post["t"], sent2),
                "busy_turn_stopped_after_s": rel(busy_stop and busy_stop["t"], sent2),
                "resolved_after_s": rel(r2 and r2["t"], sent2),
                "user_prompt_submit_after_s": rel(ups2 and ups2["t"], sent2),
            })
        else:
            checks["busy_submit_waits_for_turn_end"] = False

        marker = out / "approval-test.txt"
        typed = time.time()
        session.type(f"请用 Bash 工具运行这一条命令：touch {marker} 。命令结束后只回复：建好了")
        dialog = session.wait_for(lambda: first(hooks(), after=typed, event="PermissionRequest"), 90)
        observed["approval"] = {"dialog_shown": bool(dialog)}
        if dialog:
            session.wait_for(lambda: "Do you want" in session.text[-4000:] or "Doyouwant" in session.text[-4000:], 15)
            text3 = "实验三：批准框挂着时送入。请只回复：收到三"
            sent3 = send(out, text3)
            session.pump(DIALOG_WATCH_SECONDS)
            during = resolved(out, text3)()
            answered = time.time()
            session.key(b"\x1b")
            r3 = session.wait_for(resolved(out, text3), 90)
            s3 = session.wait_for(stop_after(out, r3["t"]), 120) if r3 else None
            checks["approval_submit_starts_after_dialog_answered"] = bool(r3 and r3["event"] == "submit.resolved" and s3)
            observed["approval"].update({
                "entered_while_dialog_open": bool(during),
                "watched_dialog_s": DIALOG_WATCH_SECONDS,
                "resolved_after_send_s": rel(r3 and r3["t"], sent3),
                "resolved_after_dialog_answered_s": rel(r3 and r3["t"], answered),
                "touch_ran": marker.exists(),
            })
            asked = first(mod_events(out), after=typed, event="tool.check", decision="ask")
            turn1 = first(mod_events(out), event="turn.start", text=text1)
            done1 = turn1 and first(mod_events(out), after=turn1["t"], event="turn.complete")
            checks["mod_records_turns_and_tool_checks"] = bool(asked and turn1 and done1)
            observed["approval"]["mod_saw_ask"] = bool(asked)
        else:
            checks["approval_submit_starts_after_dialog_answered"] = False

        typed = time.time()
        session.type("/clear")
        cleared = session.wait_for(lambda: first(hooks(), after=typed, event="SessionStart", source="clear"), 30)
        session.pump(2.0)
        text4 = "实验四：/clear 之后送入。请只回复：收到四"
        sent4 = send(out, text4)
        r4 = session.wait_for(resolved(out, text4), 60)
        s4 = session.wait_for(stop_after(out, r4["t"]), 120) if r4 else None
        observed["after_clear"] = {
            "clear_session_start_hook": bool(cleared),
            "mod_start_count": len([e for e in mod_events(out) if e["event"] == "mod.start"]),
            "listener_alive": alive(listener_pid),
            "entered": bool(r4 and r4["event"] == "submit.resolved"),
            "resolved_after_s": rel(r4 and r4["t"], sent4),
            "turn_finished": bool(s4),
        }

        session.wait_for(lambda: time.time() - start["t"] >= hold, hold + 5)
        text5 = f"实验五：会话开了 {int(hold)} 秒后送入。请只回复：收到五"
        alive5 = alive(listener_pid)
        sent5 = send(out, text5)
        r5 = session.wait_for(resolved(out, text5), 60)
        s5 = session.wait_for(stop_after(out, r5["t"]), 120) if r5 else None
        observed["after_hold"] = {
            "held_s": round(time.time() - start["t"], 1),
            "listener_alive": alive5,
            "entered": bool(r5 and r5["event"] == "submit.resolved"),
            "resolved_after_s": rel(r5 and r5["t"], sent5),
            "turn_finished": bool(s5),
        }
    finally:
        session.close()
        results["main_exit_status"] = session.exit_status
        pid_file = out / "listener.pid"
        if pid_file.exists():
            listener_pid = int(pid_file.read_text().split()[1])
            end = time.time() + 5
            while time.time() < end and alive(listener_pid):
                time.sleep(0.2)
            left = alive(listener_pid)
            observed["after_exit"] = {"listener_left_running": left}
            if left:
                os.kill(listener_pid, signal.SIGTERM)
        observed["mod_events"] = [{**e, "t": rel(e["t"], base)} for e in mod_events(out)]
        observed["hook_events"] = [{"t": rel(r["t"], base), "event": r["event"], "source": r["source"],
                                    "prompt": r["prompt"], "command": r["command"]} for r in hooks()]


def transcript_entries(out: Path, results: dict) -> None:
    paths = {r["transcript_path"] for r in read_jsonl(out / "hooks.jsonl") if r.get("transcript_path")}
    found = []
    for path in sorted(paths):
        p = Path(path)
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if "实验" in line and "请只回复" in line:
                entry = json.loads(line)
                found.append({"type": entry.get("type"), "names_plugin": PLUGIN_NAME in line,
                              "keys": sorted(entry.keys()), "entry": entry})
    (out / "transcript-entries.json").write_text(json.dumps(found, indent=2, ensure_ascii=False), encoding="utf-8")
    results["observed"]["transcript"] = {
        "entries": len(found),
        "entries_naming_plugin": sum(1 for f in found if f["names_plugin"]),
    }


def run_second(out: Path, model: str, results: dict) -> None:
    claimed_by = results.get("main_session_id")
    listener_before = (out / "listener.pid").read_text() if (out / "listener.pid").exists() else None
    session = Session(out, "second", model, ["--permission-mode", "default"])
    results["second_argv"] = session.argv
    try:
        skipped = session.wait_for(lambda: first(mod_events(out), event="mod.start.skipped"), 90)
        ready = session.wait_for(lambda: "❯" in session.text, 60)
        session.pump(2.0)
        text7 = "实验七：第二个会话不该收到这句。请只回复：收到七"
        send(out, text7)
        session.pump(SAFE_WATCH_SECONDS)
        called = first(mod_events(out), event="submit.called", text=text7)
        listener_after = (out / "listener.pid").read_text() if (out / "listener.pid").exists() else None
        results["checks"]["second_session_does_not_listen"] = bool(
            ready and skipped and skipped.get("owner") == claimed_by and not called
            and listener_after == listener_before)
        results["observed"]["second_session"] = {
            "ready": bool(ready), "skipped": bool(skipped), "owner": skipped and skipped.get("owner"),
            "own_session_id": skipped and skipped.get("session_id"), "submitted": bool(called),
            "listener_restarted": listener_after != listener_before, "watched_s": SAFE_WATCH_SECONDS + 2}
    finally:
        session.close()
        results["second_exit_status"] = session.exit_status


def run_safe(out: Path, model: str, results: dict) -> None:
    safe = out / "safe-mode"
    safe.mkdir()
    (safe / "mod-events").mkdir()
    (safe / "inbox.txt").write_text("", encoding="utf-8")
    session = Session(safe, "safe", model, ["--safe-mode", "--permission-mode", "default"])
    results["safe_argv"] = session.argv
    try:
        ready = session.wait_for(lambda: "❯" in session.text, 60)
        session.pump(5.0)
        send(safe, "实验六：安全模式下送入。请只回复：收到六")
        session.pump(SAFE_WATCH_SECONDS)
        events = mod_events(safe)
        results["checks"]["safe_mode_does_not_load_mod"] = bool(
            ready and not events and not (safe / "listener.pid").exists())
        results["observed"]["safe_mode"] = {"ready": bool(ready), "mod_events": len(events),
                                            "listener_started": (safe / "listener.pid").exists(),
                                            "watched_s": SAFE_WATCH_SECONDS + 5}
    finally:
        session.close()
        results["safe_exit_status"] = session.exit_status


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out")
    parser.add_argument("--model", default="claude-haiku-5-5")
    parser.add_argument("--hold", type=float, default=300)
    args = parser.parse_args()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out).resolve() if args.out else REPO / "artifacts" / f"mod-submit-{stamp}"
    out.mkdir(parents=True, exist_ok=False)
    results = {
        "rerun": "python3 experiments/mod-submit/run.py" + ("" if args.hold == 300 else f" --hold {args.hold:g}"),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "head": git("rev-parse", "HEAD"),
        "dirty": git("status", "--short").splitlines(),
        "claude_version": subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip(),
        "model": args.model,
        "hold_s": args.hold,
        "checks": {},
        "observed": {},
    }
    try:
        run_main(out, args.model, args.hold, results)
        transcript_entries(out, results)
        run_second(out, args.model, results)
        run_safe(out, args.model, results)
    finally:
        expected = ("mod_loads_and_listener_starts", "mod_records_session_and_surface", "idle_submit_starts_turn",
                    "busy_submit_waits_for_turn_end", "approval_submit_starts_after_dialog_answered",
                    "mod_records_turns_and_tool_checks", "second_session_does_not_listen",
                    "safe_mode_does_not_load_mod")
        results["passed"] = all(results["checks"].get(name) for name in expected)
        results["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        (out / "summary.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"out": str(out), "passed": results["passed"], "checks": results["checks"],
                      **{k: v for k, v in results["observed"].items() if k not in ("mod_events", "hook_events")}},
                     indent=2, ensure_ascii=False))
    return 0 if results["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
