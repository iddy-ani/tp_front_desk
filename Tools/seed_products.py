"""Batch-ingest multiple products and TP revisions using shared ingestion logic."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from tp_ingest.config import IngestSettings
from tp_ingest.product_config import load_product_configs

from ingest_tp import run_ingestion


def _dedupe_preserve_order(items: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _build_tp_queue(config, include_latest: bool, history_depth: int) -> List[str]:
    queue: List[str] = []
    if include_latest and config.latest_tp:
        queue.append(config.latest_tp)
    if history_depth != 0 and config.releases:
        releases = config.releases
        if history_depth > 0:
            releases = releases[:history_depth]
        queue.extend(releases)
    return _dedupe_preserve_order(queue)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed multiple products and TP revisions.")
    parser.add_argument(
        "--product-config",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "Products.json",
        help="Path to Products.json file (single object or array).",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Repository root (used for resolving defaults).",
    )
    parser.add_argument(
        "--tp-root",
        type=Path,
        default=None,
        help="Override TP root directory (defaults to settings-derived Test Programs).",
    )
    parser.add_argument(
        "--product-codes",
        nargs="*",
        help="Optional list of product codes to include (defaults to all).",
    )
    parser.add_argument(
        "--history-depth",
        type=int,
        default=0,
        help="Number of historical releases to ingest per product (0 skips history, -1 ingests all).",
    )
    parser.add_argument(
        "--skip-latest",
        action="store_true",
        help="Skip the latest TP per product and only process historical releases.",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Dry-run mode that skips Mongo persistence but still parses and reports counts.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on the number of TP ingests to run across all products.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = IngestSettings.from_env(repo_root=args.repo_root)
    if args.tp_root:
        settings.tp_root = args.tp_root

    configs = load_product_configs(args.product_config)
    filter_codes = {code.upper() for code in args.product_codes} if args.product_codes else None
    include_latest = not args.skip_latest
    history_depth = args.history_depth

    results: List[Dict[str, object]] = []
    total_runs = 0
    for config in configs:
        code_upper = (config.product_code or "").upper()
        if filter_codes and code_upper not in filter_codes:
            continue
        tp_queue = _build_tp_queue(config, include_latest, history_depth)
        if not tp_queue:
            results.append(
                {
                    "product_code": config.product_code,
                    "product_name": config.product_name,
                    "status": "skipped",
                    "reason": "No TP names listed for this product",
                }
            )
            continue
        for tp_name in tp_queue:
            if args.limit is not None and total_runs >= args.limit:
                break
            try:
                payload = run_ingestion(
                    tp_name=tp_name,
                    settings=settings,
                    git_hash=None,
                    no_persist=args.no_persist,
                    product_config_path=args.product_config,
                    product_code=config.product_code,
                )
                results.append(
                    {
                        "product_code": config.product_code,
                        "product_name": config.product_name,
                        "tp_name": tp_name,
                        "git_hash": payload.get("git_hash"),
                        "mongo_doc_id": payload.get("mongo_doc_id"),
                        "warnings": payload.get("warnings", []),
                    }
                )
            except FileNotFoundError as exc:
                results.append(
                    {
                        "product_code": config.product_code,
                        "product_name": config.product_name,
                        "tp_name": tp_name,
                        "status": "error",
                        "error": str(exc),
                    }
                )
            total_runs += 1
        if args.limit is not None and total_runs >= args.limit:
            break

    summary = {
        "product_count": len(results),
        "ingest_runs": results,
        "persisted": not args.no_persist,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
     main()
