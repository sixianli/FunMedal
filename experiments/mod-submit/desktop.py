import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from run import PLUGIN, PLUGIN_NAME, REPO, alive, first, mod_events, read_jsonl

HOLD_SECONDS = 300
DIALOG_WATCH_SECONDS = 15

TEXTS = {
    "idle": "桌面实验一：会话空闲时送入。请只回复：收到一",
    "busy": "桌面实验二：Claude 在干活时送入。请只回复：收到二",
    "approval": "桌面实验三：批准框挂着时送入。请只回复：收到三",
    "approval_retry": "桌面实验六：批准框先弹出、再送入。请只回复：收到六",
    "approval_third": "桌面实验七：切回默认模式后，批准框先弹出、再送入。请只回复：收到七",
    "after_reply": "桌面实验八：按过 Stop、又正常发过一句话之后送入。请只回复：收到八",
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

    def entered(label: str) -> bool:
        r = resolution(events, label)
        return bool(r and r["event"] == "submit.resolved" and not r.get("dropped")
                    and first(events, event="turn.start", text=TEXTS[label]))

    desktop = json.loads(notes["desktop_session"]["detail"]) if "desktop_session" in notes else {}
    checks["mod_loads_in_desktop"] = bool(len(starts) == 1 and listener_pid and desktop.get("cwd")
                                          and start.get("cwd") == desktop["cwd"])
    observed["desktop_session"] = desktop
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
            and entered("busy") and r["t"] >= busy_end["t"] - 0.5)
        observed["busy"] = {"tool_decision": tool and tool.get("decision"),
                            "busy_turn_ended_after_s": busy_end and round(busy_end["t"] - sent_t, 2),
                            "resolved_after_s": r and round(r["t"] - sent_t, 2)}

    def approval(label: str, prompt: str, closed: str, closed_t: float):
        asked = first(events, after=notes[prompt]["t"], event="tool.check", decision="ask")
        asked = asked if asked and asked["t"] < closed_t else None
        r = resolution(events, label)
        sent_t = sent[label]["t"]
        watched = closed_t - sent_t
        waited = bool(asked and asked["t"] < sent_t and watched >= DIALOG_WATCH_SECONDS and r and r["t"] >= closed_t)
        seen = {"mod_saw_ask": bool(asked), "ask_after_send_s": asked and round(asked["t"] - sent_t, 2),
                "watched_dialog_s": round(watched, 1), "how_closed": notes[closed].get("detail"),
                "resolved_after_send_s": r and round(r["t"] - sent_t, 2),
                "resolved_after_dialog_closed_s": r and round(r["t"] - closed_t, 2),
                "dropped": r and r.get("dropped"), "drop_reason": r and r.get("drop_reason"),
                "entered": entered(label)}
        return (waited, entered(label)), seen

    if "approval" in sent and "approval_prompt" in notes and "first_try_closed" in notes:
        closed = json.loads(notes["first_try_closed"]["detail"])["approved_at_utc"].replace("Z", "+00:00")
        observed["approval_first_try"] = approval("approval", "approval_prompt", "first_try_closed",
                                                  datetime.fromisoformat(closed).timestamp())[1]
    if "approval_retry" in sent and "approval_retry_prompt" in notes and "second_try_result" in notes:
        observed["approval_second_try"] = approval("approval_retry", "approval_retry_prompt", "dialog_closed",
                                                   notes["dialog_closed"]["t"])[1]
        observed["approval_second_try"]["how_closed"] = notes["second_try_result"].get("detail")
    if "approval_third" in sent and "approval_third_prompt" in notes and "third_dialog_closed" in notes:
        (waited, came_in), observed["approval"] = approval(
            "approval_third", "approval_third_prompt", "third_dialog_closed", notes["third_dialog_closed"]["t"])
        checks["approval_submit_waits_for_dialog"] = waited
        checks["approval_submit_enters_after_stop"] = came_in

    after_stop = [label for label in ("hold", "after_reply") if label in sent]
    if after_stop:
        checks["submit_after_stop_enters"] = all(entered(label) for label in after_stop)
        observed["after_stop"] = {label: {"sent_after_mod_start_s": rel(sent[label]["t"]),
                                          "dropped": (resolution(events, label) or {}).get("dropped"),
                                          "drop_reason": (resolution(events, label) or {}).get("drop_reason"),
                                          "entered": entered(label)} for label in after_stop}

    if "clear" in sent:
        r = resolution(events, "clear")
        observed["after_clear"] = {"clear_requested": "clear_requested" in notes, "mod_start_count": len(starts),
                                   "entered": entered("clear"),
                                   "resolved_after_s": r and round(r["t"] - sent["clear"]["t"], 2)}
    if "clear_refused" in notes:
        observed["after_clear"] = {"not_run": notes["clear_refused"].get("detail")}

    observed["listener_alive_now"] = bool(listener_pid and alive(listener_pid))
    observed["transcript"] = transcript(start.get("session_id"))
    observed["notes"] = {k: {"t": rel(v["t"])} for k, v in notes.items()}
    observed["mod_events"] = [{**e, "t": rel(e["t"])} for e in events]
    expected = ("mod_loads_in_desktop", "idle_submit_starts_turn", "busy_submit_waits_for_turn_end",
                "approval_submit_waits_for_dialog", "approval_submit_enters_after_stop", "submit_after_stop_enters")
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
