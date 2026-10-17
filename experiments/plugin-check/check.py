import argparse
import fcntl
import hashlib
import json
import os
import pty
import select
import shutil
import signal
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
MARKET = HERE / "marketplace"
PLUGIN = MARKET / "funmedal-check"
PLUGIN_ID = "funmedal-check@funmedal-check"
APP = REPO / "mac/bridge/build/FunMedalBridge.app"
BRIDGE = APP / "Contents/MacOS/funmedal-bridge"
CLAUDE_HOME = Path.home() / ".claude"
WATCHED_FILES = [
    CLAUDE_HOME / "settings.json",
    CLAUDE_HOME / "plugins/known_marketplaces.json",
    CLAUDE_HOME / "plugins/installed_plugins.json",
]
WATCHED_DIRS = [CLAUDE_HOME / "plugins/cache", CLAUDE_HOME / "plugins/data", CLAUDE_HOME / "plugins/marketplaces"]
sys.path.insert(0, str(HERE.parent / "claude-hooks"))
from drive import INHERITED_PREFIXES, plain  # noqa: E402


def sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def snapshot() -> dict:
    return {
        "files": {str(p): sha256(p) for p in WATCHED_FILES},
        "dirs": {str(d): sorted(x.name for x in d.iterdir()) if d.exists() else None for d in WATCHED_DIRS},
        "settings": json.loads((CLAUDE_HOME / "settings.json").read_text(encoding="utf-8")),
    }


def settings_diff(before: dict, after: dict) -> dict:
    keys = sorted(set(before) | set(after))
    return {k: {"before": before.get(k), "after": after.get(k)} for k in keys if before.get(k) != after.get(k)}


def compare(before: dict, after: dict) -> dict:
    return {
        "files_changed": [p for p in before["files"] if before["files"][p] != after["files"].get(p)],
        "dirs_added": {d: sorted(set(after["dirs"][d] or []) - set(before["dirs"][d] or [])) for d in before["dirs"]},
        "dirs_removed": {d: sorted(set(before["dirs"][d] or []) - set(after["dirs"][d] or [])) for d in before["dirs"]},
        "settings_keys_changed": settings_diff(before["settings"], after["settings"]),
    }


def bridge_status() -> dict:
    done = subprocess.run([str(BRIDGE), "status"], capture_output=True, text=True, timeout=10)
    try:
        s = json.loads(done.stdout)
        return {"ok": bool(s.get("ok")), "pid": s.get("pid"), "card": s.get("card"), "sessions": s.get("sessions")}
    except ValueError:
        return {"ok": False, "text": (done.stdout + done.stderr).strip()[:200]}


def wait_until(check, timeout: float, step: float = 0.5):
    end = time.time() + timeout
    while time.time() < end:
        value = check()
        if value:
            return value
        time.sleep(step)
    return check()


def run_logged(argv: list[str], log: Path) -> int:
    done = subprocess.run(argv, capture_output=True, text=True, cwd=REPO)
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"$ {' '.join(argv)}\nexit={done.returncode}\n{done.stdout}{done.stderr}\n")
    return done.returncode


def mod_events(phase_dir: Path) -> list[dict]:
    folder = phase_dir / "mod-events"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))] if folder.exists() else []


def gate_rows(phase_dir: Path) -> list[dict]:
    path = phase_dir / "gate.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


def arm(base: Path, phase: str) -> Path:
    phase_dir = base / phase
    (phase_dir / "mod-events").mkdir(parents=True, exist_ok=True)
    (base / "phase.txt").write_text(phase + "\n", encoding="utf-8")
    (phase_dir / "armed").write_text(str(time.time()), encoding="utf-8")
    return phase_dir


def disarm(phase_dir: Path) -> None:
    (phase_dir / "armed").unlink(missing_ok=True)


def quit_bridge(log: Path) -> bool:
    if bridge_status()["ok"]:
        run_logged([str(BRIDGE), "quit"], log)
    return bool(wait_until(lambda: not bridge_status()["ok"], 10))


def cmd_prepare(args) -> int:
    base = Path(args.base)
    base.mkdir(parents=True, exist_ok=True)
    backup = Path(args.backup)
    backup.mkdir(parents=True, exist_ok=True)
    for p in WATCHED_FILES:
        if p.exists():
            shutil.copy2(p, backup / p.name)
    (base / "before.json").write_text(json.dumps(snapshot(), indent=2, ensure_ascii=False), encoding="utf-8")
    (PLUGIN / "check.json").write_text(json.dumps({"base": str(base), "bridge": str(BRIDGE), "app": str(APP), "cwd_prefix": str(REPO) + "/"}) + "\n",
                                        encoding="utf-8")
    (base / "phase.txt").write_text("none\n", encoding="utf-8")
    print(f"prepared {base}; backups in {backup}")
    return 0


