"""Parser for VMinSearchAudit.csv report."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .. import models


@dataclass
class VMinSearchParseResult:
    records: List[models.VMinSearchRecord]
    warnings: List[str]


class VMinSearchParser:
    """Parse VMin search audit CSV into structured records."""

    def parse(self, csv_path: Path) -> VMinSearchParseResult:
        records: List[models.VMinSearchRecord] = []
        warnings: List[str] = []
        if not csv_path.exists():
            warnings.append(f"VMin search report not found at {csv_path}")
            return VMinSearchParseResult(records, warnings)

        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                warnings.append("VMin search report is empty")
                return VMinSearchParseResult(records, warnings)

            for line_number, row in enumerate(reader, start=2):
                module = _clean(row.get("Module"))
                test_name = _clean(row.get("Testname"))
                if not module or not test_name:
                    warnings.append(
                        f"VMin search line {line_number} missing Module/Testname"
                    )
                    continue
                record = models.VMinSearchRecord(
                    module=module,
                    test_name=test_name,
                    template=_clean(row.get("Template")),
                    execution_mode=_clean(row.get("executionmode")),
                    vmin_predict=_clean(row.get("VMinPredict")),
                    low_search=_clean(row.get("LowSearch")),
                    hi_search=_clean(row.get("HiSearch")),
                    gsds=_clean(row.get("GSDS")),
                    search_pin=_clean(row.get("searchpin")),
                    overshoot_recv=_clean(row.get("overshootrecv")),
                    vmin_pred_high=_clean(row.get("vminPredHigh")),
                    search_result=_to_float(row.get("searchRes")),
                )
                records.append(record)
        return VMinSearchParseResult(records, warnings)


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _to_float(value: Optional[str]) -> Optional[float]:
    text = _clean(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None
