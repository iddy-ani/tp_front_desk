"""Parser for `Integration_Report.txt` content."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from .. import models

_SECTION_PATTERN = re.compile(r"^\[(?P<name>.+?)\]\s*$")
_FIELD_PATTERN = re.compile(r"^<(?P<key>[^>]+)>\s*(?P<value>.*)$")
_DOUBLE_SPACE_SPLIT = re.compile(r"\s{2,}")


@dataclass
class _SectionedText:
    sections: Dict[str, List[str]]

    @classmethod
    def from_lines(cls, lines: Iterable[str]) -> "_SectionedText":
        sections: Dict[str, List[str]] = {}
        current_name: str | None = None
        current_buffer: List[str] = []

        def commit() -> None:
            nonlocal current_name, current_buffer
            if current_name is not None:
                sections[current_name] = current_buffer.copy()
            current_buffer = []

        for raw_line in lines:
            line = raw_line.rstrip("\n")
            if line.startswith("#-" ):
                # Separator from Cake output.
                continue
            section_match = _SECTION_PATTERN.match(line.strip())
            if section_match:
                commit()
                name = section_match.group("name").strip().lower().replace(" ", "_")
                current_name = name
                continue
            if current_name is not None:
                current_buffer.append(line)
        commit()
        return cls(sections=sections)


class IntegrationReportParser:
    """Parses key metadata from `Integration_Report.txt`."""

    def parse(self, path: Path) -> models.IntegrationReport:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        sectioned = _SectionedText.from_lines(lines)
        program_section = sectioned.sections.get("program_identification", [])
        environment_section = sectioned.sections.get("environment_settings", [])
        component_section = sectioned.sections.get("component_revisions", [])
        self._warnings: List[str] = []
        flow_sections = {
            key: "\n".join(value)
            for key, value in sectioned.sections.items()
            if key in {"tp_flow_structure", "fork_flows", "special_flows"}
        }

        program = self._parse_program(program_section)
        environment = self._parse_environment(environment_section)
        shared_components, tp_modules = self._parse_components(component_section)
        flow_tables = self._parse_flow_tables(sectioned)
        dll_inventory = self._parse_dll_inventory(path.parent / "CAKE_DLLVersions.csv")
        return models.IntegrationReport(
            path=path,
            program=program,
            environment=environment,
            shared_components=shared_components,
            tp_modules=tp_modules,
            flows_raw=flow_sections,
            flow_tables=flow_tables,
            dll_inventory=dll_inventory,
            warnings=self._warnings.copy(),
        )

    def _parse_program(self, lines: List[str]) -> models.ProgramIdentification:
        data = self._collect_fields(lines)
        return models.ProgramIdentification(
            program_family=data.get("Program Family", ""),
            subfamily=data.get("Subfamily", ""),
            base_tp_name=data.get("Base TP Name", ""),
            tp_revision=data.get("TP Revision", ""),
            tp_git_repo_url=data.get("TP Git Repo URL", ""),
        )

    def _parse_environment(self, lines: List[str]) -> models.EnvironmentSettings:
        data = self._collect_fields(lines)
        pattern_table = self._extract_pattern_table(lines)
        return models.EnvironmentSettings(
            prime_rev=data.get("Prime Rev"),
            fuse_file_rev=data.get("Fuse File Rev"),
            pattern_revs=pattern_table,
        )

    def _parse_components(self, lines: List[str]) -> tuple[List[models.ComponentRevision], List[models.ComponentRevision]]:
        shared: List[models.ComponentRevision] = []
        modules: List[models.ComponentRevision] = []
        current_target: List[models.ComponentRevision] | None = None
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("<Shared>"):
                current_target = shared
                continue
            if stripped.startswith("<TP Modules>"):
                current_target = modules
                continue
            if current_target is None:
                continue
            if stripped.startswith("|"):
                # header row
                continue
            record = self._parse_component_row(stripped)
            if record:
                current_target.append(record)
        return shared, modules

    def _parse_component_row(self, stripped: str) -> models.ComponentRevision | None:
        cells = [cell for cell in _DOUBLE_SPACE_SPLIT.split(stripped) if cell]
        if len(cells) < 4:
            self._warnings.append(f"Component row malformed: '{stripped}'")
            return None
        name, owner, timestamp_sha, comment = cells[0], cells[1], cells[2], cells[3]
        timestamp, sha = self._split_timestamp_sha(timestamp_sha)
        return models.ComponentRevision(name=name, owner=owner, timestamp=timestamp, sha=sha, comment=comment)

    def _parse_flow_tables(self, sectioned: _SectionedText) -> List[models.FlowTable]:
        tables: List[models.FlowTable] = []
        for section_key, friendly in (
            ("tp_flow_structure", "TP Flow Structure"),
            ("fork_flows", "Fork Flows"),
            ("special_flows", "Special Flows"),
        ):
            lines = sectioned.sections.get(section_key, [])
            tables.extend(self._extract_flow_tables(lines, friendly))
        return tables

    def _extract_flow_tables(self, lines: List[str], default_name: str) -> List[models.FlowTable]:
        tables: List[models.FlowTable] = []
        current: Tuple[str, List[str], List[models.FlowRow]] | None = None
        for raw in lines:
            line = raw.rstrip()
            if not line:
                continue
            if line.startswith("<") and ">" in line:
                if current:
                    name, columns, rows = current
                    tables.append(models.FlowTable(name=name, columns=columns, rows=rows))
                name = line[line.find("<") + 1 : line.find(">")].strip()
                columns = self._parse_flow_headers(line)
                if not columns:
                    columns = []
                current = (name or default_name, columns, [])
                continue
            if current is None:
                continue
            _, columns, rows = current
            row_dict = self._split_flow_row(line, columns)
            if row_dict is None:
                continue
            rows.append(models.FlowRow(columns=row_dict))
        if current:
            name, columns, rows = current
            tables.append(models.FlowTable(name=name, columns=columns, rows=rows))
        return tables

    def _parse_flow_headers(self, header_line: str) -> List[str]:
        if "|" not in header_line:
            # fallback: squeeze whitespace
            columns = [token.strip() for token in _DOUBLE_SPACE_SPLIT.split(header_line) if token.strip()]
            return columns[1:] if columns else []
        parts = [part.strip() for part in header_line.split("|") if part.strip()]
        if parts and parts[0].startswith("<"):
            parts = parts[1:]
        return parts

    def _split_flow_row(self, line: str, columns: List[str]) -> Dict[str, str] | None:
        if not columns:
            return None
        tokens = [token.strip() for token in _DOUBLE_SPACE_SPLIT.split(line.strip()) if token.strip()]
        if not tokens:
            return None
        if len(tokens) > len(columns):
            self._warnings.append(
                f"Flow row has {len(tokens)} columns but expected {len(columns)}: '{line.strip()}'"
            )
        row: Dict[str, str] = {}
        for idx, column in enumerate(columns):
            row[column] = tokens[idx] if idx < len(tokens) else ""
        return row

    def _parse_dll_inventory(self, dll_path: Path) -> List[models.DllEntry]:
        entries: List[models.DllEntry] = []
        if not dll_path.exists():
            self._warnings.append(f"DLL inventory missing at {dll_path}")
            return entries
        try:
            with dll_path.open(encoding="utf-8", errors="ignore") as handle:
                reader = csv.reader(handle)
                header = next(reader, None)
                if header is None:
                    self._warnings.append(f"DLL inventory empty at {dll_path}")
                    return entries
                for idx, row in enumerate(reader, start=2):
                    if not row or all(not cell.strip() for cell in row):
                        continue
                    if len(row) < 5:
                        self._warnings.append(
                            f"DLL row {idx} malformed (expected 5 columns, got {len(row)}): '{','.join(row)}'"
                        )
                        continue
                    name = row[0].strip()
                    version = row[1].strip()
                    count_str = row[2].strip()
                    supersede_str = row[3].strip()
                    path_value = ",".join(row[4:]).strip()
                    if not name:
                        self._warnings.append(f"DLL row {idx} missing name: '{','.join(row)}'")
                        continue
                    count = None
                    if count_str:
                        try:
                            count = int(count_str)
                        except ValueError:
                            self._warnings.append(
                                f"DLL row {idx} has non-integer count '{count_str}' for {name}"
                            )
                    supersede = supersede_str.lower() == "true"
                    entries.append(
                        models.DllEntry(
                            name=name,
                            version=version,
                            count=count,
                            supersede=supersede,
                            path=path_value,
                        )
                    )
        except OSError as exc:
            self._warnings.append(f"Failed reading DLL inventory at {dll_path}: {exc}")
        return entries

    def _split_timestamp_sha(self, value: str) -> tuple[str, str]:
        parts = value.split()
        if not parts:
            return "", ""
        if len(parts) == 1:
            return parts[0], ""
        return parts[0], parts[1]

    def _extract_pattern_table(self, lines: List[str]) -> List[models.PatternRevision]:
        pattern_rows: List[models.PatternRevision] = []
        collecting = False
        for line in lines:
            if "Pattern Module" in line:
                collecting = True
                continue
            if not collecting:
                continue
            stripped = line.strip()
            if not stripped:
                break
            if stripped.startswith("#"):
                continue
            match = re.match(r"(?P<module>\S+)\s+(?P<rev>\S+)\s+(?P<deps>.+)", stripped)
            if not match:
                continue
            deps = [dep.strip() for dep in match.group("deps").split(",") if dep.strip()]
            pattern_rows.append(
                models.PatternRevision(
                    module=match.group("module"),
                    revision=match.group("rev"),
                    dependencies=deps,
                )
            )
        return pattern_rows

    def _collect_fields(self, lines: List[str]) -> Dict[str, str]:
        collected: Dict[str, str] = {}
        for line in lines:
            match = _FIELD_PATTERN.match(line.strip())
            if match:
                key = match.group("key").strip()
                value = match.group("value").strip()
                collected[key] = value
        return collected
