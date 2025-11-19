"""Monitor daily scanner alert stream and trigger notifications."""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_ALERTS = Path(__file__).resolve().parent.parent / "state" / "daily_scanner_alerts.jsonl"
DEFAULT_STATE = Path(__file__).resolve().parent.parent / "state" / "alert_monitor_state.json"


def load_state(path: Path) -> Dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logging.warning("State file %s is invalid JSON; starting fresh", path)
    return {"last_line": 0}


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _safe_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {k: ("" if v is None else v) for k, v in entry.items()}


def notify(entry: Dict[str, Any], command: str) -> None:
    formatted = command.format(**_safe_entry(entry))
    logging.info("Running notify command: %s", formatted)
    subprocess.run(formatted, shell=True, check=False)


def summarize(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    per_status: Dict[str, int] = {}
    per_product: Dict[str, int] = {}
    for entry in entries:
        status = str(entry.get("status", "unknown"))
        product = str(entry.get("product_code", "UNKNOWN"))
        per_status[status] = per_status.get(status, 0) + 1
        per_product[product] = per_product.get(product, 0) + 1
    return {
        "total_new_alerts": len(entries),
        "by_status": per_status,
        "by_product": per_product,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor alert JSONL and emit notifications.")
    parser.add_argument(
        "--alerts-file",
        type=Path,
        default=DEFAULT_ALERTS,
        help="Path to daily_scanner_alerts.jsonl",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE,
        help="Persistent tracker for last processed line.",
    )
    parser.add_argument(
        "--notify-command",
        default=None,
        help="Optional shell command to execute per new alert (Python format placeholders allowed).",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Emit summary JSON to stdout for upstream tooling.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Ignore saved state and reprocess entire file.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging output.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    alerts_path = args.alerts_file.resolve()
    state_path = args.state_file.resolve()
    state = {"last_line": 0} if args.reset else load_state(state_path)
    last_line = int(state.get("last_line", 0))

    if not alerts_path.exists():
        logging.info("Alerts file %s not found; nothing to do", alerts_path)
        return 0

    lines = alerts_path.read_text(encoding="utf-8").splitlines()
    if last_line > len(lines):
        logging.info("Alerts file shrank (last_line=%s, current=%s); resetting pointer", last_line, len(lines))
        last_line = 0

    new_entries: List[Dict[str, Any]] = []
    for idx, line in enumerate(lines, start=1):
        if idx <= last_line:
            continue
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            logging.warning("Skipping malformed alert line %s", idx)
            continue
        new_entries.append(payload)
        if args.notify_command:
            notify(payload, args.notify_command)

    if new_entries:
        summary = summarize(new_entries)
        summary["alerts_file"] = str(alerts_path)
        summary["state_file"] = str(state_path)
        summary["last_line"] = last_line
        summary["new_last_line"] = len(lines)
        if args.print_json:
            print(json.dumps(summary, indent=2))
        else:
            logging.info("New alerts detected: %s", summary)
        state["last_line"] = len(lines)
        save_state(state_path, state)
        return 1

    logging.info("No new alerts (last_line=%s)", last_line)
    state["last_line"] = len(lines)
    save_state(state_path, state)
    if args.print_json:
        print(json.dumps({
            "alerts_file": str(alerts_path),
            "state_file": str(state_path),
            "last_line": len(lines),
            "new_last_line": len(lines),
            "total_new_alerts": 0,
        }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