def cmd_install(args) -> int:
    base = Path(args.base)
    log = base / "install.log"
    codes = [run_logged(["claude", "--version"], log),
             run_logged(["claude", "plugin", "marketplace", "add", str(MARKET)], log),
             run_logged(["claude", "plugin", "install", PLUGIN_ID], log),
             run_logged(["claude", "plugin", "list"], log)]
    before = json.loads((base / "before.json").read_text(encoding="utf-8"))
    diff = compare(before, snapshot())
    (base / "after-install.json").write_text(json.dumps(diff, indent=2, ensure_ascii=False), encoding="utf-8")
    print(log.read_text(encoding="utf-8"))
    print(json.dumps(diff, indent=2, ensure_ascii=False))
    return 0 if all(c == 0 for c in codes[:3]) else 1


class Terminal:
    def __init__(self, out: Path, argv: list[str]):
        self.raw = open(out / "terminal.raw", "wb", buffering=0)
        self.text = ""
        env = {k: v for k, v in os.environ.items() if not k.startswith(INHERITED_PREFIXES)}
        env["TERM"] = "xterm-256color"
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(REPO)
            os.execvpe(argv[0], argv, env)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 160, 0, 0))

    def pump(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([self.fd], [], [], min(max(end - time.time(), 0), 0.2))
            if not ready:
                continue
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            self.raw.write(chunk)
            self.text += plain(chunk)

    def close(self) -> None:
        for data in (b"\x03", b"\x03"):
            try:
                os.write(self.fd, data)
            except OSError:
                break
            self.pump(1.0)
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                done, _ = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                break
            if done:
                break
            os.kill(self.pid, sig)
            self.pump(2.0)


def cmd_terminal(args) -> int:
    base = Path(args.base)
    log = base / "terminal.log"
    stopped = quit_bridge(log)
    phase_dir = arm(base, "terminal")
    session = Terminal(phase_dir, ["claude", "--model", args.model])
    finished = None
    end = time.time() + args.timeout
    while time.time() < end and finished is None:
        session.pump(0.5)
        finished = next((e for e in mod_events(phase_dir) if e["event"] in ("checks.done", "checks.failed")), None)
    session.pump(3.0)
    outside = bridge_status()
    session.close()
    disarm(phase_dir)
    (phase_dir / "terminal.txt").write_text(session.text, encoding="utf-8")
    report = {"bridge_stopped_before": stopped, "finished": finished, "bridge_after": outside,
              "mod_events": mod_events(phase_dir), "gate": gate_rows(phase_dir)}
    (phase_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if finished is not None else 1


def cmd_desktop(args) -> int:
    base = Path(args.base)
    stopped = quit_bridge(base / "desktop.log") if args.stop_bridge else None
    phase_dir = arm(base, args.phase)
    print(json.dumps({"armed": str(phase_dir), "bridge_stopped_before": stopped}))
    return 0


def cmd_report(args) -> int:
    phase_dir = Path(args.base) / args.phase
    report = {"owner": json.loads((phase_dir / "owner.json").read_text()) if (phase_dir / "owner.json").exists() else None,
              "bridge_now": bridge_status(), "mod_events": mod_events(phase_dir), "gate": gate_rows(phase_dir)}
    (phase_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def cmd_uninstall(args) -> int:
    base = Path(args.base)
    for phase_dir in base.iterdir():
        if phase_dir.is_dir():
            disarm(phase_dir)
    log = base / "uninstall.log"
    codes = [run_logged(["claude", "plugin", "uninstall", PLUGIN_ID], log),
             run_logged(["claude", "plugin", "marketplace", "remove", "funmedal-check"], log)]
    before = json.loads((base / "before.json").read_text(encoding="utf-8"))
    diff = compare(before, snapshot())
    (base / "after-uninstall.json").write_text(json.dumps(diff, indent=2, ensure_ascii=False), encoding="utf-8")
    print(log.read_text(encoding="utf-8"))
    print(json.dumps(diff, indent=2, ensure_ascii=False))
    return 0 if all(c == 0 for c in codes) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("prepare", "install", "terminal", "desktop", "report", "uninstall"):
        p = sub.add_parser(name)
        p.add_argument("--base", required=True)
        if name == "prepare":
            p.add_argument("--backup", required=True)
        if name == "terminal":
            p.add_argument("--model", default="claude-haiku-5-5")
            p.add_argument("--timeout", type=float, default=120)
        if name == "desktop":
            p.add_argument("--stop-bridge", action="store_true")
            p.add_argument("--phase", default="desktop")
        if name == "report":
            p.add_argument("--phase", required=True)
    args = parser.parse_args()
    return {"prepare": cmd_prepare, "install": cmd_install, "terminal": cmd_terminal, "desktop": cmd_desktop,
            "report": cmd_report, "uninstall": cmd_uninstall}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
