"""Parser for StartItemList.csv (flow map)."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .. import models


@dataclass
class FlowMapParseResult:
    entries: List[models.FlowMapEntry]
    warnings: List[str]


class FlowMapParser:
    """Parse module/dutflow/instance relationships."""

    def parse(self, csv_path: Path) -> FlowMapParseResult:
        entries: List[models.FlowMapEntry] = []
        warnings: List[str] = []
        if not csv_path.exists():
            warnings.append(f"Flow map report not found at {csv_path}")
            return FlowMapParseResult(entries=entries, warnings=warnings)

        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            try:
                next(reader)
            except StopIteration:
                warnings.append("Flow map report is empty")
                return FlowMapParseResult(entries=entries, warnings=warnings)

            sequence_index = 0
            for row in reader:
                module = _clean(row, 0)
                dutflow = _clean(row, 1)
                instance = _clean(row, 2)
                if not module and not instance:
                    continue
                entries.append(
                    models.FlowMapEntry(
                        module=module or "unknown",
                        dutflow=dutflow or "unknown",
                        instance=instance or "unknown",
                        sequence_index=sequence_index,
                    )
                )
                sequence_index += 1

        return FlowMapParseResult(entries=entries, warnings=warnings)


def _clean(row: List[str], index: int) -> str | None:
    if index >= len(row):
        return None
    value = row[index]
    if value is None:
        return None
    text = value.strip()
    return text or None
