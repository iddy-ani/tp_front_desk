"""Daily automation pipeline for copying network TPs and triggering ingestion."""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from tp_ingest.config import IngestSettings
from tp_ingest.product_config import load_product_configs

from ingest_tp import run_ingestion

StateDict = Dict[str, Dict[str, Dict[str, Dict[str, str]]]]
FAILURE_STATUSES = {
    "network-unavailable",
    "copy-timeout",
    "copy-failed",
    "ingest-failed",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_state() -> StateDict:
    return {"products": {}}


def load_state(path: Path) -> StateDict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logging.warning("State file %s is invalid JSON; starting with empty state", path)
    return _default_state()


def save_state(path: Path, state: StateDict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _append_json_line(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload))
        handle.write("\n")


def _sorted_directories(network_path: Path) -> List[Path]:
    candidates: List[tuple[float, Path]] = []
    try:
        for child in network_path.iterdir():
            if not child.is_dir():
                continue
            try:
                mtime = child.stat().st_mtime
            except OSError:
                mtime = 0.0
            candidates.append((mtime, child))
    except OSError as exc:
        raise FileNotFoundError(f"Unable to enumerate {network_path}: {exc}") from exc
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [entry for _, entry in candidates]


def _select_new_directories(
    dirs: Iterable[Path],
    processed: Dict[str, Dict[str, str]],
    max_per_product: Optional[int],
    force: bool,
) -> List[Path]:
    selected: List[Path] = []
    processed_names = set(processed.keys())
    for candidate in dirs:
        if not force and candidate.name in processed_names:
            continue
        selected.append(candidate)
        if max_per_product is not None and len(selected) >= max_per_product:
            break
    return selected


def _run_copy_script(
    copy_script: Path,
    source: Path,
    destination: Path,
    *,
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(copy_script),
        "-SourcePath",
        str(source),
        "-DestinationPath",
        str(destination),
    ]
    logging.info("Copying %s -> %s", source, destination)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def _update_product_state(
    state: StateDict,
    product_key: str,
    tp_name: str,
    *,
    git_hash: Optional[str],
    mongo_id: Optional[str],
) -> None:
    product_bucket = state.setdefault("products", {}).setdefault(product_key, {})
    processed = product_bucket.setdefault("processed_tps", {})
    processed[tp_name] = {
        "last_processed": _now_iso(),
        "git_hash": git_hash or "unknown",
        "mongo_doc_id": mongo_id or "unknown",
    }
    product_bucket["last_scan"] = _now_iso()


def build_parser() -> argparse.ArgumentParser:
    default_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Daily automation pipeline for TP ingestion.")
    parser.add_argument(
        "--product-config",
        type=Path,
        default=default_root / "Products.json",
        help="Path to Products.json containing product metadata.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=default_root,
        help="Repository root used for resolving defaults.",
    )
    parser.add_argument(
        "--tp-root",
        type=Path,
        default=None,
        help="Override TP root directory (defaults to settings-derived Test Programs).",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=default_root / "state" / "daily_scanner_state.json",
        help="File used to track which TP folders have already been ingested.",
    )
    parser.add_argument(
        "--copy-script",
        type=Path,
        default=default_root / "copy_network_files.ps1",
        help="Path to the PowerShell helper that copies network TP folders.",
    )
    parser.add_argument(
        "--product-codes",
        nargs="*",
        help="Optional whitelist of product codes to include.",
    )
    parser.add_argument(
        "--max-network-scan",
        type=int,
        default=20,
        help="Maximum number of network TP folders to inspect per product (sorted by mtime).",
    )
    parser.add_argument(
        "--max-per-product",
        type=int,
        default=3,
        help="Cap on how many new TP folders to process per product per run.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Global cap on ingestion attempts across all products.",
    )
    parser.add_argument(
        "--copy-timeout",
        type=int,
        default=3600,
        help="Timeout (seconds) for each copy operation.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Maximum number of attempts per TP (includes the initial try).",
    )
    parser.add_argument(
        "--retry-delay",
        type=int,
        default=30,
        help="Seconds to wait between retries when a copy or ingest step fails.",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Skip Mongo persistence when triggering ingestion (dry-run on database).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover candidates without copying or ingesting.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess TP folders even if they were recorded in the state file.",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=default_root / "logs" / "daily_scanner.log",
        help="Path to append structured job records (JSON lines).",
    )
    parser.add_argument(
        "--alerts-file",
        type=Path,
        default=default_root / "state" / "daily_scanner_alerts.jsonl",
        help="File where failed ingests are recorded for downstream alerting.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging output.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    repo_root = args.repo_root.resolve()
    settings = IngestSettings.from_env(repo_root=repo_root)
    if args.tp_root:
        settings.tp_root = args.tp_root.resolve()

    product_config_path = args.product_config.resolve()
    copy_script = args.copy_script.resolve()
    if not copy_script.exists():
        raise FileNotFoundError(f"Copy script not found at {copy_script}")
    log_path = args.log_file.resolve() if args.log_file else None
    alerts_path = args.alerts_file.resolve() if args.alerts_file else None
    max_retries = max(1, args.max_retries)
    retry_delay = max(0, args.retry_delay)

    configs = load_product_configs(product_config_path)
    filter_codes = {code.upper() for code in args.product_codes} if args.product_codes else None
    state = load_state(args.state_file.resolve())

    results: List[Dict[str, Any]] = []
    alerts: List[Dict[str, Any]] = []

    def record_entry(entry: Dict[str, Any], *, final: bool) -> None:
        payload = dict(entry)
        payload.setdefault("timestamp", _now_iso())
        payload["final_attempt"] = final
        if log_path:
            _append_json_line(log_path, payload)
        if final:
            results.append(payload)
            if payload.get("status") in FAILURE_STATUSES:
                alerts.append(payload)
                if alerts_path:
                    _append_json_line(alerts_path, payload)

    total_ingest_attempts = 0
    for config in configs:
        product_code = (config.product_code or "UNKNOWN").upper()
        if filter_codes and product_code not in filter_codes:
            continue
        network_path = Path(config.network_path)
        try:
            directories = _sorted_directories(network_path)
        except FileNotFoundError as exc:
            logging.error("Skipping %s: %s", product_code, exc)
            record_entry(
                {
                    "product_code": product_code,
                    "product_name": config.product_name,
                    "status": "network-unavailable",
                    "error": str(exc),
                },
                final=True,
            )
            continue
        if args.max_network_scan is not None:
            directories = directories[: args.max_network_scan]

        product_bucket = state.setdefault("products", {}).setdefault(product_code, {})
        processed = product_bucket.setdefault("processed_tps", {})
        candidates = _select_new_directories(directories, processed, args.max_per_product, args.force)
        if not candidates:
            logging.info("No new TP folders detected for %s", product_code)
            continue

        for directory in candidates:
            if args.limit is not None and total_ingest_attempts >= args.limit:
                break
            tp_name = directory.name
            entry_base: Dict[str, Any] = {
                "product_code": product_code,
                "product_name": config.product_name,
                "tp_name": tp_name,
                "network_path": str(directory),
            }
            if args.dry_run:
                dry_entry = dict(entry_base)
                dry_entry["status"] = "dry-run"
                record_entry(dry_entry, final=True)
                continue

            total_ingest_attempts += 1
            dest_path = settings.tp_root / tp_name
            attempt = 0
            success = False
            while attempt < max_retries:
                attempt += 1
                attempt_entry = dict(entry_base)
                attempt_entry["attempt"] = attempt
                attempt_entry["max_retries"] = max_retries
                try:
                    copy_result = _run_copy_script(
                        copy_script,
                        source=directory,
                        destination=dest_path,
                        timeout=args.copy_timeout,
                    )
                except subprocess.TimeoutExpired:
                    attempt_entry["status"] = "copy-timeout"
                    final = attempt >= max_retries
                    record_entry(attempt_entry, final=final)
                    logging.error("Copy timed out for %s (attempt %s)", tp_name, attempt)
                    if final or retry_delay == 0:
                        break
                    time.sleep(retry_delay)
                    continue
                if copy_result.returncode != 0:
                    attempt_entry["status"] = "copy-failed"
                    attempt_entry["copy_exit_code"] = copy_result.returncode
                    attempt_entry["copy_stdout"] = copy_result.stdout.strip()
                    attempt_entry["copy_stderr"] = copy_result.stderr.strip()
                    final = attempt >= max_retries
                    record_entry(attempt_entry, final=final)
                    logging.error(
                        "Copy failed for %s (attempt %s, exit %s)",
                        tp_name,
                        attempt,
                        copy_result.returncode,
                    )
                    if final or retry_delay == 0:
                        break
                    time.sleep(retry_delay)
                    continue

                try:
                    payload = run_ingestion(
                        tp_name=tp_name,
                        settings=settings,
                        git_hash=None,
                        no_persist=args.no_persist,
                        product_config_path=product_config_path,
                        product_code=config.product_code,
                    )
                except Exception as exc:  # pylint: disable=broad-except
                    attempt_entry["status"] = "ingest-failed"
                    attempt_entry["error"] = str(exc)
                    final = attempt >= max_retries
                    record_entry(attempt_entry, final=final)
                    logging.exception("Ingestion failed for %s (attempt %s)", tp_name, attempt)
                    if final or retry_delay == 0:
                        break
                    time.sleep(retry_delay)
                    continue

                attempt_entry["status"] = "ingested" if not args.no_persist else "parsed"
                attempt_entry["git_hash"] = payload.get("git_hash")
                attempt_entry["mongo_doc_id"] = payload.get("mongo_doc_id")
                attempt_entry["warnings"] = payload.get("warnings", [])
                record_entry(attempt_entry, final=True)
                success = True
                if not args.no_persist:
                    _update_product_state(
                        state,
                        product_code,
                        tp_name,
                        git_hash=attempt_entry.get("git_hash"),
                        mongo_id=attempt_entry.get("mongo_doc_id"),
                    )
                break
            if not success:
                logging.error("Max retries reached for %s", tp_name)
        if args.limit is not None and total_ingest_attempts >= args.limit:
            break

    if not args.dry_run and not args.no_persist:
        save_state(args.state_file.resolve(), state)

    summary = {
        "run_started": _now_iso(),
        "results": results,
        "alerts": alerts,
        "ingest_attempts": total_ingest_attempts,
        "dry_run": args.dry_run,
        "persisted": not args.no_persist,
        "state_file": str(args.state_file.resolve()),
        "log_file": str(log_path) if log_path else None,
        "alerts_file": str(alerts_path) if alerts_path else None,
        "max_retries": max_retries,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
