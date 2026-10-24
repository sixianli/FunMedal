import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.log.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        print("no hook events")
        return
    start = rows[0]["t"]
    for e in rows:
        chain = " < ".join(f"{p['comm'].rsplit('/', 1)[-1]}({p['pid']})" for p in e.get("parents", [])[:3])
        print(f"{e['t'] - start:8.2f} {e['phase']:6} {e['event']:18} sid={e['session_id'][:8]} "
              f"src={e.get('source')} reason={e.get('reason')} tool={e.get('tool_name')} "
              f"nt={e.get('notification_type')} alive={e.get('alive_s')}")
        if e["phase"] == "start" and e["event"] == "SessionStart":
            print(f"         cwd={e.get('cwd')}\n         project_dir={e.get('project_dir')}\n         parents={chain}")


if __name__ == "__main__":
    main()
