"""Parser for plist_master.csv capturing optional plist metadata."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .. import models


@dataclass
class PlistMasterParseResult:
    entries: List[models.PlistEntry]
    warnings: List[str]


class PlistMasterParser:
    """Parse plist_master.csv rows into structured PlistEntry records."""

    _STRING_ATTRIBUTE_KEYS: Dict[str, str] = {
        "PreBurst": "pre_burst",
        "PostBurst": "post_burst",
        "PreBurstPList": "pre_burst_plist",
        "PostBurstPList": "post_burst_plist",
        "PrePattern": "pre_pattern",
        "PostPattern": "post_pattern",
        "Mask": "mask",
        "KeepAlive": "keep_alive",
    }
    _FLAG_ATTRIBUTE_KEYS: Dict[str, str] = {
        "Flatten": "flatten",
        "BurstOff": "burst_off",
    }

    def __init__(self) -> None:
        self._unknown_attribute_keys: set[str] = set()

    def parse(self, csv_path: Path) -> PlistMasterParseResult:
        entries: List[models.PlistEntry] = []
        warnings: List[str] = []
        if not csv_path.exists():
            warnings.append(f"plist master report not found at {csv_path}")
            return PlistMasterParseResult(entries=entries, warnings=warnings)

        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration:
                warnings.append("plist master report is empty")
                return PlistMasterParseResult(entries=entries, warnings=warnings)

            if len(header) < 3:
                warnings.append("plist master header missing required columns")

            for line_number, row in enumerate(reader, start=2):
                if not row or not any(cell.strip() for cell in row):
                    continue
                if len(row) < 3:
                    warnings.append(
                        f"plist master line {line_number} missing required columns"
                    )
                    continue

                pattern_name = row[0].strip()
                if not pattern_name:
                    warnings.append(
                        f"plist master line {line_number} missing pattern name"
                    )
                    continue
                total_patterns = _to_int(row[1])
                commented_patterns = _to_int(row[2])
                attrs = self._parse_attributes(row[3:], line_number, warnings)
                entry = models.PlistEntry(
                    pattern_name=pattern_name,
                    total_patterns=total_patterns,
                    commented_patterns=commented_patterns,
                    **attrs,
                )
                entries.append(entry)
        return PlistMasterParseResult(entries=entries, warnings=warnings)

    def _parse_attributes(
        self, cells: List[str], line_number: int, warnings: List[str]
    ) -> Dict[str, object]:
        attrs: Dict[str, object] = {
            "flatten": False,
            "burst_off": False,
            "pre_burst": None,
            "post_burst": None,
            "pre_burst_plist": None,
            "post_burst_plist": None,
            "pre_pattern": None,
            "post_pattern": None,
            "mask": None,
            "keep_alive": None,
        }
        for raw in cells:
            value = _clean_field(raw)
            if not value:
                continue
            key, remainder = _split_key_value(value)
            if key in self._STRING_ATTRIBUTE_KEYS:
                attrs[self._STRING_ATTRIBUTE_KEYS[key]] = remainder
            elif key in self._FLAG_ATTRIBUTE_KEYS:
                attrs[self._FLAG_ATTRIBUTE_KEYS[key]] = True
            else:
                if key not in self._unknown_attribute_keys:
                    warnings.append(
                        f"Unknown plist attribute '{key}' encountered on line {line_number}"
                    )
                    self._unknown_attribute_keys.add(key)
        return attrs


def _clean_field(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    if "#" in cleaned:
        cleaned = cleaned.split("#", 1)[0].strip()
    return cleaned


def _split_key_value(value: str) -> tuple[str, Optional[str]]:
    parts = value.split(None, 1)
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1].strip() or None


def _to_int(value: str | None) -> Optional[int]:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None
