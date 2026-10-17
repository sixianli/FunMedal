import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from run import PLUGIN, PLUGIN_NAME, REPO, alive, first, mod_events, read_jsonl

HOLD_SECONDS = 300
DIALOG_WATCH_SECONDS = 15

TEXTS = {
    "idle": "桌面实验一：会话空闲时送入。请只回复：收到一",
    "busy": "桌面实验二：Claude 在干活时送入。请只回复：收到二",
    "approval": "桌面实验三：批准框挂着时送入。请只回复：收到三",
    "clear": "桌面实验四：清空对话之后送入。请只回复：收到四",
    "hold": "桌面实验五：会话开了 5 分钟之后送入。请只回复：收到五",
}


def append(path: Path, row: dict) -> None:
    with open(path, "a", encoding="utf-8") as log:
        log.write(json.dumps(row, ensure_ascii=False) + "\n")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True).stdout.strip()


def prepare(_: argparse.Namespace) -> int:
    out = REPO / "artifacts" / f"mod-submit-desktop-{time.strftime('%Y%m%d-%H%M%S')}"
    (out / "mod-events").mkdir(parents=True)
    (out / "inbox.txt").write_text("", encoding="utf-8")
    meta = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "head": git("rev-parse", "HEAD"),
            "dirty": git("status", "--short").splitlines(),
            "env": {"CLAUDE_CODE_PLUGIN_DIRS": str(PLUGIN), "FUNMEDAL_MODTEST_DIR": str(out)}}
    (out / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"out": str(out), "env": meta["env"]}, indent=2, ensure_ascii=False))
    return 0


def send(args: argparse.Namespace) -> int:
    out = Path(args.out)
    text = TEXTS[args.label]
    sent = time.time()
    with open(out / "inbox.txt", "a", encoding="utf-8") as inbox:
        inbox.write(text + "\n")
    append(out / "sent.jsonl", {"t": sent, "label": args.label, "text": text})
    print(json.dumps({"label": args.label, "t": sent}))
    return 0


def note(args: argparse.Namespace) -> int:
    row = {"t": time.time(), "label": args.label, "detail": args.detail}
    append(Path(args.out) / "notes.jsonl", row)
    print(json.dumps(row))
    return 0


def wait(args: argparse.Namespace) -> int:
    out = Path(args.out)
    match = {"event": args.event}
    if args.label:
        match["text"] = TEXTS[args.label]
    if args.decision:
        match["decision"] = args.decision
    if args.tool:
        match["tool"] = args.tool
    end = time.time() + args.timeout
    while True:
        row = first(mod_events(out), after=args.after, **match)
        if row or time.time() >= end:
            break
        time.sleep(0.3)
    print(json.dumps(row, ensure_ascii=False) if row else json.dumps({"timeout": args.timeout, **match},
                                                                    ensure_ascii=False))
    return 0 if row else 1


def by_label(rows: list[dict]) -> dict:
    found = {}
    for row in rows:
        found.setdefault(row["label"], row)
    return found


def resolution(events: list[dict], label: str):
    return first(events, event="submit.resolved", text=TEXTS[label]) or first(
        events, event="submit.rejected", text=TEXTS[label])


def transcript(session_id: str | None) -> dict:
    if not session_id:
        return {"found": False}
    paths = sorted((Path.home() / ".claude" / "projects").glob(f"*/{session_id}.jsonl"))
    entries = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "桌面实验" in line and "请只回复" in line:
                entry = json.loads(line)
                if entry.get("type") == "user":
                    entries.append({"names_plugin": PLUGIN_NAME in line, "origin": entry.get("origin")
                                    or (entry.get("message") or {}).get("origin")})
    return {"found": bool(paths), "user_entries": len(entries),
            "entries_naming_plugin": sum(1 for e in entries if e["names_plugin"]), "entries": entries}


