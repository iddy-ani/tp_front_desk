"""MongoDB persistence helpers for TP ingestion artifacts."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pymongo import ASCENDING, MongoClient

try:
    import mongomock
except ImportError:  # pragma: no cover - optional dependency
    mongomock = None

from . import models
from .serialization import (
    artifact_reference_to_document,
    cake_audit_entry_to_document,
    flow_map_entry_to_document,
    hvqk_config_entry_to_document,
    ingest_artifact_to_document,
    module_summary_entry_to_document,
    pas_record_to_document,
    plist_entry_to_document,
    port_result_row_to_document,
    product_config_to_document,
    scoreboard_entry_to_document,
    setpoint_entry_to_document,
    vmin_search_record_to_document,
)


_ID_SANITIZER = re.compile(r"[^A-Za-z0-9._-]+")


def _normalize_identifier(value: Optional[str], fallback: str = "unknown") -> str:
    if not value:
        return fallback
    cleaned = _ID_SANITIZER.sub("_", value.strip())
    return cleaned or fallback


def _module_key(name: Optional[str]) -> str:
    return (_normalize_identifier(name, "unknown")).lower()


def _instance_key(module_name: Optional[str], instance_name: Optional[str]) -> str:
    module_component = _module_key(module_name)
    instance_component = _normalize_identifier(instance_name, "unnamed").lower()
    return f"{module_component}|{instance_component}"


def _safe_sum(*values: Optional[int]) -> Optional[int]:
    total = 0
    found = False
    for value in values:
        if value is None:
            continue
        total += value
        found = True
    return total if found else None


def _ratio_float(numerator: Optional[int], denominator: Optional[int]) -> Optional[float]:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


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
        test_instances_collection: str = "test_instances",
        plist_collection: str = "plist_entries",
        cake_collection: str = "cake_audit",
        vmin_collection: str = "vmin_search",
        scoreboard_collection: str = "scoreboard_entries",
        product_collection: str = "product_configs",
        setpoints_collection: str = "setpoints",
        module_summary_collection: str = "module_summary",
        port_results_collection: str = "port_results",
        flow_map_collection: str = "flow_map",
        artifacts_collection: str = "artifacts",
        hvqk_collection: str = "hvqk_configs",
    ) -> None:
        self._client = _build_client(uri)
        self._collection = self._client[db_name][collection]
        self._test_instances_collection = self._client[db_name][test_instances_collection]
        self._plist_collection = self._client[db_name][plist_collection]
        self._cake_collection = self._client[db_name][cake_collection]
        self._vmin_collection = self._client[db_name][vmin_collection]
        self._scoreboard_collection = self._client[db_name][scoreboard_collection]
        self._product_collection = self._client[db_name][product_collection]
        self._setpoints_collection = self._client[db_name][setpoints_collection]
        self._module_summary_collection = self._client[db_name][module_summary_collection]
        self._port_results_collection = self._client[db_name][port_results_collection]
        self._flow_map_collection = self._client[db_name][flow_map_collection]
        self._artifacts_collection = self._client[db_name][artifacts_collection]
        self._hvqk_collection = self._client[db_name][hvqk_collection]
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
        self._test_instances_collection.create_index(
            [("tp_document_id", ASCENDING), ("instance_name", ASCENDING)],
            name="test_instances_tp_instance_idx",
            unique=True,
        )
        self._test_instances_collection.create_index(
            [("tp_document_id", ASCENDING), ("module_name", ASCENDING), ("status", ASCENDING)],
            name="test_instances_module_status_idx",
        )
        self._module_summary_collection.create_index(
            [("tp_document_id", ASCENDING), ("module_name", ASCENDING)],
            name="module_summary_tp_module_idx",
            unique=True,
        )
        self._port_results_collection.create_index(
            [("tp_document_id", ASCENDING), ("instance_name_port", ASCENDING)],
            name="port_results_tp_instance_idx",
            unique=True,
        )
        self._port_results_collection.create_index(
            [("tp_document_id", ASCENDING), ("module_name", ASCENDING), ("port", ASCENDING)],
            name="port_results_module_port_idx",
        )
        self._flow_map_collection.create_index(
            [("tp_document_id", ASCENDING), ("module", ASCENDING), ("dutflow", ASCENDING)],
            name="flow_map_tp_module_dut_idx",
        )
        self._artifacts_collection.create_index(
            [("tp_document_id", ASCENDING), ("category", ASCENDING)],
            name="artifacts_tp_category_idx",
        )
        self._hvqk_collection.create_index(
            [("tp_document_id", ASCENDING), ("module_name", ASCENDING)],
            name="hvqk_tp_module_idx",
        )
        self._hvqk_collection.create_index(
            [("tp_document_id", ASCENDING), ("file_name", ASCENDING)],
            name="hvqk_tp_file_idx",
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

    def write_module_summary_entries(
        self,
        tp_name: str,
        git_hash: str,
        entries: Iterable[models.ModuleSummaryEntry],
        *,
        product_code: Optional[str] = None,
    ) -> Dict[str, str]:
        tp_document_id = f"{tp_name}:{git_hash}"
        docs: List[Dict[str, Any]] = []
        module_lookup: Dict[str, str] = {}
        timestamp = datetime.now(timezone.utc)
        for entry in entries:
            doc = module_summary_entry_to_document(entry)
            if product_code:
                doc["product_code"] = product_code
            doc["tp_document_id"] = tp_document_id
            doc["tp_name"] = tp_name
            doc["git_hash"] = git_hash
            doc["ingested_at"] = timestamp
            module_id = f"{tp_document_id}|module:{_normalize_identifier(entry.module_name)}"
            doc["_id"] = module_id
            docs.append(doc)
            module_lookup[_module_key(entry.module_name)] = module_id

        self._module_summary_collection.delete_many({"tp_document_id": tp_document_id})
        if docs:
            self._module_summary_collection.insert_many(docs)
        return module_lookup

    def write_port_results(
        self,
        tp_name: str,
        git_hash: str,
        rows: Iterable[models.PortResultRow],
        *,
        product_code: Optional[str] = None,
        module_lookup: Optional[Dict[str, str]] = None,
        instance_lookup: Optional[Dict[str, str]] = None,
    ) -> int:
        tp_document_id = f"{tp_name}:{git_hash}"
        docs: List[Dict[str, Any]] = []
        timestamp = datetime.now(timezone.utc)
        for row in rows:
            doc = port_result_row_to_document(row)
            doc["tp_document_id"] = tp_document_id
            doc["tp_name"] = tp_name
            doc["git_hash"] = git_hash
            if product_code:
                doc["product_code"] = product_code
            if module_lookup:
                module_name = row.module_summary_name or row.module_name
                doc["module_summary_id"] = module_lookup.get(_module_key(module_name))
            if instance_lookup:
                doc["test_instance_id"] = instance_lookup.get(
                    _instance_key(row.module_name, row.instance_name)
                )
            doc["ingested_at"] = timestamp
            doc["_id"] = f"{tp_document_id}|port:{_normalize_identifier(row.instance_name_port)}"
            docs.append(doc)
        self._port_results_collection.delete_many({"tp_document_id": tp_document_id})
        if docs:
            self._port_results_collection.insert_many(docs)
        return len(docs)

    def write_flow_map_entries(
        self,
        tp_name: str,
        git_hash: str,
        entries: Iterable[models.FlowMapEntry],
        *,
        product_code: Optional[str] = None,
        instance_lookup: Optional[Dict[str, str]] = None,
    ) -> int:
        tp_document_id = f"{tp_name}:{git_hash}"
        docs: List[Dict[str, Any]] = []
        timestamp = datetime.now(timezone.utc)
        for entry in entries:
            doc = flow_map_entry_to_document(entry)
            doc["tp_document_id"] = tp_document_id
            doc["tp_name"] = tp_name
            doc["git_hash"] = git_hash
            if product_code:
                doc["product_code"] = product_code
            if instance_lookup:
                doc["linked_instance_id"] = instance_lookup.get(
                    _instance_key(entry.module, entry.instance)
                )
            doc["ingested_at"] = timestamp
            doc["_id"] = (
                f"{tp_document_id}|flow:{_normalize_identifier(entry.module, 'module')}:"
                f"{_normalize_identifier(entry.dutflow, 'dut')}:"
                f"{entry.sequence_index}"
            )
            docs.append(doc)
        self._flow_map_collection.delete_many({"tp_document_id": tp_document_id})
        if docs:
            self._flow_map_collection.insert_many(docs)
        return len(docs)

    def write_artifact_documents(
        self,
        tp_name: str,
        git_hash: str,
        artifacts: Iterable[models.ArtifactReference],
        *,
        product_code: Optional[str] = None,
    ) -> int:
        tp_document_id = f"{tp_name}:{git_hash}"
        docs: List[Dict[str, Any]] = []
        timestamp = datetime.now(timezone.utc)
        for artifact in artifacts:
            doc = artifact_reference_to_document(artifact)
            doc["tp_document_id"] = tp_document_id
            doc["tp_name"] = tp_name
            doc["git_hash"] = git_hash
            if product_code:
                doc["product_code"] = product_code
            doc["ingested_at"] = timestamp
            doc["_id"] = f"{tp_document_id}|artifact:{_normalize_identifier(artifact.relative_path)}"
            docs.append(doc)
        self._artifacts_collection.delete_many({"tp_document_id": tp_document_id})
        if docs:
            self._artifacts_collection.insert_many(docs)
        return len(docs)

    def write_hvqk_configs(
        self,
        tp_name: str,
        git_hash: str,
        entries: Iterable[models.HVQKConfigEntry],
        *,
        product_code: Optional[str] = None,
    ) -> int:
        tp_document_id = f"{tp_name}:{git_hash}"
        docs: List[Dict[str, Any]] = []
        timestamp = datetime.now(timezone.utc)
        for entry in entries:
            doc = hvqk_config_entry_to_document(entry)
            doc["tp_document_id"] = tp_document_id
            doc["tp_name"] = tp_name
            doc["git_hash"] = git_hash
            if product_code:
                doc["product_code"] = product_code
            doc["ingested_at"] = timestamp
            doc["_id"] = f"{tp_document_id}|hvqk:{_normalize_identifier(entry.relative_path)}"
            docs.append(doc)
        self._hvqk_collection.delete_many({"tp_document_id": tp_document_id})
        if docs:
            self._hvqk_collection.insert_many(docs)
        return len(docs)

    def write_test_instances(
        self,
        tp_name: str,
        git_hash: str,
        records: Iterable[models.PASRecord],
        *,
        product_code: Optional[str] = None,
        module_lookup: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        tp_document_id = f"{tp_name}:{git_hash}"
        record_list = list(records)
        docs: List[Dict[str, Any]] = []
        existing_ids: set[str] = set()
        instance_lookup: Dict[str, str] = {}
        timestamp = datetime.now(timezone.utc)
        for record in record_list:
            doc = pas_record_to_document(record)
            doc["tp_document_id"] = tp_document_id
            doc["tp_name"] = tp_name
            doc["git_hash"] = git_hash
            if product_code:
                doc["product_code"] = product_code
            if module_lookup:
                doc["module_summary_id"] = module_lookup.get(_module_key(record.module_name))
            kill_monitor_total = _safe_sum(record.kill_pat_count, record.monitor_pat_count)
            doc["kill_ratio"] = _ratio_float(record.kill_pat_count, kill_monitor_total)
            doc["monitor_ratio"] = _ratio_float(record.monitor_pat_count, kill_monitor_total)
            doc["ingested_at"] = timestamp
            base_id = f"{tp_document_id}|instance:{_normalize_identifier(record.instance_name, 'instance')}"
            unique_id = base_id
            suffix = 1
            while unique_id in existing_ids:
                suffix += 1
                unique_id = f"{base_id}:{suffix}"
            doc["_id"] = unique_id
            existing_ids.add(unique_id)
            docs.append(doc)
            key = _instance_key(record.module_name, record.instance_name)
            instance_lookup.setdefault(key, unique_id)

        self._test_instances_collection.delete_many({"tp_document_id": tp_document_id})
        if docs:
            self._test_instances_collection.insert_many(docs)
        return instance_lookup

    def write_pas_records(
        self, tp_name: str, git_hash: str, records: Iterable[models.PASRecord]
    ) -> int:
        """Backward-compatible wrapper for legacy callers."""
        record_list = list(records)
        self.write_test_instances(tp_name, git_hash, record_list)
        return len(record_list)

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
        self,
        tp_name: str,
        git_hash: str,
        entries: Iterable[models.ScoreboardEntry],
        *,
        product_code: Optional[str] = None,
        instance_lookup: Optional[Dict[str, str]] = None,
    ) -> int:
        tp_document_id = f"{tp_name}:{git_hash}"
        docs: List[Dict[str, Any]] = []
        timestamp = datetime.now(timezone.utc)
        for entry in entries:
            doc = scoreboard_entry_to_document(entry)
            doc["tp_document_id"] = tp_document_id
            doc["tp_name"] = tp_name
            doc["git_hash"] = git_hash
            if product_code:
                doc["product_code"] = product_code
            if instance_lookup:
                doc["test_instance_id"] = instance_lookup.get(
                    _instance_key(entry.module, entry.test_instance)
                )
            doc["ingested_at"] = timestamp
            doc["_id"] = (
                f"{tp_document_id}|score:{_normalize_identifier(entry.module, 'module')}:"
                f"{_normalize_identifier(entry.test_instance, 'test')}:"
                f"{entry.base_number or 0}:{entry.duplicate or 0}"
            )
            docs.append(doc)
        self._scoreboard_collection.delete_many({"tp_document_id": tp_document_id})
        if docs:
            self._scoreboard_collection.insert_many(docs)
        return len(docs)

    def write_setpoint_entries(
        self,
        tp_name: str,
        git_hash: str,
        entries: Iterable[models.SetpointEntry],
        *,
        product_code: Optional[str] = None,
    ) -> int:
        tp_document_id = f"{tp_name}:{git_hash}"
        docs: List[Dict[str, Any]] = []
        timestamp = datetime.now(timezone.utc)
        for entry in entries:
            doc = setpoint_entry_to_document(entry)
            doc["tp_document_id"] = tp_document_id
            doc["tp_name"] = tp_name
            doc["git_hash"] = git_hash
            if product_code:
                doc["product_code"] = product_code
            doc["ingested_at"] = timestamp
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
