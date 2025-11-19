"""Parser for PASReport_ModuleSummary.csv."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .. import models


@dataclass
class ModuleSummaryParseResult:
    entries: List[models.ModuleSummaryEntry]
    warnings: List[str]


class ModuleSummaryParser:
    """Parse module-level PAS summaries into structured entries."""

    REQUIRED_HEADERS = {
        "Module Name",
        "TotalTests",
        "TotalTestsRun",
        "TotalKill",
        "TotalEDC_E_",
        "TotalMonitor_K_",
        "TotalFloat",
        "TotalBypassed",
        "AlwaysBypassed",
        "PercentKill",
        "PercentKill+Monitor",
    }

    def parse(self, csv_path: Path) -> ModuleSummaryParseResult:
        entries: List[models.ModuleSummaryEntry] = []
        warnings: List[str] = []
        if not csv_path.exists():
            warnings.append(f"Module summary report not found at {csv_path}")
            return ModuleSummaryParseResult(entries=entries, warnings=warnings)

        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                warnings.append("Module summary report is empty")
                return ModuleSummaryParseResult(entries=entries, warnings=warnings)

            normalized_headers = {header.strip() for header in reader.fieldnames}
            missing = sorted(self.REQUIRED_HEADERS - normalized_headers)
            if missing:
                warnings.append(
                    "Module summary header mismatch; missing columns: " + ", ".join(missing)
                )

            for line_number, row in enumerate(reader, start=2):
                module_name = _clean(row.get("Module Name"))
                if not module_name:
                    if any(_clean(value) for value in row.values()):
                        warnings.append(f"Line {line_number} missing Module Name; row skipped")
                    continue
                totals = {
                    "tests": _to_int(row.get("TotalTests")),
                    "tests_run": _to_int(row.get("TotalTestsRun")),
                    "kill": _to_int(row.get("TotalKill")),
                    "edc": _to_int(row.get("TotalEDC_E_")),
                    "monitor": _to_int(row.get("TotalMonitor_K_")),
                    "float": _to_int(row.get("TotalFloat")),
                    "bypassed": _to_int(row.get("TotalBypassed")),
                    "always_bypassed": _to_int(row.get("AlwaysBypassed")),
                }
                entry = models.ModuleSummaryEntry(
                    module_name=module_name,
                    total_tests=totals["tests"],
                    total_tests_run=totals["tests_run"],
                    total_kill=totals["kill"],
                    total_edc=totals["edc"],
                    total_monitor=totals["monitor"],
                    total_float=totals["float"],
                    total_bypassed=totals["bypassed"],
                    always_bypassed=totals["always_bypassed"],
                    percent_kill=_to_float(row.get("PercentKill")),
                    percent_kill_monitor=_to_float(row.get("PercentKill+Monitor")),
                    run_rate=_ratio(totals["tests_run"], totals["tests"]),
                    kill_rate=_ratio(totals["kill"], totals["tests_run"]),
                    bypass_rate=_ratio(totals["bypassed"], totals["tests"]),
                    source_path=csv_path,
                )
                entries.append(entry)

        return ModuleSummaryParseResult(entries=entries, warnings=warnings)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _to_int(value: str | None) -> int | None:
    text = _clean(value)
    if text is None:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _to_float(value: str | None) -> float | None:
    text = _clean(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _ratio(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator
