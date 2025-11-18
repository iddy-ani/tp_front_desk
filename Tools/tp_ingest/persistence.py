"""MongoDB persistence helpers for TP ingestion artifacts."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable

from pymongo import ASCENDING, MongoClient

try:
    import mongomock
except ImportError:  # pragma: no cover - optional dependency
    mongomock = None

from . import models
from .serialization import (
    ingest_artifact_to_document,
    pas_record_to_document,
    plist_entry_to_document,
)


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

    def __init__(
        self,
        uri: str,
        db_name: str,
        collection: str = "ingest_artifacts",
        pas_collection: str = "pas_records",
        plist_collection: str = "plist_entries",
    ) -> None:
        self._client = _build_client(uri)
        self._collection = self._client[db_name][collection]
        self._pas_collection = self._client[db_name][pas_collection]
        self._plist_collection = self._client[db_name][plist_collection]
        self._is_mock = mongomock is not None and isinstance(self._client, mongomock.MongoClient)
        self._ensure_indexes()

    def write_ingest_artifact(self, artifact: models.IngestArtifact) -> str:
        document_id = f"{artifact.tp_name}:{artifact.git_hash}"
        doc = ingest_artifact_to_document(artifact)
        doc["_id"] = document_id
        doc["ingested_at"] = datetime.now(timezone.utc)
        self._collection.update_one({"_id": document_id}, {"$set": doc}, upsert=True)
        return document_id

    def _ensure_indexes(self) -> None:
        """Create the indexes expected by the ingestion/query paths."""
        if self._is_mock:
            return

        self._collection.create_index(
            [("tp_name", ASCENDING), ("git_hash", ASCENDING)], name="tp_git_idx"
        )
        self._collection.create_index(
            [("report.dll_inventory.name", ASCENDING)], name="dll_inventory_name_idx"
        )
        self._pas_collection.create_index(
            [("tp_document_id", ASCENDING)], name="pas_tp_idx"
        )
        self._pas_collection.create_index(
            [("module_name", ASCENDING), ("instance_name", ASCENDING)],
            name="pas_module_instance_idx",
        )
        self._plist_collection.create_index(
            [("tp_document_id", ASCENDING)], name="plist_tp_idx"
        )
        self._plist_collection.create_index(
            [("pattern_name", ASCENDING)], name="plist_pattern_idx"
        )

    def write_pas_records(
        self, tp_name: str, git_hash: str, records: Iterable[models.PASRecord]
    ) -> int:
        """Replace PAS rows for the given TP revision."""
        tp_document_id = f"{tp_name}:{git_hash}"
        docs = []
        for record in records:
            doc = pas_record_to_document(record)
            doc["tp_document_id"] = tp_document_id
            docs.append(doc)
        self._pas_collection.delete_many({"tp_document_id": tp_document_id})
        if docs:
            self._pas_collection.insert_many(docs)
        return len(docs)

    def write_plist_entries(
        self, tp_name: str, git_hash: str, entries: Iterable[models.PlistEntry]
    ) -> int:
        tp_document_id = f"{tp_name}:{git_hash}"
        docs = []
        for entry in entries:
            doc = plist_entry_to_document(entry)
            doc["tp_document_id"] = tp_document_id
            docs.append(doc)
        self._plist_collection.delete_many({"tp_document_id": tp_document_id})
        if docs:
            self._plist_collection.insert_many(docs)
        return len(docs)

    def upsert_report(self, tp_name: str, git_hash: str, report: models.IntegrationReport) -> str:
        """Backwards-compatible helper for existing callers."""
        artifact = models.IngestArtifact(tp_name=tp_name, git_hash=git_hash, report=report)
        return self.write_ingest_artifact(artifact)

    def close(self) -> None:
        self._client.close()
