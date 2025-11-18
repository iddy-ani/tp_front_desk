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
class PASRecord:
    instance_name: str
    status: str
    bypass: Optional[str]
    bins: Optional[str]
    counters: Optional[str]
    level: Optional[str]
    timing: Optional[str]
    plist: Optional[str]
    monitor_pat_count: Optional[int]
    kill_pat_count: Optional[int]
    skipped_pat_count: Optional[int]
    content_directory: Optional[str]
    pattern_vrev: Optional[str]
    test_type: Optional[str]
    tp_options: Optional[str]
    scrum: Optional[str]
    module_name: Optional[str]
    module_user: Optional[str]
    test_category: Optional[str]
    partition: Optional[str]
    test_type_detail: Optional[str]
    test_type_flag: Optional[str]
    subflow: Optional[str]
    pattern_ratio: Optional[str]
    voltage_domain: Optional[str]
    corner: Optional[str]
    frequency: Optional[str]
    instance_user: Optional[str]


@dataclass
class PlistEntry:
    pattern_name: str
    total_patterns: Optional[int]
    commented_patterns: Optional[int]
    flatten: bool = False
    burst_off: bool = False
    pre_burst: Optional[str] = None
    post_burst: Optional[str] = None
    pre_burst_plist: Optional[str] = None
    post_burst_plist: Optional[str] = None
    pre_pattern: Optional[str] = None
    post_pattern: Optional[str] = None
    mask: Optional[str] = None
    keep_alive: Optional[str] = None


@dataclass
class CakeAuditEntry:
    domain_name: str
    shift_name: str
    gsds: str


@dataclass
class VMinSearchRecord:
    module: str
    test_name: str
    template: Optional[str]
    execution_mode: Optional[str]
    vmin_predict: Optional[str]
    low_search: Optional[str]
    hi_search: Optional[str]
    gsds: Optional[str]
    search_pin: Optional[str]
    overshoot_recv: Optional[str]
    vmin_pred_high: Optional[str]
    search_result: Optional[float]


@dataclass
class ScoreboardEntry:
    module: str
    test_instance: str
    base_number: Optional[int]
    duplicate: Optional[int]
    in_range: Optional[bool]
    extra_info: Optional[str]


@dataclass
class IngestArtifact:
    tp_name: str
    git_hash: str
    report: IntegrationReport
