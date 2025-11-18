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


def plist_entry_to_document(entry: models.PlistEntry) -> Dict[str, Any]:
    return {
        "pattern_name": entry.pattern_name,
        "total_patterns": entry.total_patterns,
        "commented_patterns": entry.commented_patterns,
        "flatten": entry.flatten,
        "burst_off": entry.burst_off,
        "pre_burst": entry.pre_burst,
        "post_burst": entry.post_burst,
        "pre_burst_plist": entry.pre_burst_plist,
        "post_burst_plist": entry.post_burst_plist,
        "pre_pattern": entry.pre_pattern,
        "post_pattern": entry.post_pattern,
        "mask": entry.mask,
        "keep_alive": entry.keep_alive,
    }


def cake_audit_entry_to_document(entry: models.CakeAuditEntry) -> Dict[str, Any]:
    return {
        "domain_name": entry.domain_name,
        "shift_name": entry.shift_name,
        "gsds": entry.gsds,
    }


def vmin_search_record_to_document(record: models.VMinSearchRecord) -> Dict[str, Any]:
    return {
        "module": record.module,
        "test_name": record.test_name,
        "template": record.template,
        "execution_mode": record.execution_mode,
        "vmin_predict": record.vmin_predict,
        "low_search": record.low_search,
        "hi_search": record.hi_search,
        "gsds": record.gsds,
        "search_pin": record.search_pin,
        "overshoot_recv": record.overshoot_recv,
        "vmin_pred_high": record.vmin_pred_high,
        "search_result": record.search_result,
    }


def scoreboard_entry_to_document(entry: models.ScoreboardEntry) -> Dict[str, Any]:
    return {
        "module": entry.module,
        "test_instance": entry.test_instance,
        "base_number": entry.base_number,
        "duplicate": entry.duplicate,
        "in_range": entry.in_range,
        "extra_info": entry.extra_info,
    }


def setpoint_entry_to_document(entry: models.SetpointEntry) -> Dict[str, Any]:
    return {
        "module": entry.module,
        "test_instance": entry.test_instance,
        "method": entry.method,
        "source_file": str(entry.source_file),
        "values": entry.values,
    }


def product_config_to_document(config: models.ProductConfig) -> Dict[str, Any]:
    return {
        "product_code": config.product_code,
        "product_name": config.product_name,
        "network_path": config.network_path,
        "latest_tp": config.latest_tp,
        "number_of_releases": config.number_of_releases,
        "releases": config.releases,
        "last_run_date": config.last_run_date,
        "additional_attributes": config.additional_attributes,
    }
