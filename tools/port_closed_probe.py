import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cardlink  # noqa: E402


def info_fields(line):
    return dict(p.split("=", 1) for p in line.split()[2:] if "=" in p)


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure what the card's unsolicited lines do while the Mac keeps its serial port closed.")
    parser.add_argument("--closed-seconds", type=float, default=10.0)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    card = cardlink.Card()
    before = info_fields(card.wait_ready())
    card.command("adc 60")
    open_lines = []
    end = time.monotonic() + 1.0
    while (line := card.read_line(end)) is not None:
        open_lines.append(line)
    card.close()
    closed_at = time.monotonic()
    time.sleep(args.closed_seconds)

    card = cardlink.Card()
    reopened_at = time.monotonic()
    burst = b""
    first_gap_at = None
    last_data = time.monotonic()
    while time.monotonic() - reopened_at < 3.0:
        chunk = card.link.read(4096)
        now = time.monotonic()
        if chunk:
            if first_gap_at is None and burst and now - last_data > 0.015:
                first_gap_at = len(burst)
            burst += chunk
            last_data = now
    card.pending = b""
    card.command("adc 1")
    time.sleep(1.5)
    card.link.reset_input_buffer()
    after = info_fields(card.command("info"))
    card.close()

    lines = burst.decode("utf-8", "replace").split("\n")
    adc_lines = [l for l in lines if l.startswith("ADC ")]
    produced = round(args.closed_seconds / 0.02)
    record = {
        "closed_seconds": args.closed_seconds,
        "closed_for_s": round(reopened_at - closed_at, 3),
        "lines_before_close": len([l for l in open_lines if l.startswith("ADC ")]),
        "bytes_in_first_3s_after_reopen": len(burst),
        "bytes_before_first_pause": first_gap_at,
        "adc_lines_in_first_3s": len(adc_lines),
        "adc_lines_expected_while_closed": produced,
        "events_dropped_before": int(before["events_dropped"]),
        "events_dropped_after": int(after["events_dropped"]),
        "events_dropped_delta": int(after["events_dropped"]) - int(before["events_dropped"]),
        "before": before,
        "after": after,
    }
    (out / "port-closed.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    (out / "reopen-burst.txt").write_bytes(burst)
    print(json.dumps({k: v for k, v in record.items() if k not in ("before", "after")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
