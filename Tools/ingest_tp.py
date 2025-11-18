"""Entry point for TP ingestion experiments.

Usage:
    python Tools/ingest_tp.py --tp-name PTUSDJXA1H21G402546
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from tp_ingest.config import IngestSettings
from tp_ingest.parsers import IntegrationReportParser
from tp_ingest.persistence import MongoWriter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parse TP integration reports into structured JSON.")
    parser.add_argument("--tp-name", required=True, help="Name of the TP folder under the Test Programs directory.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Override repository root (defaults to current repo).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional explicit path to Integration_Report.txt (bypasses tp-name lookup).",
    )
    parser.add_argument("--git-hash", default="unknown", help="Git hash to associate with this ingestion run.")
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Skip writing results to MongoDB (defaults to persisting).",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = IngestSettings.from_env(repo_root=args.repo_root)

    report_path = args.report
    if report_path is None:
        tp_dir = settings.tp_root / args.tp_name
        report_path = tp_dir / "Reports" / "Integration_Report.txt"
    report_path = report_path.resolve()
    if not report_path.exists():
        raise FileNotFoundError(f"Integration report not found at {report_path}")

    integration = IntegrationReportParser().parse(report_path)
    payload: Dict[str, Any] = {
        "program": integration.program.__dict__,
        "environment": {
            "prime_rev": integration.environment.prime_rev,
            "fuse_file_rev": integration.environment.fuse_file_rev,
            "pattern_revs": [rev.__dict__ for rev in integration.environment.pattern_revs],
        },
        "shared_components": [component.__dict__ for component in integration.shared_components],
        "tp_modules": [component.__dict__ for component in integration.tp_modules],
        "flows_raw_keys": list(integration.flows_raw.keys()),
        "flow_table_count": len(integration.flow_tables),
        "dll_inventory_count": len(integration.dll_inventory),
        "dll_sample": [entry.__dict__ for entry in integration.dll_inventory[:5]],
        "warnings": integration.warnings,
    }
    if not args.no_persist:
        mongo_settings = settings.mongo
        writer = MongoWriter(mongo_settings.uri, mongo_settings.database, mongo_settings.collection)
        doc_id = writer.upsert_report(tp_name=args.tp_name, git_hash=args.git_hash, report=integration)
        writer.close()
        payload["mongo_doc_id"] = doc_id
    else:
        payload["mongo_doc_id"] = "skipped"

    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
