"""MongoDB persistence helpers for TP ingestion artifacts."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict

from pymongo import MongoClient

try:
    import mongomock
except ImportError:  # pragma: no cover - optional dependency
    mongomock = None

from . import models


def integration_report_to_dict(report: models.IntegrationReport) -> Dict[str, Any]:
    """Serialize an IntegrationReport dataclass into plain dicts fit for Mongo storage."""
    payload = asdict(report)
    payload["path"] = str(report.path)
    return payload


def _build_client(uri: str) -> MongoClient:
    if uri.startswith("mongomock://"):
        if mongomock is None:
            raise RuntimeError("mongomock URI requested but mongomock is not installed.")
        return mongomock.MongoClient()
    return MongoClient(uri)


class MongoWriter:
    """Thin wrapper that upserts ingestion documents into MongoDB."""

    def __init__(self, uri: str, db_name: str, collection: str = "ingest_artifacts") -> None:
        self._client = _build_client(uri)
        self._collection = self._client[db_name][collection]

    def upsert_report(self, tp_name: str, git_hash: str, report: models.IntegrationReport) -> str:
        """Upsert the report document keyed by TP name + git hash."""
        document_id = f"{tp_name}:{git_hash}"
        report_dict = integration_report_to_dict(report)
        doc = {
            "_id": document_id,
            "tp_name": tp_name,
            "git_hash": git_hash,
            "ingested_at": datetime.now(timezone.utc),
            "program": report_dict.get("program", {}),
            "environment": report_dict.get("environment", {}),
            "shared_components": report_dict.get("shared_components", []),
            "tp_modules": report_dict.get("tp_modules", []),
            "flows_raw": report_dict.get("flows_raw", {}),
            "flow_tables": report_dict.get("flow_tables", []),
            "dll_inventory": report_dict.get("dll_inventory", []),
            "warnings": report_dict.get("warnings", []),
        }
        self._collection.update_one({"_id": document_id}, {"$set": doc}, upsert=True)
        return document_id

    def close(self) -> None:
        self._client.close()
