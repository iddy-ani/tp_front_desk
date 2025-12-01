"""Configuration helpers for the ingestion pipeline."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class MongoSettings:
    uri: str
    database: str
    collection: str
    test_instances_collection: str
    plist_collection: str
    cake_collection: str
    vmin_collection: str
    scoreboard_collection: str
    product_collection: str
    setpoints_collection: str
    module_summary_collection: str
    port_results_collection: str
    flow_map_collection: str
    artifacts_collection: str
    hvqk_collection: str
    tls: bool = True

    @classmethod
    def from_env(cls) -> "MongoSettings":
        default_uri = (
            "mongodb://iq6cdegc265ebn7uiiem_admin:"
            "Yv0BqT17bakhZ9M%2CrL%3DbTPD0fqbVo%2Ch4@"
            "10-108-27-21.dbaas.intel.com:27017,"
            "10-108-27-23.dbaas.intel.com:27017,"
            "10-109-224-18.dbaas.intel.com:27017/"
            "tpfrontdesk?authSource=admin&tls=true"
        )
        uri = os.environ.get("TPFD_MONGO_URI", default_uri)
        database = os.environ.get("TPFD_MONGO_DB", "tpfrontdesk")
        collection = os.environ.get("TPFD_MONGO_COLLECTION", "ingest_artifacts")
        test_instances_collection = (
            os.environ.get("TPFD_MONGO_TEST_INSTANCES_COLLECTION")
            or os.environ.get("TPFD_MONGO_PAS_COLLECTION")
            or "test_instances"
        )
        plist_collection = os.environ.get("TPFD_MONGO_PLIST_COLLECTION", "plist_entries")
        cake_collection = os.environ.get("TPFD_MONGO_CAKE_COLLECTION", "cake_audit")
        vmin_collection = os.environ.get("TPFD_MONGO_VMIN_COLLECTION", "vmin_search")
        scoreboard_collection = os.environ.get("TPFD_MONGO_SCOREBOARD_COLLECTION", "scoreboard_entries")
        product_collection = os.environ.get("TPFD_MONGO_PRODUCT_COLLECTION", "product_configs")
        setpoints_collection = os.environ.get("TPFD_MONGO_SETPOINT_COLLECTION", "setpoints")
        module_summary_collection = os.environ.get("TPFD_MONGO_MODULE_SUMMARY_COLLECTION", "module_summary")
        port_results_collection = os.environ.get("TPFD_MONGO_PORT_RESULTS_COLLECTION", "port_results")
        flow_map_collection = os.environ.get("TPFD_MONGO_FLOW_MAP_COLLECTION", "flow_map")
        artifacts_collection = os.environ.get("TPFD_MONGO_ARTIFACTS_COLLECTION", "artifacts")
        hvqk_collection = os.environ.get("TPFD_MONGO_HVQK_COLLECTION", "hvqk_configs")
        tls = os.environ.get("TPFD_MONGO_TLS", "true").lower() in {"1", "true", "yes"}
        return cls(
            uri=uri,
            database=database,
            collection=collection,
            test_instances_collection=test_instances_collection,
            plist_collection=plist_collection,
            cake_collection=cake_collection,
            vmin_collection=vmin_collection,
            scoreboard_collection=scoreboard_collection,
            product_collection=product_collection,
            setpoints_collection=setpoints_collection,
            module_summary_collection=module_summary_collection,
            port_results_collection=port_results_collection,
            flow_map_collection=flow_map_collection,
            artifacts_collection=artifacts_collection,
            hvqk_collection=hvqk_collection,
            tls=tls,
        )


@dataclass
class IngestSettings:
    tp_root: Path
    mongo: MongoSettings
    repo_root: Path

    @classmethod
    def from_env(cls, repo_root: Optional[Path] = None) -> "IngestSettings":
        repo_root = repo_root or Path.cwd()
        tp_root_env = os.environ.get("TPFD_TP_ROOT")
        tp_root = Path(tp_root_env) if tp_root_env else repo_root / "Test Programs"
        return cls(tp_root=tp_root, mongo=MongoSettings.from_env(), repo_root=repo_root)
