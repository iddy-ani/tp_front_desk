"""Generate minimal report artifacts when they are missing.

This is intentionally conservative: it only creates stub files that satisfy the ingestion
parsers so the pipeline can proceed, and it never overwrites non-empty existing files.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import List

from tp_ingest.parsers.pas import PASReportParser
from tp_ingest.parsers.module_summary import ModuleSummaryParser


def ensure_minimal_reports(tp_dir: Path, *, tp_name: str) -> List[str]:
    """Ensure required report files exist for ingestion.

    Creates minimal stubs for:
    - Reports/Integration_Report.txt (required by run_ingestion)
    - Reports/PASReport.csv (optional but avoids missing-file warnings)
    - Reports/PASReport_ModuleSummary.csv (optional)
    - Reports/PASReport_PortLevel.csv (optional)

    Returns:
        List of human-readable messages describing what was generated.
    """

    messages: List[str] = []
    reports_dir = tp_dir / "Reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    integration_path = reports_dir / "Integration_Report.txt"
    if _is_missing_or_empty(integration_path):
        integration_path.write_text(
            _build_stub_integration_report(tp_name),
            encoding="utf-8",
        )
        messages.append("Generated stub Reports/Integration_Report.txt")

    pas_path = reports_dir / "PASReport.csv"
    if _is_missing_or_empty(pas_path):
        header_line = ",".join(PASReportParser.HEADER_SEQUENCE) + "\n"
        pas_path.write_text(header_line, encoding="utf-8")
        messages.append("Generated stub Reports/PASReport.csv")

    module_summary_path = reports_dir / "PASReport_ModuleSummary.csv"
    if _is_missing_or_empty(module_summary_path):
        # DictReader-based; ensure required headers exist.
        header_line = ",".join(sorted(ModuleSummaryParser.REQUIRED_HEADERS)) + "\n"
        module_summary_path.write_text(header_line, encoding="utf-8")
        messages.append("Generated stub Reports/PASReport_ModuleSummary.csv")

    port_level_path = reports_dir / "PASReport_PortLevel.csv"
    if _is_missing_or_empty(port_level_path):
        # Reader-based; headers are free-form, but InstanceName_Port must be present for any rows.
        # Create a minimal header-only file.
        port_level_path.write_text("InstanceName_Port\n", encoding="utf-8")
        messages.append("Generated stub Reports/PASReport_PortLevel.csv")

    return messages


def _is_missing_or_empty(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        return path.stat().st_size == 0
    except OSError:
        return True


def _build_stub_integration_report(tp_name: str) -> str:
    stamp = datetime.now(timezone.utc).isoformat()
    # Keep this format compatible with IntegrationReportParser:
    # - Sections: [Program Identification], [Environment Settings], [Component Revisions]
    # - Fields use <Key> Value
    return "\n".join(
        [
            "# Auto-generated stub Integration_Report.txt",
            f"# Generated: {stamp}",
            "# Reason: required by ingestion when vendor report is missing.",
            "",
            "[Program Identification]",
            "<Program Family> UNKNOWN",
            "<Subfamily> UNKNOWN",
            f"<Base TP Name> {tp_name}",
            "<TP Revision> ",
            "<TP Git Repo URL> ",
            "",
            "[Environment Settings]",
            "<Prime Rev> ",
            "<Fuse File Rev> ",
            "",
            "[Component Revisions]",
            "<Shared>",
            "| Name  Owner  Timestamp SHA  Comment |",
            "",
            "<TP Modules>",
            "| Name  Owner  Timestamp SHA  Comment |",
            "",
        ]
    )
