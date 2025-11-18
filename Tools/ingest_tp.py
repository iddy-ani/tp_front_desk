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
from tp_ingest.parsers import (
    CakeAuditParser,
    IntegrationReportParser,
    PASReportParser,
    PlistMasterParser,
    ScoreboardParser,
    VMinSearchParser,
)
from tp_ingest.persistence import MongoWriter
from tp_ingest import models


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
    pas_path = report_path.parent / "PASReport.csv"
    pas_result = PASReportParser().parse(pas_path)
    plist_path = report_path.parent / "plist_master.csv"
    plist_result = PlistMasterParser().parse(plist_path)
    cake_path = report_path.parent / "CAKEVADTLAudit.csv"
    cake_result = CakeAuditParser().parse(cake_path)
    vmin_path = report_path.parent / "VMinSearchAudit.csv"
    vmin_result = VMinSearchParser().parse(vmin_path)
    scoreboard_path = report_path.parent / "ScoreBoard_Report.csv"
    scoreboard_result = ScoreboardParser().parse(scoreboard_path)
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
        "warnings": (
            integration.warnings
            + pas_result.warnings
            + plist_result.warnings
            + cake_result.warnings
            + vmin_result.warnings
            + scoreboard_result.warnings
        ),
        "pas_records_count": len(pas_result.records),
        "plist_entries_count": len(plist_result.entries),
        "cake_audit_count": len(cake_result.entries),
        "vmin_search_count": len(vmin_result.records),
        "scoreboard_entries_count": len(scoreboard_result.entries),
    }
    if not args.no_persist:
        mongo_settings = settings.mongo
        artifact = models.IngestArtifact(tp_name=args.tp_name, git_hash=args.git_hash, report=integration)
        writer = MongoWriter(
            mongo_settings.uri,
            mongo_settings.database,
            mongo_settings.collection,
            mongo_settings.pas_collection,
            mongo_settings.plist_collection,
            mongo_settings.cake_collection,
            mongo_settings.vmin_collection,
            mongo_settings.scoreboard_collection,
        )
        doc_id = writer.write_ingest_artifact(artifact)
        pas_rows = writer.write_pas_records(args.tp_name, args.git_hash, pas_result.records)
        plist_rows = writer.write_plist_entries(args.tp_name, args.git_hash, plist_result.entries)
        cake_rows = writer.write_cake_audit_entries(args.tp_name, args.git_hash, cake_result.entries)
        vmin_rows = writer.write_vmin_search_records(args.tp_name, args.git_hash, vmin_result.records)
        scoreboard_rows = writer.write_scoreboard_entries(
            args.tp_name, args.git_hash, scoreboard_result.entries
        )
        writer.close()
        payload["mongo_doc_id"] = doc_id
        payload["pas_records_persisted"] = pas_rows
        payload["plist_entries_persisted"] = plist_rows
        payload["cake_audit_persisted"] = cake_rows
        payload["vmin_search_persisted"] = vmin_rows
        payload["scoreboard_entries_persisted"] = scoreboard_rows
    else:
        payload["mongo_doc_id"] = "skipped"

    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
