"""MongoDB persistence helpers for TP ingestion artifacts."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from pymongo import MongoClient

try:
    import mongomock
except ImportError:  # pragma: no cover - optional dependency
    mongomock = None

from . import models
from .serialization import ingest_artifact_to_document


def _build_client(uri: str) -> MongoClient:
    if uri.startswith("mongomock://"):
        if mongomock is None:
            raise RuntimeError("mongomock URI requested but mongomock is not installed.")
        return mongomock.MongoClient()
    return MongoClient(uri)


class DatabaseWriter:
    """Abstract writer so ingestion can be tested without a live database."""

    def write_ingest_artifact(self, artifact: models.IngestArtifact) -> str:  # pragma: no cover - interface only
        raise NotImplementedError


class MongoWriter(DatabaseWriter):
    """Thin wrapper that upserts ingestion documents into MongoDB."""

    def __init__(self, uri: str, db_name: str, collection: str = "ingest_artifacts") -> None:
        self._client = _build_client(uri)
        self._collection = self._client[db_name][collection]

    def write_ingest_artifact(self, artifact: models.IngestArtifact) -> str:
        document_id = f"{artifact.tp_name}:{artifact.git_hash}"
        doc = ingest_artifact_to_document(artifact)
        doc["_id"] = document_id
        doc["ingested_at"] = datetime.now(timezone.utc)
        self._collection.update_one({"_id": document_id}, {"$set": doc}, upsert=True)
        return document_id

    def upsert_report(self, tp_name: str, git_hash: str, report: models.IntegrationReport) -> str:
        """Backwards-compatible helper for existing callers."""
        artifact = models.IngestArtifact(tp_name=tp_name, git_hash=git_hash, report=report)
        return self.write_ingest_artifact(artifact)

    def close(self) -> None:
        self._client.close()
