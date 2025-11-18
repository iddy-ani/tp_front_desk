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
        tls = os.environ.get("TPFD_MONGO_TLS", "true").lower() in {"1", "true", "yes"}
        return cls(uri=uri, database=database, collection=collection, tls=tls)


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
