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
