"""Parser for PASReport_PortLevel.csv."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from .. import models


@dataclass
class PortResultsParseResult:
    entries: List[models.PortResultRow]
    warnings: List[str]


class PortResultsParser:
    """Parse the verbose port-level PAS report."""

    def parse(self, csv_path: Path) -> PortResultsParseResult:
        entries: List[models.PortResultRow] = []
        warnings: List[str] = []
        if not csv_path.exists():
            warnings.append(f"Port-level PAS report not found at {csv_path}")
            return PortResultsParseResult(entries=entries, warnings=warnings)

        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            try:
                headers = next(reader)
            except StopIteration:
                warnings.append("Port-level PAS report is empty")
                return PortResultsParseResult(entries=entries, warnings=warnings)

            header_labels = _normalize_headers(headers)
            for line_number, row in enumerate(reader, start=2):
                if not _row_has_data(row):
                    continue
                row_map = _map_row(row, header_labels)
                instance_name_port = row_map.get("InstanceName_Port")
                if not instance_name_port:
                    warnings.append(
                        f"Line {line_number} missing InstanceName_Port; row skipped"
                    )
                    continue
                port_value = row_map.get("Port")
                instance_name = _strip_port_suffix(instance_name_port, port_value)
                entry = models.PortResultRow(
                    instance_name_port=instance_name_port,
                    status=row_map.get("STATUS"),
                    bypass=row_map.get("Bypass"),
                    bin=_to_int(row_map.get("Bin")),
                    hb=_to_int(row_map.get("HB")),
                    sb=_to_int(row_map.get("SB")),
                    counter=_to_int(row_map.get("Counter")),
                    plist=row_map.get("PLIST"),
                    monitor_pat_count=_to_int(row_map.get("MonitorPatCount")),
                    kill_pat_count=_to_int(row_map.get("KILLPatCount")),
                    skipped_pat_count=_to_int(row_map.get("SkippedPatCount")),
                    content_directory=row_map.get("Content Directory"),
                    pattern_vrev=row_map.get("PatternVREV"),
                    test_type=row_map.get("TestType"),
                    tp_options=row_map.get("TpOptions"),
                    scrum=row_map.get("Scrum"),
                    module_name=row_map.get("ModuleName"),
                    module_user=row_map.get("ModuleUser"),
                    test_category=row_map.get("TestCategory"),
                    partition=row_map.get("Partition"),
                    test_type_flag=row_map.get("TestTypeFlag"),
                    subflow=row_map.get("SubFlow"),
                    pattern_ratio=row_map.get("PatternRatio"),
                    voltage_domain=row_map.get("VoltageDomain"),
                    corner=row_map.get("Corner"),
                    frequency=row_map.get("Frequency"),
                    port_owner=row_map.get("ModuleUser__2"),
                    port=port_value,
                    instance_name=instance_name,
                    module_summary_name=row_map.get("ModuleName"),
                )
                entries.append(entry)
        return PortResultsParseResult(entries=entries, warnings=warnings)


def _normalize_headers(headers: List[str]) -> Dict[int, str]:
    label_counts: Dict[str, int] = {}
    normalized: Dict[int, str] = {}
    for idx, raw_header in enumerate(headers):
        header = (raw_header or "").strip()
        count = label_counts.get(header, 0) + 1
        label_counts[header] = count
        label = header
        if header == "ModuleUser" and count > 1:
            label = "ModuleUser__2"
        elif header == "TestType" and count > 1:
            label = "TestTypeDetail"
        normalized[idx] = label or f"column_{idx}"
    return normalized


def _map_row(row: List[str], header_labels: Dict[int, str]) -> Dict[str, str | None]:
    mapped: Dict[str, str | None] = {}
    for idx, value in enumerate(row):
        label = header_labels.get(idx)
        if not label:
            continue
        mapped[label] = (value.strip() or None) if value is not None else None
    return mapped


def _row_has_data(row: List[str]) -> bool:
    return any(cell.strip() for cell in row if cell)


def _to_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        try:
            return int(float(value))
        except ValueError:
            return None


def _strip_port_suffix(instance_name_port: str, port_value: str | None) -> str:
    if port_value and instance_name_port.endswith(f"_{port_value}"):
        suffix_length = len(port_value) + 1
        if suffix_length < len(instance_name_port):
            return instance_name_port[:-suffix_length]
    return instance_name_port
