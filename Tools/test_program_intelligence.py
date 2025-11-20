"""
title: Test Program Intelligence
author: Idriss Animashaun
version: 0.1.0
license: MIT
description: Answer common PDE questions about HDMT test programs by reading the normalized Mongo collections.
requirements: pymongo>=4.6.0
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, TYPE_CHECKING

from pymongo import MongoClient

if TYPE_CHECKING:
    from pymongo.collection import Collection as MongoCollection
    MongoClientType = MongoClient
else:  # pragma: no cover - runtime alias to avoid Pydantic schema generation on PyMongo types
    MongoCollection = Any
    MongoClientType = Any
from pymongo.errors import PyMongoError
from pydantic import BaseModel, Field

MONGO_URI = (
    "mongodb://iq6cdegc265ebn7uiiem_admin:"
    "Yv0BqT17bakhZ9M%2CrL%3DbTPD0fqbVo%2Ch4@"
    "10-108-27-21.dbaas.intel.com:27017,"
    "10-108-27-23.dbaas.intel.com:27017,"
    "10-109-224-18.dbaas.intel.com:27017/"
    "tpfrontdesk?authSource=admin&tls=true"
)

MONGO_DATABASE = "tpfrontdesk"
INGEST_COLLECTION = "ingest_artifacts"
MODULE_SUMMARY_COLLECTION = "module_summary"
TEST_INSTANCES_COLLECTION = "test_instances"
PORT_RESULTS_COLLECTION = "port_results"
FLOW_MAP_COLLECTION = "flow_map"
SETPOINTS_COLLECTION = "setpoints"
ARTIFACTS_COLLECTION = "artifacts"
PRODUCT_COLLECTION = "product_configs"


# Reusable status emitter so Open WebUI can stream progress updates
def _safe_getenv(key: str, default: str = "") -> str:
    value = os.environ.get(key)
    return value if value is not None else default


class EventEmitter:
    def __init__(self, event_emitter: Optional[Callable[[dict], Any]] = None):
        self._event_emitter = event_emitter

    async def emit(self, description: str, status: str = "in_progress", done: bool = False):
        if self._event_emitter:
            await self._event_emitter(
                {
                    "type": "status",
                    "data": {
                        "status": status,
                        "description": description,
                        "done": done,
                    },
                }
            )

    async def progress(self, description: str):
        await self.emit(description, "in_progress", False)

    async def success(self, description: str):
        await self.emit(description, "success", True)

    async def error(self, description: str):
        await self.emit(description, "error", True)


@dataclass
class TPContext:
    tp_name: str
    git_hash: str
    tp_document_id: str
    product_code: Optional[str]
    product_name: Optional[str]
    ingested_at: Optional[datetime]
    metadata: Dict[str, Any]


class QuestionClassifier:
    MAPPINGS: List[Tuple[str, Tuple[str, ...]]] = [
        ("current_tp", ("current test program", "latest tp", "current tp")),
        ("list_tests", ("what tests", "list of tests", "tests does it have")),
        ("hvqk_flow", ("hvqk", "water fall", "waterfall")),
        ("tp_snapshot", ("what does it look like", "overview", "snapshot")),
        ("vcc_continuity", ("vcc continuity", "continuity")),
        ("setpoints", ("vMintc", "settings", "test class")),
        ("array_repair", ("array repair", "running array", "repair flows")),
        ("hot_repair", ("hot repair", "hot-repair", "hotrepair")),
        ("sdt_flow", ("sdt flow", "sdt content")),
    ]

    @classmethod
    def classify(cls, question: str) -> str:
        normalized = question.lower().strip()
        for label, keywords in cls.MAPPINGS:
            if any(keyword in normalized for keyword in keywords):
                return label
        return "fallback"


class Tools:
    class Valves(BaseModel):
        max_test_instance_rows: int = Field(
            default=250,
            ge=25,
            le=2000,
            description="Maximum test instances to sample when answering questions",
        )
        max_flow_rows: int = Field(
            default=150,
            ge=25,
            le=1000,
            description="Maximum flow map rows to inspect per question",
        )
        default_product_code: str = Field(
            default="",
            description="Fallback product code when the question omits it",
        )
        default_tp_name: str = Field(
            default="",
            description="Fallback TP name when a specific revision is required",
        )

    class UserValves(BaseModel):
        mongo_database: str = Field(default="", description="Override database name")
        product_code: str = Field(
            default="", description="Preferred product code if not provided in the prompt"
        )
        tp_name: str = Field(default="", description="Preferred TP name if not provided")

    def __init__(self):
        self.valves = self.Valves()
        self._mongo_client: Optional[MongoClientType] = None

    # Utility helpers -------------------------------------------------------------------
    @staticmethod
    def _normalize_token(value: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", (value or "").upper())

    def _match_catalog_token(self, question: str, catalog: Iterable[str]) -> Optional[str]:
        normalized_question = self._normalize_token(question)
        for entry in catalog:
            token = self._normalize_token(entry)
            if token and token in normalized_question:
                return entry
        return None

    def _list_module_names(self, module_collection: MongoCollection, ctx: TPContext) -> List[str]:
        modules: List[str] = []
        cursor = (
            module_collection.find(
                {"tp_document_id": ctx.tp_document_id},
                {"module_name": 1},
            ).sort("module_name", 1)
        )
        for row in cursor:
            name = row.get("module_name")
            if name:
                modules.append(name)
        return modules

    def _list_subflows(self, test_collection: MongoCollection, ctx: TPContext) -> List[str]:
        distinct = test_collection.distinct("subflow", {"tp_document_id": ctx.tp_document_id})
        flows = sorted(value for value in distinct if isinstance(value, str) and value)
        return flows

    @staticmethod
    def _module_filters(module_key: Optional[str]) -> Tuple[Dict[str, Any], Optional[str]]:
        if not module_key:
            return {}, None
        parts = module_key.split("_", 1)
        scrum = parts[0]
        module_name = parts[1] if len(parts) > 1 else None
        filters: Dict[str, Any] = {}
        if scrum:
            filters["scrum"] = scrum
        if module_name:
            filters["module_name"] = module_name
        return filters, f"{scrum}{'_' + module_name if module_name else ''}"

    def _fetch_test_rows(
        self,
        test_collection: MongoCollection,
        ctx: TPContext,
        extra_filters: Optional[Dict[str, Any]] = None,
        *,
        limit: int = 60,
        sort_field: str = "instance_name",
    ) -> List[dict]:
        filters: Dict[str, Any] = {"tp_document_id": ctx.tp_document_id}
        if extra_filters:
            filters.update(extra_filters)
        projection = {
            "instance_name": 1,
            "status": 1,
            "bypass": 1,
            "bins": 1,
            "counters": 1,
            "level": 1,
            "timing": 1,
            "plist": 1,
            "monitor_pat_count": 1,
            "kill_pat_count": 1,
            "scrum": 1,
            "module_name": 1,
            "test_type": 1,
            "test_type_detail": 1,
            "subflow": 1,
            "voltage_domain": 1,
            "corner": 1,
            "frequency": 1,
        }
        cursor = (
            test_collection.find(filters, projection)
            .sort(sort_field, 1)
            .limit(limit)
        )
        return list(cursor)

    def _format_detailed_tests(
        self,
        rows: List[dict],
        *,
        title: str,
        limit: int,
    ) -> str:
        if not rows:
            return f"No tests matched {title}."
        lines = [f"📐 {title} ({len(rows)} shown, limit {limit})"]
        for row in rows:
            lines.append(
                "- {instance} | Status {status} | SubFlow {subflow} | Module {module} | Type {test_type} | Level {level} | Timing {timing} | Plist {plist}".format(
                    instance=row.get("instance_name", "unknown"),
                    status=row.get("status", "n/a"),
                    subflow=row.get("subflow", "n/a"),
                    module=f"{row.get('scrum', 'n/a')}::{row.get('module_name', 'n/a')}",
                    test_type=row.get("test_type_detail") or row.get("test_type") or "n/a",
                    level=row.get("level") or "n/a",
                    timing=row.get("timing") or "n/a",
                    plist=row.get("plist") or "n/a",
                )
            )
        return "\n".join(lines)

    # Connection helpers -----------------------------------------------------------------
    def _get_mongo_client(self, user_valves: Optional["Tools.UserValves"] = None) -> Optional[MongoClientType]:
        uri = MONGO_URI
        if self._mongo_client is None:
            self._mongo_client = MongoClient(uri)
        return self._mongo_client

    def _get_collection(self, client: MongoClientType, name: str) -> MongoCollection:
        return client[MONGO_DATABASE][name]

    # Context resolution ------------------------------------------------------------------
    def _resolve_context(
        self,
        ingest_collection: MongoCollection,
        product_code: Optional[str],
        tp_name: Optional[str],
    ) -> TPContext:
        conditions: List[Dict[str, Any]] = []
        if tp_name:
            conditions.append({"tp_name": tp_name.strip()})
        if product_code:
            product_code = product_code.strip()
            conditions.append(
                {
                    "$or": [
                        {"product.product_code": product_code},
                        {"metadata.product_code": product_code},
                    ]
                }
            )
        query: Dict[str, Any] = {"$and": conditions} if conditions else {}
        cursor = ingest_collection.find(query).sort("ingested_at", -1)
        try:
            doc = cursor.next()
        except StopIteration:
            raise ValueError(
                "Unable to locate an ingest record matching TP "
                f"{tp_name or 'any'} and product {product_code or 'any'}."
            )
        metadata = doc.get("metadata", {}) or {}
        product_blob = doc.get("product", {}) or {}
        return TPContext(
            tp_name=doc.get("tp_name", "unknown"),
            git_hash=doc.get("git_hash", "unknown"),
            tp_document_id=doc["_id"],
            product_code=product_blob.get("product_code") or metadata.get("product_code") or product_code,
            product_name=product_blob.get("product_name") or metadata.get("product_name"),
            ingested_at=doc.get("ingested_at"),
            metadata=metadata,
        )

    # Query helpers ----------------------------------------------------------------------
    def _fetch_product_state(self, product_collection: MongoCollection, product_code: Optional[str]) -> Optional[dict]:
        if not product_code:
            return None
        return product_collection.find_one({"product_code": product_code})

    def _sample_test_instances(self, test_collection: MongoCollection, ctx: TPContext) -> List[dict]:
        cursor = (
            test_collection.find({"tp_document_id": ctx.tp_document_id}, {"module_name": 1, "instance_name": 1, "status": 1})
            .limit(self.valves.max_test_instance_rows)
        )
        return list(cursor)

    def _sample_flow_rows(self, flow_collection: MongoCollection, ctx: TPContext, keyword: Optional[str] = None) -> List[dict]:
        filters: Dict[str, Any] = {"tp_document_id": ctx.tp_document_id}
        if keyword:
            filters["$or"] = [
                {"module": {"$regex": keyword, "$options": "i"}},
                {"dutflow": {"$regex": keyword, "$options": "i"}},
                {"instance": {"$regex": keyword, "$options": "i"}},
            ]
        return list(
            flow_collection.find(filters, {"module": 1, "dutflow": 1, "instance": 1, "sequence_index": 1})
            .limit(self.valves.max_flow_rows)
        )

    def _fetch_module_summary(self, module_collection: MongoCollection, ctx: TPContext) -> List[dict]:
        return list(
            module_collection.find({"tp_document_id": ctx.tp_document_id}, {"module_name": 1, "total_tests": 1, "total_kill": 1, "percent_kill": 1})
        )

    def _fetch_setpoints(self, setpoints_collection: MongoCollection, ctx: TPContext, keyword: str) -> List[dict]:
        regex = {"$regex": keyword, "$options": "i"}
        filters = {
            "tp_document_id": ctx.tp_document_id,
            "$or": [
                {"method": regex},
                {"module": regex},
                {"test_instance": regex},
            ],
        }
        return list(
            setpoints_collection.find(filters, {"module": 1, "test_instance": 1, "method": 1, "values": 1}).limit(50)
        )

    def _fetch_artifact_summary(self, artifacts_collection: MongoCollection, ctx: TPContext) -> Dict[str, int]:
        pipeline = [
            {"$match": {"tp_document_id": ctx.tp_document_id}},
            {"$group": {"_id": "$category", "count": {"$sum": 1}}},
        ]
        summary: Dict[str, int] = {}
        for row in artifacts_collection.aggregate(pipeline):
            summary[row["_id"] or "unknown"] = row["count"]
        return summary

    def _summarize_hvqk_modules(self, test_collection: MongoCollection, ctx: TPContext) -> List[dict]:
        pipeline = [
            {
                "$match": {
                    "tp_document_id": ctx.tp_document_id,
                    "subflow": {"$regex": "HVQK", "$options": "i"},
                }
            },
            {
                "$group": {
                    "_id": {
                        "subflow": "$subflow",
                        "scrum": "$scrum",
                        "module": "$module_name",
                    },
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"_id.subflow": 1, "count": -1}},
        ]
        return list(test_collection.aggregate(pipeline))

    # Answer generators -----------------------------------------------------------------
    def _format_current_tp_answer(self, ctx: TPContext, product_state: Optional[dict]) -> str:
        ingested = ctx.ingested_at.isoformat() if ctx.ingested_at else "unknown"
        product_line = f"Product: {ctx.product_name or 'unknown'} ({ctx.product_code or 'n/a'})"
        product_details = []
        if product_state:
            if product_state.get("latest_tp"):
                product_details.append(f"Configured latest TP: {product_state['latest_tp']}")
            if product_state.get("network_path"):
                product_details.append(f"Network path: {product_state['network_path']}")
            if product_state.get("number_of_releases"):
                product_details.append(f"Tracked releases: {product_state['number_of_releases']}")
        flow_tables = ctx.metadata.get("flow_table_names", [])
        summary = [
            f"✅ Current ingest for {ctx.tp_name}",
            product_line,
            f"Git hash: {ctx.git_hash}",
            f"Ingested: {ingested}",
        ]
        if product_details:
            summary.append("Product config: " + "; ".join(product_details))
        if flow_tables:
            summary.append("Flow tables: " + ", ".join(flow_tables[:8]))
        return "\n".join(summary)

    def _format_test_list_answer(
        self,
        ctx: TPContext,
        tests: List[dict],
        flows: Optional[List[str]] = None,
        modules: Optional[List[str]] = None,
    ) -> str:
        if not tests:
            return "I could not find PAS instances for this TP."
        module_buckets: Dict[str, List[str]] = defaultdict(list)
        status_counter = Counter()
        for row in tests:
            module = (row.get("module_name") or "unknown").upper()
            instance = row.get("instance_name") or "unnamed"
            module_buckets[module].append(instance)
            status_counter[row.get("status", "UNKNOWN")] += 1
        lines = [f"📋 Sample tests for {ctx.tp_name} ({len(tests)} rows scanned)"]
        for module, instances in list(module_buckets.items())[:8]:
            preview = ", ".join(instances[:5])
            lines.append(f"- {module}: {preview}{'…' if len(instances) > 5 else ''}")
        status_summary = ", ".join(f"{status}:{count}" for status, count in status_counter.most_common(5))
        lines.append(f"Status mix: {status_summary}")
        if flows:
            lines.append("")
            lines.append(f"Flows ({len(flows)} entries):")
            for idx, flow in enumerate(flows[:30], start=1):
                lines.append(f"  {idx}: {flow}")
        if modules:
            lines.append("")
            lines.append(f"Modules ({len(modules)} entries):")
            for idx, module in enumerate(modules[:40], start=1):
                lines.append(f"  {idx}: {module}")
        return "\n".join(lines)

    def _format_flow_answer(self, ctx: TPContext, flows: List[dict], keyword: str, description: str) -> str:
        if not flows:
            return f"No {description} references matched '{keyword}'."
        lines = [f"🔍 {description.title()} hits for '{keyword}' ({len(flows)} results)"]
        for row in flows[:8]:
            lines.append(
                f"- Module {row.get('module')} | Flow {row.get('dutflow')} | Instance {row.get('instance')} (index {row.get('sequence_index')})"
            )
        return "\n".join(lines)

    def _format_setpoint_answer(self, ctx: TPContext, matches: List[dict], keyword: str) -> str:
        if not matches:
            return f"No setpoints mention '{keyword}'."
        lines = [f"⚙️ Setpoint coverage for '{keyword}'"]
        for row in matches[:8]:
            values = ", ".join(row.get("values", [])[:5])
            lines.append(
                f"- Module {row.get('module')} | Instance {row.get('test_instance')} | Method {row.get('method')} | Values {values or 'n/a'}"
            )
        return "\n".join(lines)

    def _format_module_focus(self, ctx: TPContext, modules: List[dict], keyword: str, description: str) -> str:
        matches = [row for row in modules if keyword in (row.get("module_name") or "").lower()]
        if not matches:
            return f"No modules hint at {description}."
        lines = [f"🛠️ {description.title()} modules"]
        for row in matches[:6]:
            lines.append(
                f"- {row.get('module_name')} | Tests {row.get('total_tests')} | Kill {row.get('total_kill')} | Kill% {row.get('percent_kill')}"
            )
        return "\n".join(lines)

    def _format_snapshot(self, ctx: TPContext, modules: List[dict], artifacts: Dict[str, int]) -> str:
        top_modules = sorted(modules, key=lambda row: row.get("total_tests", 0) or 0, reverse=True)[:5]
        module_lines = [
            f"- {row.get('module_name')} (tests={row.get('total_tests')}, kill={row.get('total_kill')}, kill%={row.get('percent_kill')})"
            for row in top_modules
        ]
        artifact_lines = [f"{category}:{count}" for category, count in artifacts.items()]
        return "\n".join(
            [
                f"📊 Snapshot for {ctx.tp_name}",
                "Top modules:",
                *module_lines,
                "Artifacts:",
                ", ".join(artifact_lines) or "No artifacts tracked",
            ]
        )

    def _format_hvqk_summary(self, rows: List[dict]) -> str:
        if not rows:
            return "I could not find HVQK-linked flows in this TP."
        lines = ["🌊 HVQK waterfall coverage:"]
        for row in rows:
            ident = row.get("_id", {})
            subflow = ident.get("subflow", "unknown")
            module = ident.get("module", "unknown")
            scrum = ident.get("scrum", "")
            lines.append(f"- {subflow}: {scrum}_{module} ({row.get('count', 0)} tests)")
        return "\n".join(lines)

    # Main entrypoint --------------------------------------------------------------------
    async def answer_tp_question(
        self,
        question: str,
        product_code: str = "",
        tp_name: str = "",
        __event_emitter__: Optional[Callable[[dict], Any]] = None,
        __user__: Optional[dict] = None,
    ) -> str:
        emitter = EventEmitter(__event_emitter__)
        if not question.strip():
            await emitter.error("Please provide a question so I know what to retrieve.")
            return "Provide a question about the test program (e.g., 'What is the current test program?')."

        user_valves = self.UserValves()
        client = self._get_mongo_client(user_valves)
        if client is None:
            await emitter.error("Mongo connection unavailable. Hardcoded URI returned no client.")
            return "Mongo connection is not configured."

        product_code = product_code or user_valves.product_code or self.valves.default_product_code
        tp_name = tp_name or user_valves.tp_name or self.valves.default_tp_name

        ingest_collection = self._get_collection(client, INGEST_COLLECTION)
        classification = QuestionClassifier.classify(question)

        try:
            ctx = self._resolve_context(ingest_collection, product_code, tp_name)
        except (StopIteration, ValueError) as exc:
            await emitter.error(str(exc))
            return str(exc)

        await emitter.progress(f"Resolved context: {ctx.tp_name} ({ctx.product_code or 'unknown product'})")

        module_collection = self._get_collection(client, MODULE_SUMMARY_COLLECTION)
        test_collection = self._get_collection(client, TEST_INSTANCES_COLLECTION)
        flow_collection = self._get_collection(client, FLOW_MAP_COLLECTION)
        port_collection = self._get_collection(client, PORT_RESULTS_COLLECTION)
        setpoints_collection = self._get_collection(client, SETPOINTS_COLLECTION)
        artifacts_collection = self._get_collection(client, ARTIFACTS_COLLECTION)
        product_collection = self._get_collection(client, PRODUCT_COLLECTION)

        modules_catalog = self._list_module_names(module_collection, ctx)
        flows_catalog = self._list_subflows(test_collection, ctx)
        module_match = self._match_catalog_token(question, modules_catalog)
        flow_match = self._match_catalog_token(question, flows_catalog)

        normalized_question = question.lower()
        if classification == "hvqk_flow" and module_match:
            classification = "hvqk_module_detail"
        if classification == "fallback" and (module_match or flow_match) and "test" in normalized_question:
            classification = "module_flow_tests"

        answer_lines: List[str] = []

        try:
            if classification == "current_tp":
                product_state = self._fetch_product_state(product_collection, ctx.product_code)
                answer_lines.append(self._format_current_tp_answer(ctx, product_state))

            elif classification == "list_tests":
                tests = self._sample_test_instances(test_collection, ctx)
                answer_lines.append(
                    self._format_test_list_answer(ctx, tests, flows_catalog, modules_catalog)
                )

            elif classification == "hvqk_flow":
                flows = self._sample_flow_rows(flow_collection, ctx, keyword="hvqk")
                if not flows:
                    flows = self._sample_flow_rows(flow_collection, ctx, keyword="water")
                answer_lines.append(
                    self._format_flow_answer(ctx, flows, "HVQK", "HVQK/Waterfall flow content")
                )
                hvqk_summary = self._summarize_hvqk_modules(test_collection, ctx)
                answer_lines.append(self._format_hvqk_summary(hvqk_summary))

            elif classification == "hvqk_module_detail":
                filters: Dict[str, Any] = {"subflow": {"$regex": "HVQK", "$options": "i"}}
                module_filters, module_label = self._module_filters(module_match)
                filters.update(module_filters)
                rows = self._fetch_test_rows(
                    test_collection,
                    ctx,
                    filters,
                    limit=self.valves.max_test_instance_rows,
                )
                title = module_label or "HVQK modules"
                answer_lines.append(
                    self._format_detailed_tests(
                        rows,
                        title=f"HVQK coverage for {title}",
                        limit=self.valves.max_test_instance_rows,
                    )
                )

            elif classification == "tp_snapshot":
                modules = self._fetch_module_summary(module_collection, ctx)
                artifact_summary = self._fetch_artifact_summary(artifacts_collection, ctx)
                answer_lines.append(self._format_snapshot(ctx, modules, artifact_summary))
                sample_rows = self._fetch_test_rows(test_collection, ctx, limit=15)
                answer_lines.append(
                    self._format_detailed_tests(
                        sample_rows,
                        title="Representative PAS excerpt",
                        limit=15,
                    )
                )

            elif classification == "vcc_continuity":
                flows = self._sample_flow_rows(flow_collection, ctx, keyword="vcc")
                tests = self._sample_test_instances(test_collection, ctx)
                matches = [
                    row
                    for row in tests
                    if "vcc" in (row.get("instance_name") or "").lower()
                    and "cont" in (row.get("instance_name") or "").lower()
                ]
                lines = [self._format_flow_answer(ctx, flows, "VCC", "VCC continuity flows")]
                if matches:
                    lines.append(
                        "PAS instances: "
                        + ", ".join(f"{row.get('module_name')}::{row.get('instance_name')}" for row in matches[:6])
                    )
                answer_lines.extend(lines)

            elif classification == "setpoints":
                matches = self._fetch_setpoints(setpoints_collection, ctx, "VMIN")
                answer_lines.append(self._format_setpoint_answer(ctx, matches, "Vmin"))

            elif classification == "array_repair":
                modules = self._fetch_module_summary(module_collection, ctx)
                answer_lines.append(self._format_module_focus(ctx, modules, "repair", "array repair"))

            elif classification == "hot_repair":
                tests = self._sample_test_instances(test_collection, ctx)
                hot = [
                    row
                    for row in tests
                    if "hot" in (row.get("instance_name") or "").lower() or "hot" in (row.get("module_name") or "").lower()
                ]
                if hot:
                    answer_lines.append(
                        "🔥 Hot repair instances: "
                        + ", ".join(f"{row.get('module_name')}::{row.get('instance_name')}" for row in hot[:6])
                    )
                else:
                    answer_lines.append("No hot repair instances detected in the sampled PAS data.")

            elif classification == "sdt_flow":
                flows = self._sample_flow_rows(flow_collection, ctx, keyword="sdt")
                if not flows:
                    flows = self._sample_flow_rows(flow_collection, ctx)
                answer_lines.append(
                    self._format_flow_answer(ctx, flows, "SDT", "SDT flow content")
                )

            elif classification == "module_flow_tests":
                module_filters, module_label = self._module_filters(module_match)
                filters: Dict[str, Any] = {}
                filters.update(module_filters)
                if flow_match:
                    filters["subflow"] = flow_match
                rows = self._fetch_test_rows(
                    test_collection,
                    ctx,
                    extra_filters=filters,
                    limit=self.valves.max_test_instance_rows,
                )
                descriptor = module_label or "all modules"
                if flow_match:
                    descriptor += f" in {flow_match}"
                answer_lines.append(
                    self._format_detailed_tests(
                        rows,
                        title=f"Tests for {descriptor}",
                        limit=self.valves.max_test_instance_rows,
                    )
                )

            elif classification == "atspeed_detail":
                filters: Dict[str, Any] = {
                    "instance_name": {"$regex": "ATSPEED", "$options": "i"}
                }
                module_filters, module_label = self._module_filters(module_match)
                filters.update(module_filters)
                if flow_match:
                    filters["subflow"] = flow_match
                rows = self._fetch_test_rows(
                    test_collection,
                    ctx,
                    extra_filters=filters,
                    limit=self.valves.max_test_instance_rows,
                )
                descriptor = module_label or "program"
                if flow_match:
                    descriptor += f"/{flow_match}"
                answer_lines.append(
                    self._format_detailed_tests(
                        rows,
                        title=f"ATSPEED tests for {descriptor}",
                        limit=self.valves.max_test_instance_rows,
                    )
                )

            else:
                modules = self._fetch_module_summary(module_collection, ctx)
                tests = self._sample_test_instances(test_collection, ctx)
                summary = self._format_snapshot(ctx, modules, self._fetch_artifact_summary(artifacts_collection, ctx))
                answer_lines.append(summary)
                answer_lines.append(
                    self._format_test_list_answer(ctx, tests, flows_catalog, modules_catalog)
                )

            result = "\n\n".join(answer_lines) if answer_lines else "I could not build a response."
            await emitter.success("Answer ready")
            return result
        except PyMongoError as exc:
            await emitter.error(f"Mongo query failed: {exc}")
            return f"Mongo query failed: {exc}"
        except Exception as exc:  # pragma: no cover - defensive coding
            await emitter.error(str(exc))
            return str(exc)


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    tool = Tools()

    async def _demo():
        answer = await tool.answer_tp_question(
            "What is the current test program for PantherLake?",
            product_code=os.environ.get("TPFD_DEMO_PRODUCT", "8PXM"),
        )
        print(answer)

    asyncio.run(_demo())
