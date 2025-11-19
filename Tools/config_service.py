"""Manage product configuration metadata and ingestion status."""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from tp_ingest.product_config import load_product_configs, models

DEFAULT_PRODUCTS = Path(__file__).resolve().parent.parent / "Products.json"
DEFAULT_STATE = Path(__file__).resolve().parent.parent / "state" / "daily_scanner_state.json"


def _strip_json_comments(raw_text: str) -> str:
    cleaned_lines = []
    for line in raw_text.splitlines():
        if "//" in line:
            prefix, *_ = line.split("//", 1)
            cleaned_lines.append(prefix.rstrip())
        else:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def _read_raw_payload(path: Path) -> Tuple[Any, bool]:
    text = path.read_text(encoding="utf-8")
    cleaned = _strip_json_comments(text)
    cleaned = cleaned.replace("\\", "\\\\")
    data = json.loads(cleaned)
    is_list = isinstance(data, list)
    if not is_list and not isinstance(data, dict):
        raise ValueError("Products.json must be an object or array")
    return data, is_list


def _config_to_payload(config: models.ProductConfig) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "ProductCode": config.product_code,
        "ProductName": config.product_name,
        "NetworkPath": config.network_path,
    }
    if config.latest_tp:
        payload["LatestTP"] = config.latest_tp
    if config.number_of_releases is not None:
        payload["NumberOfReleases"] = config.number_of_releases
    if config.releases:
        payload["ListOfReleases"] = config.releases
    if config.last_run_date:
        payload["LastRunDate"] = config.last_run_date
    payload.update(config.additional_attributes)
    return payload


def load_ingestion_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"products": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Unable to parse ingestion state at {path}: {exc}") from exc


def _latest_ingested(processed_tps: Dict[str, Dict[str, Any]]) -> Optional[Tuple[str, Dict[str, Any]]]:
    best: Optional[Tuple[str, Dict[str, Any]]] = None
    for tp_name, metadata in processed_tps.items():
        stamp = metadata.get("last_processed") or ""
        try:
            ts = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            ts = datetime.min
        if best is None or ts > best[1]["_ts"]:
            best = (tp_name, {**metadata, "_ts": ts})
    if best:
        meta = dict(best[1])
        meta.pop("_ts", None)
        return best[0], meta
    return None


def summarize_products(
    configs: Iterable[models.ProductConfig],
    state: Dict[str, Any],
    *,
    validate_paths: bool = False,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    products_state = state.get("products", {}) if isinstance(state, dict) else {}
    for config in configs:
        product_code = (config.product_code or "").upper()
        product_state = products_state.get(product_code, {})
        processed = product_state.get("processed_tps", {})
        latest_processed = _latest_ingested(processed) if processed else None
        last_ingested_tp = latest_processed[0] if latest_processed else None
        path_valid: Optional[bool] = None
        if validate_paths and config.network_path:
            try:
                path_valid = Path(config.network_path).exists()
            except OSError:
                path_valid = False
        results.append(
            {
                "product_code": product_code,
                "product_name": config.product_name,
                "network_path": config.network_path,
                "path_valid": path_valid,
                "config_latest_tp": config.latest_tp,
                "state_last_ingested": last_ingested_tp,
                "processed_count": len(processed),
            }
        )
    return results


def sync_latest_from_state(
    configs: List[models.ProductConfig],
    state: Dict[str, Any],
    *,
    update_releases: bool = True,
) -> List[Dict[str, Any]]:
    products_state = state.get("products", {}) if isinstance(state, dict) else {}
    changes: List[Dict[str, Any]] = []
    for config in configs:
        product_code = (config.product_code or "").upper()
        product_state = products_state.get(product_code, {})
        processed = product_state.get("processed_tps", {})
        latest_processed = _latest_ingested(processed) if processed else None
        if not latest_processed:
            continue
        last_ingested_tp = latest_processed[0]
        if not last_ingested_tp or last_ingested_tp == config.latest_tp:
            continue
        old_latest = config.latest_tp
        config.latest_tp = last_ingested_tp
        if update_releases:
            releases = list(config.releases or [])
            if last_ingested_tp in releases:
                releases.remove(last_ingested_tp)
            releases.insert(0, last_ingested_tp)
            config.releases = releases
            config.number_of_releases = len(releases)
        changes.append(
            {
                "product_code": product_code,
                "previous_latest": old_latest,
                "new_latest": last_ingested_tp,
            }
        )
    return changes


def write_products_config(path: Path, configs: List[models.ProductConfig], original_is_list: bool) -> None:
    payload = [_config_to_payload(config) for config in configs]
    data: Any
    if original_is_list:
        data = payload
    elif len(payload) == 1:
        data = payload[0]
    else:
        data = payload
    serialized = json.dumps(data, indent=2)
    path.write_text(serialized, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Central configuration service for product metadata.")
    parser.add_argument(
        "--product-config",
        type=Path,
        default=DEFAULT_PRODUCTS,
        help="Path to Products.json",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE,
        help="Ingestion state file produced by daily_scanner.",
    )
    parser.add_argument(
        "--product-codes",
        nargs="*",
        help="Optional product codes to filter (UPPERCASE).",
    )
    parser.add_argument(
        "--validate-paths",
        action="store_true",
        help="Check whether each NetworkPath currently exists.",
    )
    parser.add_argument(
        "--sync-latest",
        action="store_true",
        help="Update LatestTP/ListOfReleases with the last ingested revision from state.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist modifications back to Products.json (otherwise dry run).",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Emit summary JSON instead of log output.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging output.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    product_path = args.product_config.resolve()
    state_path = args.state_file.resolve()
    _, is_list = _read_raw_payload(product_path)
    configs = load_product_configs(product_path)
    filter_codes = {code.upper() for code in args.product_codes} if args.product_codes else None
    if filter_codes:
        configs = [cfg for cfg in configs if (cfg.product_code or "").upper() in filter_codes]
    state = load_ingestion_state(state_path)

    summary = summarize_products(configs, state, validate_paths=args.validate_paths)
    changes: List[Dict[str, Any]] = []
    if args.sync_latest:
        changes = sync_latest_from_state(configs, state)

    if args.sync_latest and changes:
        logging.info("Detected %s product updates", len(changes))
        if args.apply:
            logging.info("Writing updates to %s", product_path)
            write_products_config(product_path, configs, is_list)
        else:
            logging.info("Dry run; use --apply to write changes")

    payload = {
        "product_config": str(product_path),
        "state_file": str(state_path),
        "summary": summary,
        "changes": changes,
        "applied": bool(args.apply and changes),
    }
    if args.print_json:
        print(json.dumps(payload, indent=2))
    else:
        logging.info("Summary: %s", payload)


if __name__ == "__main__":
    main()
