import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from drive import plain

MARKERS = {
    "permission dialog": "Doyouwanttoproceed",
    "allowed by hook": "AllowedbyPermissionRequesthook",
    "denied by hook": "DeniedbyPermissionRequesthook",
    "running PermissionRequest hook": "runningPermissionRequesthook",
}


def first_seen(raw_path: Path) -> dict:
    data = raw_path.read_bytes()
    text = ""
    seen = {}
    i = 0
    while i + 12 <= len(data):
        t, n = struct.unpack(">dI", data[i:i + 12])
        text += re.sub(r"\s+", "", plain(data[i + 12:i + 12 + n]))
        i += 12 + n
        for name, needle in MARKERS.items():
            if name not in seen and needle in text:
                seen[name] = round(t, 3)
    return seen


def main() -> int:
    report = {}
    for out in sys.argv[1:]:
        out = Path(out)
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        hooks = [{"t": e["t_rel"], "event": e["event"], "kind": e.get("kind"), "decision": e.get("decision"),
                  "notification_type": (e.get("input") or {}).get("notification_type") if isinstance(e.get("input"), dict) else None}
                 for e in summary["hook_events"]]
        report[summary["scenario"]] = {
            "claude_version": summary["claude_version"],
            "screen_first_seen_s": first_seen(out / "terminal.raw"),
            "keys_sent": [t for t in summary["timeline"] if t["event"] == "sent"],
            "marker_created_s": summary["marker_created_s"],
            "hooks": hooks,
        }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
