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
    cake_audit_entry_to_document,
    ingest_artifact_to_document,
    pas_record_to_document,
    plist_entry_to_document,
    product_config_to_document,
    scoreboard_entry_to_document,
    setpoint_entry_to_document,
    vmin_search_record_to_document,
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
        cake_collection: str = "cake_audit",
        vmin_collection: str = "vmin_search",
        scoreboard_collection: str = "scoreboard_entries",
        product_collection: str = "product_configs",
        setpoints_collection: str = "setpoints",
    ) -> None:
        self._client = _build_client(uri)
        self._collection = self._client[db_name][collection]
        self._pas_collection = self._client[db_name][pas_collection]
        self._plist_collection = self._client[db_name][plist_collection]
        self._cake_collection = self._client[db_name][cake_collection]
        self._vmin_collection = self._client[db_name][vmin_collection]
        self._scoreboard_collection = self._client[db_name][scoreboard_collection]
        self._product_collection = self._client[db_name][product_collection]
        self._setpoints_collection = self._client[db_name][setpoints_collection]
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
        self._cake_collection.create_index(
            [("tp_document_id", ASCENDING)], name="cake_tp_idx"
        )
        self._cake_collection.create_index(
            [("domain_name", ASCENDING), ("shift_name", ASCENDING)],
            name="cake_domain_shift_idx",
        )
        self._vmin_collection.create_index(
            [("tp_document_id", ASCENDING)], name="vmin_tp_idx"
        )
        self._vmin_collection.create_index(
            [("module", ASCENDING), ("test_name", ASCENDING)],
            name="vmin_module_test_idx",
        )
        self._scoreboard_collection.create_index(
            [("tp_document_id", ASCENDING)], name="scoreboard_tp_idx"
        )
        self._scoreboard_collection.create_index(
            [("module", ASCENDING), ("test_instance", ASCENDING)],
            name="scoreboard_module_test_idx",
        )
        self._product_collection.create_index([("product_code", ASCENDING)], name="product_code_idx", unique=True)
        self._setpoints_collection.create_index(
            [("tp_document_id", ASCENDING)], name="setpoints_tp_idx"
        )
        self._setpoints_collection.create_index(
            [("module", ASCENDING), ("test_instance", ASCENDING)],
            name="setpoints_module_test_idx",
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

    def write_cake_audit_entries(
        self, tp_name: str, git_hash: str, entries: Iterable[models.CakeAuditEntry]
    ) -> int:
        tp_document_id = f"{tp_name}:{git_hash}"
        docs = []
        for entry in entries:
            doc = cake_audit_entry_to_document(entry)
            doc["tp_document_id"] = tp_document_id
            docs.append(doc)
        self._cake_collection.delete_many({"tp_document_id": tp_document_id})
        if docs:
            self._cake_collection.insert_many(docs)
        return len(docs)

    def write_vmin_search_records(
        self, tp_name: str, git_hash: str, records: Iterable[models.VMinSearchRecord]
    ) -> int:
        tp_document_id = f"{tp_name}:{git_hash}"
        docs = []
        for record in records:
            doc = vmin_search_record_to_document(record)
            doc["tp_document_id"] = tp_document_id
            docs.append(doc)
        self._vmin_collection.delete_many({"tp_document_id": tp_document_id})
        if docs:
            self._vmin_collection.insert_many(docs)
        return len(docs)

    def write_scoreboard_entries(
        self, tp_name: str, git_hash: str, entries: Iterable[models.ScoreboardEntry]
    ) -> int:
        tp_document_id = f"{tp_name}:{git_hash}"
        docs = []
        for entry in entries:
            doc = scoreboard_entry_to_document(entry)
            doc["tp_document_id"] = tp_document_id
            docs.append(doc)
        self._scoreboard_collection.delete_many({"tp_document_id": tp_document_id})
        if docs:
            self._scoreboard_collection.insert_many(docs)
        return len(docs)

    def write_setpoint_entries(
        self, tp_name: str, git_hash: str, entries: Iterable[models.SetpointEntry]
    ) -> int:
        tp_document_id = f"{tp_name}:{git_hash}"
        docs = []
        for entry in entries:
            doc = setpoint_entry_to_document(entry)
            doc["tp_document_id"] = tp_document_id
            docs.append(doc)
        self._setpoints_collection.delete_many({"tp_document_id": tp_document_id})
        if docs:
            self._setpoints_collection.insert_many(docs)
        return len(docs)

    def upsert_product_config(self, config: models.ProductConfig) -> str:
        if not config.product_code:
            raise ValueError("Product config requires a ProductCode field before persistence")
        doc = product_config_to_document(config)
        doc["updated_at"] = datetime.now(timezone.utc)
        self._product_collection.update_one(
            {"product_code": config.product_code},
            {"$set": doc},
            upsert=True,
        )
        return config.product_code

    def upsert_report(self, tp_name: str, git_hash: str, report: models.IntegrationReport) -> str:
        """Backwards-compatible helper for existing callers."""
        artifact = models.IngestArtifact(tp_name=tp_name, git_hash=git_hash, report=report)
        return self.write_ingest_artifact(artifact)

    def close(self) -> None:
        self._client.close()
