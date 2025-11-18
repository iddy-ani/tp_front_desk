"""Utilities for converting ingestion dataclasses into Mongo-friendly dicts."""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, Dict

from . import models


def _to_mongo(value: Any) -> Any:
    """Recursively convert dataclasses, Paths, and containers into Mongo-safe types."""
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        result: Dict[str, Any] = {}
        for field in fields(value):
            result[field.name] = _to_mongo(getattr(value, field.name))
        return result
    if isinstance(value, list):
        return [_to_mongo(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_mongo(item) for key, item in value.items()}
    return value


def integration_report_to_document(report: models.IntegrationReport) -> Dict[str, Any]:
    """Serialize an IntegrationReport into a plain dictionary."""
    return _to_mongo(report)


def ingest_artifact_to_document(artifact: models.IngestArtifact) -> Dict[str, Any]:
    """Serialize the top-level ingest artifact (program, git hash, report)."""
    return _to_mongo(artifact)


def pas_record_to_document(record: models.PASRecord) -> Dict[str, Any]:
    return {
        "instance_name": record.instance_name,
        "status": record.status,
        "bypass": record.bypass,
        "bins": record.bins,
        "counters": record.counters,
        "level": record.level,
        "timing": record.timing,
        "plist": record.plist,
        "monitor_pat_count": record.monitor_pat_count,
        "kill_pat_count": record.kill_pat_count,
        "skipped_pat_count": record.skipped_pat_count,
        "content_directory": record.content_directory,
        "pattern_vrev": record.pattern_vrev,
        "test_type": record.test_type,
        "tp_options": record.tp_options,
        "scrum": record.scrum,
        "module_name": record.module_name,
        "module_user": record.module_user,
        "test_category": record.test_category,
        "partition": record.partition,
        "test_type_detail": record.test_type_detail,
        "test_type_flag": record.test_type_flag,
        "subflow": record.subflow,
        "pattern_ratio": record.pattern_ratio,
        "voltage_domain": record.voltage_domain,
        "corner": record.corner,
        "frequency": record.frequency,
        "instance_user": record.instance_user,
    }
