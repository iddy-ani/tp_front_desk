"""Dataclasses describing normalized ingestion artifacts."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class ProgramIdentification:
    program_family: str
    subfamily: str
    base_tp_name: str
    tp_revision: str
    tp_git_repo_url: str


@dataclass
class PatternRevision:
    module: str
    revision: str
    dependencies: List[str] = field(default_factory=list)


@dataclass
class ComponentRevision:
    name: str
    owner: str
    timestamp: str
    sha: str
    comment: str


@dataclass
class EnvironmentSettings:
    prime_rev: Optional[str]
    fuse_file_rev: Optional[str]
    pattern_revs: List[PatternRevision] = field(default_factory=list)


@dataclass
class IntegrationReport:
    path: Path
    program: ProgramIdentification
    environment: EnvironmentSettings
    shared_components: List[ComponentRevision] = field(default_factory=list)
    tp_modules: List[ComponentRevision] = field(default_factory=list)
    flows_raw: Dict[str, str] = field(default_factory=dict)
    flow_tables: List["FlowTable"] = field(default_factory=list)
    dll_inventory: List[DllEntry] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class FlowRow:
    columns: Dict[str, str]


@dataclass
class FlowTable:
    name: str
    columns: List[str]
    rows: List[FlowRow]


@dataclass
class DllEntry:
    name: str
    version: str
    count: Optional[int]
    supersede: bool
    path: str


@dataclass
class IngestArtifact:
    tp_name: str
    git_hash: str
    report: IntegrationReport
