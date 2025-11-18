"""Parser for ScoreBoard_Report.csv."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .. import models


@dataclass
class ScoreboardParseResult:
    entries: List[models.ScoreboardEntry]
    warnings: List[str]


class ScoreboardParser:
    """Parse ScoreBoard report rows into structured entries."""

    def parse(self, csv_path: Path) -> ScoreboardParseResult:
        entries: List[models.ScoreboardEntry] = []
        warnings: List[str] = []
        if not csv_path.exists():
            warnings.append(f"Scoreboard report not found at {csv_path}")
            return ScoreboardParseResult(entries, warnings)

        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                warnings.append("Scoreboard report is empty")
                return ScoreboardParseResult(entries, warnings)

            for line_number, row in enumerate(reader, start=2):
                module = _clean(row.get("MODULE"))
                test_instance = _clean(row.get("TESTINSTANCE"))
                if not module or not test_instance:
                    warnings.append(
                        f"Scoreboard line {line_number} missing MODULE/TESTINSTANCE"
                    )
                    continue
                entry = models.ScoreboardEntry(
                    module=module,
                    test_instance=test_instance,
                    base_number=_to_int(row.get("BASENUMBER")),
                    duplicate=_to_int(row.get("DUPLICATE")),
                    in_range=_to_bool(row.get("INRANGE")),
                    extra_info=_clean(row.get("EXTRA_INFO")),
                )
                entries.append(entry)
        return ScoreboardParseResult(entries, warnings)


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _to_int(value: Optional[str]) -> Optional[int]:
    text = _clean(value)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _to_bool(value: Optional[str]) -> Optional[bool]:
    text = _clean(value)
    if text is None:
        return None
    lowered = text.lower()
    if lowered in {"true", "1", "yes", "y"}:
        return True
    if lowered in {"false", "0", "no", "n"}:
        return False
    return None
