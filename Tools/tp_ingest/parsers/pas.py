"""Parser for PASReport.csv providing full row fidelity."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .. import models


@dataclass
class PASParseResult:
    records: List[models.PASRecord]
    warnings: List[str]


class PASReportParser:
    """Parse PASReport.csv into structured PASRecord entries."""

    HEADER_SEQUENCE = [
        "InstanceName",
        "STATUS",
        "Bypass",
        "Bins",
        "Counters",
        "LEVEL",
        "TIMING",
        "PLIST",
        "MonitorPatCount",
        "KILLPatCount",
        "SkippedPatCount",
        "Content Directory",
        "PatternVREV",
        "TestType",
        "TpOptions",
        "Scrum",
        "ModuleName",
        "ModuleUser",
        "TestCategory",
        "Partition",
        "TestType",
        "TestTypeFlag",
        "SubFlow",
        "PatternRatio",
        "VoltageDomain",
        "Corner",
        "Frequency",
        "InstanceUser",
    ]

    def parse(self, csv_path: Path) -> PASParseResult:
        records: List[models.PASRecord] = []
        warnings: List[str] = []
        if not csv_path.exists():
            warnings.append(f"PAS report not found at {csv_path}")
            return PASParseResult(records=records, warnings=warnings)

        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            try:
                headers = next(reader)
            except StopIteration:
                warnings.append("PAS report file is empty")
                return PASParseResult(records=records, warnings=warnings)

            normalized_headers = [header.strip() for header in headers]
            if normalized_headers != self.HEADER_SEQUENCE:
                warnings.append(
                    "Unexpected PAS header sequence; parsing will continue but verify format."
                )

            expected_len = len(self.HEADER_SEQUENCE)
            for line_number, row in enumerate(reader, start=2):
                if not any(cell.strip() for cell in row):
                    continue
                padded_row = row + [""] * (expected_len - len(row))
                try:
                    record = models.PASRecord(
                        instance_name=_clean_value(padded_row, 0) or "",
                        status=_clean_value(padded_row, 1) or "",
                        bypass=_clean_value(padded_row, 2),
                        bins=_clean_value(padded_row, 3),
                        counters=_clean_value(padded_row, 4),
                        level=_clean_value(padded_row, 5),
                        timing=_clean_value(padded_row, 6),
                        plist=_clean_value(padded_row, 7),
                        monitor_pat_count=_to_int(_clean_value(padded_row, 8)),
                        kill_pat_count=_to_int(_clean_value(padded_row, 9)),
                        skipped_pat_count=_to_int(_clean_value(padded_row, 10)),
                        content_directory=_clean_value(padded_row, 11),
                        pattern_vrev=_clean_value(padded_row, 12),
                        test_type=_clean_value(padded_row, 13),
                        tp_options=_clean_value(padded_row, 14),
                        scrum=_clean_value(padded_row, 15),
                        module_name=_clean_value(padded_row, 16),
                        module_user=_clean_value(padded_row, 17),
                        test_category=_clean_value(padded_row, 18),
                        partition=_clean_value(padded_row, 19),
                        test_type_detail=_clean_value(padded_row, 20),
                        test_type_flag=_clean_value(padded_row, 21),
                        subflow=_clean_value(padded_row, 22),
                        pattern_ratio=_clean_value(padded_row, 23),
                        voltage_domain=_clean_value(padded_row, 24),
                        corner=_clean_value(padded_row, 25),
                        frequency=_clean_value(padded_row, 26),
                        instance_user=_clean_value(padded_row, 27),
                    )
                except Exception as exc:  # pragma: no cover - defensive coding
                    warnings.append(f"Failed to parse PAS line {line_number}: {exc}")
                    continue
                records.append(record)
        return PASParseResult(records=records, warnings=warnings)


def _clean_value(row: List[str], index: int) -> str | None:
    if index >= len(row):
        return None
    value = row[index].strip()
    return value or None


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
