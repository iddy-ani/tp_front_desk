"""Parser for CAKEVADTLAudit.csv."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .. import models


@dataclass
class CakeAuditParseResult:
    entries: List[models.CakeAuditEntry]
    warnings: List[str]


class CakeAuditParser:
    """Parse CAKE VADT L audit CSV into structured entries."""

    HEADERS = ["DomainName", "ShiftName", "GSDS"]

    def parse(self, csv_path: Path) -> CakeAuditParseResult:
        entries: List[models.CakeAuditEntry] = []
        warnings: List[str] = []
        if not csv_path.exists():
            warnings.append(f"CAKE audit report not found at {csv_path}")
            return CakeAuditParseResult(entries, warnings)

        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            try:
                headers = next(reader)
            except StopIteration:
                warnings.append("CAKE audit report is empty")
                return CakeAuditParseResult(entries, warnings)

            normalized = [h.strip() for h in headers]
            if normalized != self.HEADERS:
                warnings.append("Unexpected CAKE audit header sequence; proceeding regardless")

            for line_number, row in enumerate(reader, start=2):
                if not row or not any(cell.strip() for cell in row):
                    continue
                if len(row) < 3:
                    warnings.append(f"CAKE audit line {line_number} missing columns")
                    continue
                entries.append(
                    models.CakeAuditEntry(
                        domain_name=row[0].strip(),
                        shift_name=row[1].strip(),
                        gsds=row[2].strip(),
                    )
                )
        return CakeAuditParseResult(entries, warnings)