def report(args: argparse.Namespace) -> int:
    out = Path(args.out)
    events = mod_events(out)
    sent = by_label(read_jsonl(out / "sent.jsonl"))
    notes = by_label(read_jsonl(out / "notes.jsonl"))
    starts = [e for e in events if e["event"] == "mod.start"]
    skipped = [e for e in events if e["event"] == "mod.start.skipped"]
    start = starts[0] if starts else {}
    base = start.get("t", 0.0)
    pid_file = out / "listener.pid"
    listener_pid = int(pid_file.read_text().split()[1]) if pid_file.exists() else None
    checks, observed = {}, {}

    def rel(t):
        return None if t is None else round(t - base, 2)

    checks["mod_loads_in_desktop"] = bool(len(starts) == 1 and "desktop" in (start.get("surfaces") or [])
                                          and listener_pid)
    observed["mod_start"] = {k: start.get(k) for k in ("session_id", "cwd", "surfaces")}
    observed["mod_start_count"] = len(starts)
    observed["other_sessions_skipped"] = [{k: e.get(k) for k in ("session_id", "cwd", "surfaces")} for e in skipped]

    if "idle" in sent:
        r = resolution(events, "idle")
        turn = first(events, event="turn.start", text=TEXTS["idle"])
        done = turn and first(events, after=turn["t"], event="turn.complete")
        checks["idle_submit_starts_turn"] = bool(r and r["event"] == "submit.resolved" and not r.get("dropped")
                                                 and turn and done)
        observed["idle"] = {"resolve_delay_s": r and round(r["t"] - sent["idle"]["t"], 2),
                            "turn_started": bool(turn), "turn_completed": bool(done)}

    if "busy" in sent and "busy_prompt" in notes:
        tool = first(events, after=notes["busy_prompt"]["t"], event="tool.check", tool="Bash")
        busy_end = tool and first(events, after=tool["t"], event="turn.complete")
        r = resolution(events, "busy")
        sent_t = sent["busy"]["t"]
        checks["busy_submit_waits_for_turn_end"] = bool(
            tool and tool.get("decision") == "allow" and busy_end and tool["t"] < sent_t < busy_end["t"]
            and r and r["event"] == "submit.resolved" and r["t"] >= busy_end["t"] - 0.5)
        observed["busy"] = {"tool_decision": tool and tool.get("decision"),
                            "busy_turn_ended_after_s": busy_end and round(busy_end["t"] - sent_t, 2),
                            "resolved_after_s": r and round(r["t"] - sent_t, 2)}

    if "approval" in sent and "approval_prompt" in notes and "dialog_closed" in notes:
        asked = first(events, after=notes["approval_prompt"]["t"], event="tool.check", decision="ask")
        r = resolution(events, "approval")
        sent_t, closed_t = sent["approval"]["t"], notes["dialog_closed"]["t"]
        watched = closed_t - sent_t
        checks["approval_submit_waits_for_dialog"] = bool(
            asked and asked["t"] < sent_t and watched >= DIALOG_WATCH_SECONDS and r
            and r["event"] == "submit.resolved" and r["t"] >= closed_t)
        observed["approval"] = {"mod_saw_ask": bool(asked), "watched_dialog_s": round(watched, 1),
                                "how_closed": notes["dialog_closed"].get("detail"),
                                "resolved_after_send_s": r and round(r["t"] - sent_t, 2),
                                "resolved_after_dialog_closed_s": r and round(r["t"] - closed_t, 2)}

    if "clear" in sent:
        r = resolution(events, "clear")
        observed["after_clear"] = {"clear_requested": "clear_requested" in notes,
                                   "mod_start_count": len(starts),
                                   "entered": bool(r and r["event"] == "submit.resolved"),
                                   "resolved_after_s": r and round(r["t"] - sent["clear"]["t"], 2)}

    if "hold" in sent:
        r = resolution(events, "hold")
        observed["after_hold"] = {"sent_after_mod_start_s": rel(sent["hold"]["t"]),
                                  "entered": bool(r and r["event"] == "submit.resolved"),
                                  "resolved_after_s": r and round(r["t"] - sent["hold"]["t"], 2)}

    observed["listener_alive_now"] = bool(listener_pid and alive(listener_pid))
    observed["transcript"] = transcript(start.get("session_id"))
    observed["notes"] = {k: {"t": rel(v["t"])} for k, v in notes.items()}
    observed["mod_events"] = [{**e, "t": rel(e["t"])} for e in events]
    expected = ("mod_loads_in_desktop", "idle_submit_starts_turn", "busy_submit_waits_for_turn_end",
                "approval_submit_waits_for_dialog")
    summary = {"meta": json.loads((out / "meta.json").read_text(encoding="utf-8")),
               "reported": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "checks": checks,
               "passed": all(checks.get(name) for name in expected), "observed": observed}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "observed"} | {
        "observed": {k: v for k, v in observed.items() if k != "mod_events"}}, indent=2, ensure_ascii=False))
    return 0 if summary["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare").set_defaults(run=prepare)
    p = sub.add_parser("send")
    p.add_argument("out")
    p.add_argument("label", choices=sorted(TEXTS))
    p.set_defaults(run=send)
    p = sub.add_parser("note")
    p.add_argument("out")
    p.add_argument("label")
    p.add_argument("--detail")
    p.set_defaults(run=note)
    p = sub.add_parser("wait")
    p.add_argument("out")
    p.add_argument("--event", required=True)
    p.add_argument("--label", choices=sorted(TEXTS))
    p.add_argument("--decision")
    p.add_argument("--tool")
    p.add_argument("--after", type=float, default=0.0)
    p.add_argument("--timeout", type=float, default=60)
    p.set_defaults(run=wait)
    p = sub.add_parser("report")
    p.add_argument("out")
    p.set_defaults(run=report)
    args = parser.parse_args()
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
