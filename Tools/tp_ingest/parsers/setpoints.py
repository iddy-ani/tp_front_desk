"""Parser that extracts SetPoints metadata from module template (.mtpl) files."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .. import models


@dataclass
class SetpointParserResult:
    entries: List[models.SetpointEntry]
    warnings: List[str]


class SetpointsParser:
    """Lightweight parser that scans module templates for SetPoints declarations."""

    _test_start = re.compile(r"^\s*Test\s+(?P<method>\S+)\s+(?P<instance>\S+)")
    _setpoints = re.compile(r"SetPoints\s*=\s*(?P<value>[^;]+)")

    def parse(self, modules_dir: Path) -> SetpointParserResult:
        entries: List[models.SetpointEntry] = []
        warnings: List[str] = []
        if not modules_dir.exists():
            warnings.append(f"Modules directory not found at {modules_dir}")
            return SetpointParserResult(entries=entries, warnings=warnings)

        for mtpl_path in sorted(modules_dir.rglob("*.mtpl")):
            module_name = mtpl_path.parent.name
            try:
                file_entries = self._parse_file(modules_dir, mtpl_path, module_name)
            except Exception as exc:  # pragma: no cover - defensive logging only
                warnings.append(f"Failed to parse setpoints in {mtpl_path}: {exc}")
                continue
            entries.extend(file_entries)
        return SetpointParserResult(entries=entries, warnings=warnings)

    def _parse_file(self, modules_dir: Path, path: Path, module_name: str) -> List[models.SetpointEntry]:
        entries: List[models.SetpointEntry] = []
        current_test: dict | None = None
        brace_depth = 0
        relative_path = path.relative_to(modules_dir.parent)

        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                if current_test is None:
                    match = self._test_start.search(raw_line)
                    if match:
                        current_test = {
                            "method": match.group("method"),
                            "instance": match.group("instance"),
                            "values": [],
                        }
                        brace_depth = raw_line.count("{") - raw_line.count("}")
                    continue

                brace_depth += raw_line.count("{") - raw_line.count("}")
                setpoint_match = self._setpoints.search(raw_line)
                if setpoint_match:
                    value = self._clean_value(setpoint_match.group("value"))
                    current_test["values"].append(value)

                if brace_depth <= 0:
                    if current_test["values"]:
                        entries.append(
                            models.SetpointEntry(
                                module=module_name,
                                test_instance=current_test["instance"],
                                method=current_test["method"],
                                source_file=relative_path,
                                values=current_test["values"].copy(),
                            )
                        )
                    current_test = None
        return entries

    @staticmethod
    def _clean_value(raw_value: str) -> str:
        value = raw_value.strip()
        if value.startswith("\"") and value.endswith("\"") and len(value) >= 2:
            return value[1:-1]
        if value.endswith(","):
            value = value[:-1].strip()
        return value
