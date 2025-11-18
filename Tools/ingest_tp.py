"""Entry point for TP ingestion experiments.

Usage:
    python Tools/ingest_tp.py --tp-name PTUSDJXA1H21G402546
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from tp_ingest.config import IngestSettings
from tp_ingest.parsers import (
    CakeAuditParser,
    IntegrationReportParser,
    PASReportParser,
    PlistMasterParser,
    ScoreboardParser,
    SetpointsParser,
    VMinSearchParser,
)
from tp_ingest.persistence import MongoWriter
from tp_ingest import models
from tp_ingest.product_config import find_product_config, load_product_configs


IMPORTANT_ARTIFACTS = {
    "Reports/Integration_Report.txt": "report",
    "Reports/PASReport.csv": "report",
    "Reports/PASReport_ModuleSummary.csv": "report",
    "Reports/PASReport_PortLevel.csv": "report",
    "Reports/ScoreBoard_Report.csv": "report",
    "Reports/CAKEVADTLAudit.csv": "gsds",
    "Reports/VMinSearchAudit.csv": "vmin",
    "Reports/plist_master.csv": "plist",
    "Reports/CAKE_DLLVersions.csv": "dll",
    "Reports/StartItemList.csv": "flow",
    "Reports/GitInfo.txt": "git",
    "Reports/GitReportInfo.txt": "git",
    "Reports/ExportPath.txt": "report",
    "BaseLevels.tcg": "levels",
    "BaseSpecs.usrv": "specs",
    "EnvironmentFile.env": "env",
}

GIT_INFO_CANDIDATES = (
    Path("Reports") / "GitInfo.txt",
    Path("Reports") / "GitReportInfo.txt",
)


def collect_artifact_references(tp_dir: Path) -> List[models.ArtifactReference]:
    references: List[models.ArtifactReference] = []
    seen: set[str] = set()

    def _register(path: Path, category: str) -> None:
        rel_path = path.relative_to(tp_dir)
        key = rel_path.as_posix()
        if key in seen:
            return
        size = 0
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        references.append(
            models.ArtifactReference(
                name=path.name,
                relative_path=key,
                category=category,
                size_bytes=size,
            )
        )
        seen.add(key)

    for relative, category in IMPORTANT_ARTIFACTS.items():
        candidate = tp_dir / relative
        if candidate.exists():
            _register(candidate, category)

    reports_dir = tp_dir / "Reports"
    if reports_dir.exists():
        for child in reports_dir.iterdir():
            if child.is_file():
                _register(child, "report-extra")

    return references


def build_tp_metadata(
    product: Optional[models.ProductConfig],
    integration: models.IntegrationReport,
    cake_entries: List[models.CakeAuditEntry],
    setpoints: List[models.SetpointEntry],
    artifacts: List[models.ArtifactReference],
) -> models.TestProgramMetadata:
    flow_names = [table.name for table in integration.flow_tables]
    dll_summary = [f"{entry.name}:{entry.version}" for entry in integration.dll_inventory]
    gsds_map: Dict[str, Dict[str, str]] = {}
    for entry in cake_entries:
        domain = entry.domain_name or "unknown"
        bucket = gsds_map.setdefault(domain, {})
        bucket[entry.shift_name] = entry.gsds

    return models.TestProgramMetadata(
        product_code=product.product_code if product else None,
        product_name=product.product_name if product else None,
        network_path=product.network_path if product else None,
        flow_table_names=flow_names,
        dll_summary=dll_summary,
        gsds_mappings=gsds_map,
        artifact_references=artifacts,
        setpoints=setpoints,
    )


def read_git_hash(tp_dir: Path, fallback: str = "unknown") -> str:
    for relative in GIT_INFO_CANDIDATES:
        candidate = tp_dir / relative
        if not candidate.exists():
            continue
        try:
            for line in candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
                stripped = line.strip()
                if stripped.lower().startswith("githash"):
                    _, _, value = stripped.partition(":")
                    value = value.strip()
                    if value:
                        return value
        except OSError:
            continue
    return fallback


def run_ingestion(
    tp_name: str,
    settings: IngestSettings,
    *,
    report_path: Optional[Path] = None,
    git_hash: Optional[str] = None,
    no_persist: bool = False,
    product_config_path: Optional[Path] = None,
    product_code: Optional[str] = None,
) -> Dict[str, Any]:
    tp_dir = settings.tp_root / tp_name
    if report_path is None:
        report_path = tp_dir / "Reports" / "Integration_Report.txt"
    else:
        report_path = report_path.resolve()
        tp_dir = report_path.parent.parent
    report_path = report_path.resolve()
    if not report_path.exists():
        raise FileNotFoundError(f"Integration report not found at {report_path}")
    tp_dir = tp_dir.resolve()
    if not tp_dir.exists():
        raise FileNotFoundError(f"TP directory not found at {tp_dir}")

    product_config_path = product_config_path or (settings.repo_root / "Products.json")
    product_config: Optional[models.ProductConfig] = None
    product_warning: Optional[str] = None
    if product_config_path:
        try:
            configs = load_product_configs(product_config_path)
            product_config = find_product_config(configs, tp_name, product_code)
            if product_config is None:
                product_warning = (
                    f"No product config entry matched TP {tp_name} in {product_config_path}"
                )
        except FileNotFoundError:
            product_warning = f"Product config file not found at {product_config_path}"
        except ValueError as exc:
            product_warning = f"Unable to parse product config file {product_config_path}: {exc}"

    resolved_git_hash = git_hash or read_git_hash(tp_dir)

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
    setpoints_result = SetpointsParser().parse(tp_dir / "Modules")
    artifacts = collect_artifact_references(tp_dir)
    metadata = build_tp_metadata(
        product=product_config,
        integration=integration,
        cake_entries=cake_result.entries,
        setpoints=setpoints_result.entries,
        artifacts=artifacts,
    )
    payload: Dict[str, Any] = {
        "tp_name": tp_name,
        "git_hash": resolved_git_hash,
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
        "pas_records_count": len(pas_result.records),
        "plist_entries_count": len(plist_result.entries),
        "cake_audit_count": len(cake_result.entries),
        "vmin_search_count": len(vmin_result.records),
        "scoreboard_entries_count": len(scoreboard_result.entries),
        "setpoint_entries_count": len(setpoints_result.entries),
        "artifact_reference_count": len(artifacts),
        "product": {
            "product_code": metadata.product_code,
            "product_name": metadata.product_name,
            "network_path": metadata.network_path,
        },
        "flow_table_names": metadata.flow_table_names,
        "dll_summary": metadata.dll_summary,
        "gsds_mappings": metadata.gsds_mappings,
    }
    warnings: List[str] = []
    warnings.extend(integration.warnings)
    warnings.extend(pas_result.warnings)
    warnings.extend(plist_result.warnings)
    warnings.extend(cake_result.warnings)
    warnings.extend(vmin_result.warnings)
    warnings.extend(scoreboard_result.warnings)
    warnings.extend(setpoints_result.warnings)
    if product_warning:
        warnings.append(product_warning)
    payload["warnings"] = warnings

    if not no_persist:
        mongo_settings = settings.mongo
        artifact = models.IngestArtifact(
            tp_name=tp_name,
            git_hash=resolved_git_hash,
            report=integration,
            metadata=metadata,
        )
        writer = MongoWriter(
            mongo_settings.uri,
            mongo_settings.database,
            mongo_settings.collection,
            mongo_settings.pas_collection,
            mongo_settings.plist_collection,
            mongo_settings.cake_collection,
            mongo_settings.vmin_collection,
            mongo_settings.scoreboard_collection,
            mongo_settings.product_collection,
            mongo_settings.setpoints_collection,
        )
        doc_id = writer.write_ingest_artifact(artifact)
        pas_rows = writer.write_pas_records(tp_name, resolved_git_hash, pas_result.records)
        plist_rows = writer.write_plist_entries(tp_name, resolved_git_hash, plist_result.entries)
        cake_rows = writer.write_cake_audit_entries(tp_name, resolved_git_hash, cake_result.entries)
        vmin_rows = writer.write_vmin_search_records(tp_name, resolved_git_hash, vmin_result.records)
        scoreboard_rows = writer.write_scoreboard_entries(tp_name, resolved_git_hash, scoreboard_result.entries)
        setpoint_rows = writer.write_setpoint_entries(tp_name, resolved_git_hash, setpoints_result.entries)
        product_doc_id: Optional[str] = None
        if product_config:
            product_doc_id = writer.upsert_product_config(product_config)
        writer.close()
        payload["mongo_doc_id"] = doc_id
        payload["pas_records_persisted"] = pas_rows
        payload["plist_entries_persisted"] = plist_rows
        payload["cake_audit_persisted"] = cake_rows
        payload["vmin_search_persisted"] = vmin_rows
        payload["scoreboard_entries_persisted"] = scoreboard_rows
        payload["setpoint_entries_persisted"] = setpoint_rows
        if product_doc_id:
            payload["product_doc_id"] = product_doc_id
    else:
        payload["mongo_doc_id"] = "skipped"

    return payload


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
    parser.add_argument("--git-hash", default=None, help="Optional git hash override (auto-detected when omitted).")
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Skip writing results to MongoDB (defaults to persisting).",
    )
    parser.add_argument(
        "--product-config",
        type=Path,
        default=None,
        help="Optional path to Products.json (defaults to repo root / Products.json).",
    )
    parser.add_argument(
        "--product-code",
        default=None,
        help="Optional product code override when selecting the product config entry.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = IngestSettings.from_env(repo_root=args.repo_root)
    payload = run_ingestion(
        tp_name=args.tp_name,
        settings=settings,
        report_path=args.report,
        git_hash=args.git_hash,
        no_persist=args.no_persist,
        product_config_path=args.product_config,
        product_code=args.product_code,
    )
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
