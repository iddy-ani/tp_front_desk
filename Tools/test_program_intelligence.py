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
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Tuple,
    TYPE_CHECKING,
)

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
HVQK_COLLECTION = "hvqk_configs"


# Reusable status emitter so Open WebUI can stream progress updates
def _safe_getenv(key: str, default: str = "") -> str:
    value = os.environ.get(key)
    return value if value is not None else default


class EventEmitter:
    def __init__(self, event_emitter: Optional[Callable[[dict], Any]] = None):
        self._event_emitter = event_emitter

    async def emit(
        self, description: str, status: str = "in_progress", done: bool = False
    ):
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
        ("atspeed_detail", ("atspeed",)),
        ("tp_snapshot", ("what does it look like", "overview", "snapshot")),
        ("vcc_continuity", ("vcc continuity", "continuity")),
        ("test_class_filter", ("vmintc", "test class", "settings of the test class")),
        ("setpoints", ("settings", "setpoint")),
        # hot_repair must come BEFORE array_repair so "hot array repair" matches correctly
        ("hot_repair", ("hot array repair", "hot repair", "hot-repair", "hotrepair")),
        ("array_repair", ("array repair", "running array", "repair flows")),
        ("sdt_flow", ("sdt flow", "sdt content")),
    ]

    @classmethod
    def classify(cls, question: str) -> str:
        normalized = question.lower().strip()
        for label, keywords in cls.MAPPINGS:
            for keyword in keywords:
                cleaned = keyword.strip().lower()
                if not cleaned:
                    continue
                parts = [part for part in cleaned.split() if part]
                if len(parts) > 1:
                    pattern_body = r"\s+".join(re.escape(part) for part in parts)
                else:
                    pattern_body = re.escape(cleaned)
                pattern = rf"(?<![a-z0-9]){pattern_body}(?![a-z0-9])"
                if re.search(pattern, normalized):
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
            default="",
            description="Preferred product code if not provided in the prompt",
        )
        tp_name: str = Field(
            default="", description="Preferred TP name if not provided"
        )

    class ContextResolutionError(Exception):
        def __init__(self, message: str, suggestions: Optional[List[str]] = None):
            super().__init__(message)
            self.suggestions = suggestions or []

    def __init__(self):
        self.valves = self.Valves()
        self._mongo_client: Optional[MongoClientType] = None

    # Utility helpers -------------------------------------------------------------------
    @staticmethod
    def _normalize_token(value: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", (value or "").upper())

    @staticmethod
    def _tokenize(value: str) -> List[str]:
        return [
            token for token in re.split(r"[^A-Z0-9]+", (value or "").upper()) if token
        ]

    @staticmethod
    def _looks_like_product_code(value: str) -> bool:
        if not value:
            return False
        stripped = value.strip().upper()
        if not re.fullmatch(r"[0-9A-Z]+", stripped):
            return False
        if len(stripped) < 4 or len(stripped) > 8:
            return False
        if not stripped.startswith("8"):
            return False
        return True

    @staticmethod
    def _looks_like_tp_name(value: str) -> bool:
        if not value:
            return False
        stripped = value.strip().upper()
        if len(stripped) < 12 or len(stripped) > 24:
            return False
        if not stripped.isalnum():
            return False
        if not stripped[:3].isalpha():
            return False
        if not re.search(r"[0-9]", stripped):
            return False
        return True

    @staticmethod
    def _scan_identifier_tokens(text: str) -> List[str]:
        return re.findall(r"[A-Z0-9]{4,}", (text or "").upper())

    def _extract_tp_name_hint(self, question: str) -> Optional[str]:
        for token in sorted(
            self._scan_identifier_tokens(question), key=len, reverse=True
        ):
            if self._looks_like_tp_name(token):
                return token
        return None

    def _extract_product_code_hint(self, question: str) -> Optional[str]:
        for token in self._scan_identifier_tokens(question):
            if self._looks_like_product_code(token):
                return token
        return None

    # Known test class names for extraction
    KNOWN_TEST_CLASSES: Tuple[str, ...] = (
        "VminTC",
        "PrimePatConfigTestMethod",
        "DcTestMethod",
        "FunctionalTest",
        "IdsTestMethod",
        "ArrayTestMethod",
        "ScanTestMethod",
        "PlistExecuteTest",
    )

    def _extract_test_class_from_question(self, question: str) -> Optional[str]:
        """Extract a test class name like 'VminTC' from the question text."""
        normalized = question.lower()
        for tc in self.KNOWN_TEST_CLASSES:
            if tc.lower() in normalized:
                return tc
        # Fallback: look for pattern like "test class <Name>" or "TestType <Name>"
        match = re.search(
            r"(?:test\s*class|testtype)\s+([A-Za-z][A-Za-z0-9]*(?:TC|Method|Test)?)",
            question,
            re.IGNORECASE,
        )
        if match:
            return match.group(1)
        return None

    def _normalize_identifiers(
        self,
        product_collection: MongoCollection,
        question: str,
        product_code: str,
        tp_name: str,
    ) -> Tuple[str, str]:
        normalized_product = (product_code or "").strip().upper()
        normalized_tp = (tp_name or "").strip().upper()

        if normalized_product and not self._looks_like_product_code(normalized_product):
            if not normalized_tp and self._looks_like_tp_name(normalized_product):
                normalized_tp = normalized_product
            normalized_product = ""

        if not normalized_tp:
            normalized_tp = self._extract_tp_name_hint(question) or normalized_tp

        if not normalized_product:
            candidate = self._extract_product_code_hint(question)
            if candidate:
                normalized_product = candidate

        if normalized_product:
            existing = product_collection.find_one(
                {"product_code": normalized_product}, {"_id": 1}
            )
            if not existing:
                normalized_product = ""

        return normalized_product, normalized_tp

    def _match_catalog_token(
        self, question: str, catalog: Iterable[str]
    ) -> Optional[str]:
        normalized_question = self._normalize_token(question)
        for entry in catalog:
            token = self._normalize_token(entry)
            if token and token in normalized_question:
                return entry
        return None

    def _infer_product_from_question(
        self,
        product_collection: MongoCollection,
        question: str,
    ) -> Optional[dict]:
        if not question.strip():
            return None
        normalized = question.lower()
        candidates: List[Tuple[int, dict]] = []
        for row in product_collection.find({}, {"product_code": 1, "product_name": 1}):
            name = (row.get("product_name") or "").strip()
            if not name:
                continue
            if name.lower() in normalized:
                return row
            name_tokens = set(self._tokenize(name))
            question_tokens = set(self._tokenize(question))
            if not name_tokens or not question_tokens:
                continue
            overlap = len(name_tokens & question_tokens)
            if overlap:
                candidates.append((overlap, row))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        top_score = candidates[0][0]
        best = [row for score, row in candidates if score == top_score]
        return best[0] if len(best) == 1 else None

    def _candidate_product_suggestions(
        self,
        product_collection: MongoCollection,
        text: str,
        limit: int = 6,
    ) -> List[str]:
        suggestions: List[Tuple[int, str]] = []
        tokens = set(self._tokenize(text))
        for row in product_collection.find({}, {"product_code": 1, "product_name": 1}):
            name = (row.get("product_name") or "").strip()
            code = (row.get("product_code") or "").strip()
            label = f"{name} ({code})" if code else name
            if tokens:
                score = len(set(self._tokenize(name)) & tokens)
                if score:
                    suggestions.append((score, label))
            else:
                suggestions.append((1, label))
        suggestions.sort(key=lambda item: item[0], reverse=True)
        return [label for _, label in suggestions[:limit]]

    @staticmethod
    def _ensure_tp_document_id(ctx: TPContext | Mapping[str, Any]) -> str:
        if isinstance(ctx, TPContext):
            candidate = ctx.tp_document_id
        elif isinstance(ctx, Mapping):
            candidate = ctx.get("tp_document_id") or ctx.get("_id") or ""
        else:  # pragma: no cover - defensive guard for unexpected injections
            raise TypeError("Context payload must be a TPContext or mapping")

        if not candidate:
            raise ValueError(
                "Context does not include a tp_document_id. "
                "Call answer_tp_question first or provide a full context payload."
            )
        return str(candidate)

    def _hydrate_context(
        self,
        ctx: TPContext | Mapping[str, Any],
        *,
        question_hint: str = "",
    ) -> TPContext:
        if isinstance(ctx, TPContext):
            return ctx
        if not isinstance(
            ctx, Mapping
        ):  # pragma: no cover - consistent defensive guard
            raise TypeError("Context payload must be a TPContext or mapping")

        tp_document_id = ctx.get("tp_document_id") or ctx.get("_id")
        if tp_document_id:
            return TPContext(
                tp_name=ctx.get("tp_name") or "unknown",
                git_hash=ctx.get("git_hash") or "unknown",
                tp_document_id=str(tp_document_id),
                product_code=ctx.get("product_code"),
                product_name=ctx.get("product_name"),
                ingested_at=ctx.get("ingested_at"),
                metadata=ctx.get("metadata") or {},
            )

        client = self._get_mongo_client(None)
        if client is None:
            raise ValueError(
                "Context does not include a tp_document_id and Mongo connection is unavailable."
            )

        ingest_collection = self._get_collection(client, INGEST_COLLECTION)
        product_collection = self._get_collection(client, PRODUCT_COLLECTION)

        raw_product_code = (ctx.get("product_code") or "").strip()
        raw_tp_name = (ctx.get("tp_name") or "").strip()
        name_hint = ctx.get("product_name") or ctx.get("product_label") or ""
        question_hint = (
            question_hint
            or ctx.get("question")
            or ctx.get("prompt")
            or ctx.get("query")
            or ""
        )

        if not raw_tp_name and self._looks_like_tp_name(raw_product_code):
            raw_tp_name = raw_product_code
            raw_product_code = ""

        normalized_product, normalized_tp = self._normalize_identifiers(
            product_collection,
            question_hint,
            raw_product_code,
            raw_tp_name,
        )

        return self._resolve_context(
            ingest_collection,
            product_collection,
            normalized_product,
            normalized_tp,
            product_name_hint=name_hint,
            question=question_hint,
        )

    def _list_module_names(
        self, module_collection: MongoCollection, ctx: TPContext
    ) -> List[str]:
        modules: List[str] = []
        cursor = module_collection.find(
            {"tp_document_id": ctx.tp_document_id},
            {"module_name": 1},
        ).sort("module_name", 1)
        for row in cursor:
            name = row.get("module_name")
            if name:
                modules.append(name)
        return modules

    def _list_subflows(
        self, test_collection: MongoCollection, ctx: TPContext
    ) -> List[str]:
        distinct = test_collection.distinct(
            "subflow", {"tp_document_id": ctx.tp_document_id}
        )
        flows = sorted(value for value in distinct if isinstance(value, str) and value)
        return flows

    def _resolve_context(
        self,
        ingest_collection: MongoCollection,
        product_collection: MongoCollection,
        product_code: Optional[str],
        tp_name: Optional[str],
        *,
        product_name_hint: Optional[str] = None,
        question: str = "",
    ) -> TPContext:
        conditions: List[Dict[str, Any]] = []
        product_code = (product_code or "").strip()
        product_name_hint = (product_name_hint or "").strip()
        if product_code and not self._looks_like_product_code(product_code):
            product_name_hint = product_name_hint or product_code
            product_code = ""

        # If no explicit tp_name provided, look up LatestTP from product_configs
        # This ensures questions about a product (without specifying a revision) use the configured LatestTP
        if not tp_name:
            # Try to get the LatestTP from product_configs
            product_filter: Dict[str, Any] = {}
            if product_code:
                product_filter["product_code"] = product_code
            elif product_name_hint:
                product_filter["product_name"] = {"$regex": product_name_hint, "$options": "i"}
            else:
                # Try fuzzy match on question
                fuzzy_match = self._infer_product_from_question(product_collection, question)
                if fuzzy_match:
                    product_filter["product_code"] = fuzzy_match.get("product_code")
            if product_filter:
                product_doc = product_collection.find_one(product_filter)
                if product_doc and product_doc.get("latest_tp"):
                    tp_name = product_doc["latest_tp"]
                    if not product_code:
                        product_code = product_doc.get("product_code", "")

        if tp_name:
            conditions.append({"tp_name": tp_name.strip()})
        if product_code:
            conditions.append(
                {
                    "$or": [
                        {"product.product_code": product_code},
                        {"metadata.product_code": product_code},
                    ]
                }
            )
        if not product_code and product_name_hint:
            regex = {"$regex": re.escape(product_name_hint), "$options": "i"}
            conditions.append(
                {
                    "$or": [
                        {"product.product_name": regex},
                        {"metadata.product_name": regex},
                    ]
                }
            )

        attempts: List[Tuple[Dict[str, Any], Optional[str]]] = []
        base_query: Dict[str, Any] = {"$and": conditions} if conditions else {}
        attempts.append((base_query, product_name_hint or None))

        if not product_code:
            fuzzy_source = product_name_hint or question
            fuzzy_match = self._infer_product_from_question(
                product_collection, fuzzy_source or ""
            )
            if fuzzy_match:
                fuzzy_code = fuzzy_match.get("product_code")
                if fuzzy_code:
                    fuzzy_conditions = [
                        entry
                        for entry in conditions
                        if not entry.get("$or")
                        or "product.product_code" not in entry["$or"][0]
                    ]
                    fuzzy_conditions.append(
                        {
                            "$or": [
                                {"product.product_code": fuzzy_code},
                                {"metadata.product_code": fuzzy_code},
                            ]
                        }
                    )
                    attempts.append(
                        (
                            {"$and": fuzzy_conditions} if fuzzy_conditions else {},
                            fuzzy_match.get("product_name"),
                        )
                    )

        for query, inferred_name in attempts:
            cursor = ingest_collection.find(query).sort("ingested_at", -1)
            try:
                doc = cursor.next()
            except StopIteration:
                continue
            metadata = doc.get("metadata", {}) or {}
            product_blob = doc.get("product", {}) or {}
            return TPContext(
                tp_name=doc.get("tp_name", "unknown"),
                git_hash=doc.get("git_hash", "unknown"),
                tp_document_id=doc["_id"],
                product_code=product_blob.get("product_code")
                or metadata.get("product_code")
                or product_code,
                product_name=product_blob.get("product_name")
                or metadata.get("product_name")
                or inferred_name
                or product_name_hint,
                ingested_at=doc.get("ingested_at"),
                metadata=metadata,
            )

        suggestion_text = question or product_name_hint or product_code or "product"
        suggestions = self._candidate_product_suggestions(
            product_collection, suggestion_text
        )
        if not suggestions:
            suggestions = self._candidate_product_suggestions(
                product_collection, "", limit=8
            )
        message = (
            "I couldn't find an ingest for that product description. "
            "Please specify a product code (e.g., 8PXM) or pick one of the known names."
        )
        raise self.ContextResolutionError(message, suggestions)

    @staticmethod
    def _module_filters(
        module_key: Optional[str],
    ) -> Tuple[Dict[str, Any], Optional[str]]:
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
        ctx: TPContext | Mapping[str, Any],
        extra_filters: Optional[Dict[str, Any]] = None,
        *,
        limit: int = 60,
        sort_field: str = "instance_name",
    ) -> List[dict]:
        try:
            tp_document_id = self._hydrate_context(ctx).tp_document_id
        except ValueError:
            tp_document_id = self._ensure_tp_document_id(ctx)
        filters: Dict[str, Any] = {"tp_document_id": tp_document_id}
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
            test_collection.find(filters, projection).sort(sort_field, 1).limit(limit)
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
                    test_type=row.get("test_type_detail")
                    or row.get("test_type")
                    or "n/a",
                    level=row.get("level") or "n/a",
                    timing=row.get("timing") or "n/a",
                    plist=row.get("plist") or "n/a",
                )
            )
        return "\n".join(lines)

    # Connection helpers -----------------------------------------------------------------
    def _get_mongo_client(
        self, user_valves: Optional["Tools.UserValves"] = None
    ) -> Optional[MongoClientType]:
        uri = MONGO_URI
        if self._mongo_client is None:
            self._mongo_client = MongoClient(uri)
        return self._mongo_client

    def _get_collection(self, client: MongoClientType, name: str) -> MongoCollection:
        return client[MONGO_DATABASE][name]

    # Query helpers ----------------------------------------------------------------------
    def _fetch_product_state(
        self, product_collection: MongoCollection, product_code: Optional[str]
    ) -> Optional[dict]:
        if not product_code:
            return None
        return product_collection.find_one({"product_code": product_code})

    def _sample_test_instances(
        self, test_collection: MongoCollection, ctx: TPContext
    ) -> List[dict]:
        cursor = test_collection.find(
            {"tp_document_id": ctx.tp_document_id},
            {"module_name": 1, "instance_name": 1, "status": 1},
        ).limit(self.valves.max_test_instance_rows)
        return list(cursor)

    def _sample_flow_rows(
        self,
        flow_collection: MongoCollection,
        ctx: TPContext,
        keyword: Optional[str] = None,
    ) -> List[dict]:
        filters: Dict[str, Any] = {"tp_document_id": ctx.tp_document_id}
        if keyword:
            filters["$or"] = [
                {"module": {"$regex": keyword, "$options": "i"}},
                {"dutflow": {"$regex": keyword, "$options": "i"}},
                {"instance": {"$regex": keyword, "$options": "i"}},
            ]
        return list(
            flow_collection.find(
                filters, {"module": 1, "dutflow": 1, "instance": 1, "sequence_index": 1}
            ).limit(self.valves.max_flow_rows)
        )

    def _fetch_module_summary(
        self, module_collection: MongoCollection, ctx: TPContext
    ) -> List[dict]:
        return list(
            module_collection.find(
                {"tp_document_id": ctx.tp_document_id},
                {
                    "module_name": 1,
                    "total_tests": 1,
                    "total_kill": 1,
                    "percent_kill": 1,
                },
            )
        )

    def _fetch_setpoints(
        self, setpoints_collection: MongoCollection, ctx: TPContext, keyword: str
    ) -> List[dict]:
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
            setpoints_collection.find(
                filters, {"module": 1, "test_instance": 1, "method": 1, "values": 1}
            ).limit(50)
        )

    def _fetch_artifact_summary(
        self, artifacts_collection: MongoCollection, ctx: TPContext
    ) -> Dict[str, int]:
        pipeline = [
            {"$match": {"tp_document_id": ctx.tp_document_id}},
            {"$group": {"_id": "$category", "count": {"$sum": 1}}},
        ]
        summary: Dict[str, int] = {}
        for row in artifacts_collection.aggregate(pipeline):
            summary[row["_id"] or "unknown"] = row["count"]
        return summary

    def _fetch_hvqk_module_inventory(
        self,
        hvqk_collection: MongoCollection,
        ctx: TPContext | Mapping[str, Any],
    ) -> List[dict]:
        tp_document_id = self._ensure_tp_document_id(ctx)
        pipeline = [
            {"$match": {"tp_document_id": tp_document_id}},
            {
                "$group": {
                    "_id": "$module_name",
                    "files": {"$addToSet": "$file_name"},
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"_id": 1}},
        ]
        modules: List[dict] = []
        for row in hvqk_collection.aggregate(pipeline):
            module = row.get("_id")
            if not module:
                continue
            files = sorted(file for file in row.get("files", []) if file)
            modules.append(
                {
                    "module": module,
                    "files": files,
                    "count": row.get("count", len(files)),
                }
            )
        return modules

    @staticmethod
    def _stringify_config_value(value: Any, *, max_length: int = 320) -> str:
        if value is None:
            return "n/a"
        if isinstance(value, (int, float)):
            text = str(value)
        elif isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value)
            except TypeError:
                text = str(value)
        if len(text) > max_length:
            text = text[: max_length - 3] + "..."
        return text

    def _format_hvqk_listing(self, modules: List[dict]) -> str:
        if not modules:
            return "I did not find HVQK configs for this TP."
        lines = ["💧 HVQK waterfall configs by module:"]
        preview_limit = 12
        for entry in modules:
            module = entry.get("module", "unknown")
            files = entry.get("files", [])
            lines.append(f"- {module} ({len(files)} files):")
            for name in files[:preview_limit]:
                lines.append(f"    - {name}")
            remaining = len(files) - preview_limit
            if remaining > 0:
                lines.append(f"    - … and {remaining} more")
        return "\n".join(lines)

    def _fetch_hvqk_configs(
        self,
        hvqk_collection: MongoCollection,
        ctx: TPContext | Mapping[str, Any],
        module_name: Optional[str] = None,
    ) -> List[dict]:
        tp_document_id = self._ensure_tp_document_id(ctx)
        filters: Dict[str, Any] = {"tp_document_id": tp_document_id}
        if module_name:
            filters["module_name"] = module_name
        cursor = hvqk_collection.find(filters).sort("file_name", 1)
        return list(cursor)

    def _format_hvqk_module_detail(self, module_name: str, entries: List[dict]) -> str:
        if not entries:
            return f"No HVQK configs recorded for {module_name}."
        label_parts = module_name.split("_", 1)
        canonical_label = (
            f"{label_parts[0]}::{label_parts[1]}"
            if len(label_parts) == 2
            else module_name
        )
        lines = [
            f"🧾 HVQK configs for {module_name} ({len(entries)} files)",
            f"HVQK coverage for {module_name}",
            f"Module {canonical_label} HVQK assets",
        ]

        for entry in entries:
            file_name = entry.get("file_name", "unknown")
            config = entry.get("config") or {}
            lines.append(f"{file_name}")
            lines.append("| Key | Value |")
            lines.append("| --- | --- |")

            def _add_row(key: str, value: Any) -> None:
                lines.append(f"| {key} | {self._stringify_config_value(value)} |")

            _add_row("DomainName", config.get("DomainName"))
            _add_row("InstanceName", config.get("InstanceName"))
            _add_row("VoltageStart", config.get("VoltageStart"))
            _add_row("VoltageStop", config.get("VoltageStop"))
            _add_row("Pin", config.get("Pin"))
            _add_row("VoltageSteps", config.get("VoltageSteps"))
            _add_row("RelativePath", entry.get("relative_path"))
            _add_row("SizeBytes", entry.get("size_bytes"))
            _add_row("SHA256", entry.get("sha256"))
            lines.append("")

        return "\n".join(lines).strip()

    def _summarize_hvqk_modules(
        self,
        test_collection: MongoCollection | Mapping[str, Any] | None,
        ctx: TPContext | Mapping[str, Any],
    ) -> List[dict]:
        collection_handle: MongoCollection
        if hasattr(test_collection, "aggregate"):
            collection_handle = test_collection  # type: ignore[assignment]
        else:
            client = self._get_mongo_client(None)
            if client is None:
                raise ValueError(
                    "Test collection handle missing and Mongo connection is unavailable."
                )
            collection_handle = self._get_collection(client, TEST_INSTANCES_COLLECTION)
        try:
            tp_context = self._hydrate_context(ctx)
        except ValueError:
            tp_document_id = self._ensure_tp_document_id(ctx)
        else:
            tp_document_id = tp_context.tp_document_id
        pipeline = [
            {
                "$match": {
                    "tp_document_id": tp_document_id,
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
        return list(collection_handle.aggregate(pipeline))

    # Answer generators -----------------------------------------------------------------
    def _format_current_tp_answer(
        self, ctx: TPContext, product_state: Optional[dict]
    ) -> str:
        ingested = ctx.ingested_at.isoformat() if ctx.ingested_at else "unknown"
        product_line = (
            f"Product: {ctx.product_name or 'unknown'} ({ctx.product_code or 'n/a'})"
        )
        product_details = []
        if product_state:
            if product_state.get("latest_tp"):
                product_details.append(
                    f"Configured latest TP: {product_state['latest_tp']}"
                )
            if product_state.get("network_path"):
                product_details.append(f"Network path: {product_state['network_path']}")
            if product_state.get("number_of_releases"):
                product_details.append(
                    f"Tracked releases: {product_state['number_of_releases']}"
                )
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
        status_summary = ", ".join(
            f"{status}:{count}" for status, count in status_counter.most_common(5)
        )
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

    def _format_flow_answer(
        self, ctx: TPContext, flows: List[dict], keyword: str, description: str
    ) -> str:
        if not flows:
            return f"No {description} references matched '{keyword}'."
        lines = [f"🔍 {description} hits for '{keyword}' ({len(flows)} results)"]
        for row in flows[:8]:
            lines.append(
                f"- Module {row.get('module')} | Flow {row.get('dutflow')} | Instance {row.get('instance')} (index {row.get('sequence_index')})"
            )
        return "\n".join(lines)

    def _format_setpoint_answer(
        self, ctx: TPContext, matches: List[dict], keyword: str
    ) -> str:
        if not matches:
            return f"No setpoints mention '{keyword}'."
        lines = [f"⚙️ Setpoint coverage for '{keyword}'"]
        for row in matches[:8]:
            values = ", ".join(row.get("values", [])[:5])
            lines.append(
                f"- Module {row.get('module')} | Instance {row.get('test_instance')} | Method {row.get('method')} | Values {values or 'n/a'}"
            )
        return "\n".join(lines)

    def _format_module_focus(
        self, ctx: TPContext, modules: List[dict], keyword: str, description: str
    ) -> str:
        matches = [
            row for row in modules if keyword in (row.get("module_name") or "").lower()
        ]
        if not matches:
            return f"No modules hint at {description}."
        lines = [f"🛠️ {description.title()} modules"]
        for row in matches[:6]:
            lines.append(
                f"- {row.get('module_name')} | Tests {row.get('total_tests')} | Kill {row.get('total_kill')} | Kill% {row.get('percent_kill')}"
            )
        return "\n".join(lines)

    def _format_snapshot(
        self, ctx: TPContext, modules: List[dict], artifacts: Dict[str, int]
    ) -> str:
        top_modules = sorted(
            modules, key=lambda row: row.get("total_tests", 0) or 0, reverse=True
        )[:5]
        module_lines = [
            f"- {row.get('module_name')} (tests={row.get('total_tests')}, kill={row.get('total_kill')}, kill%={row.get('percent_kill')})"
            for row in top_modules
        ]
        artifact_lines = [
            f"{category}:{count}" for category, count in artifacts.items()
        ]
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
            await emitter.error(
                "Mongo connection unavailable. Hardcoded URI returned no client."
            )
            return "Mongo connection is not configured."

        product_code = (
            product_code or user_valves.product_code or self.valves.default_product_code
        )
        tp_name = tp_name or user_valves.tp_name or self.valves.default_tp_name

        ingest_collection = self._get_collection(client, INGEST_COLLECTION)
        product_collection = self._get_collection(client, PRODUCT_COLLECTION)
        product_code, tp_name = self._normalize_identifiers(
            product_collection,
            question,
            product_code,
            tp_name,
        )
        classification = QuestionClassifier.classify(question)

        try:
            ctx = self._resolve_context(
                ingest_collection,
                product_collection,
                product_code,
                tp_name,
                question=question,
            )
        except self.ContextResolutionError as exc:
            suggestion_lines = (
                "\n".join(f"- {label}" for label in exc.suggestions)
                if exc.suggestions
                else ""
            )
            guidance = str(exc)
            if suggestion_lines:
                guidance = f"{guidance}\nTry one of these products next time:\n{suggestion_lines}"
            await emitter.error(guidance)
            return guidance
        except ValueError as exc:
            await emitter.error(str(exc))
            return str(exc)

        await emitter.progress(
            f"Resolved context: {ctx.tp_name} ({ctx.product_code or 'unknown product'})"
        )

        module_collection = self._get_collection(client, MODULE_SUMMARY_COLLECTION)
        test_collection = self._get_collection(client, TEST_INSTANCES_COLLECTION)
        flow_collection = self._get_collection(client, FLOW_MAP_COLLECTION)
        port_collection = self._get_collection(client, PORT_RESULTS_COLLECTION)
        setpoints_collection = self._get_collection(client, SETPOINTS_COLLECTION)
        artifacts_collection = self._get_collection(client, ARTIFACTS_COLLECTION)
        hvqk_collection = self._get_collection(client, HVQK_COLLECTION)

        modules_catalog = self._list_module_names(module_collection, ctx)
        flows_catalog = self._list_subflows(test_collection, ctx)
        module_match = self._match_catalog_token(question, modules_catalog)
        flow_match = self._match_catalog_token(question, flows_catalog)

        normalized_question = question.lower()
        if classification == "hvqk_flow" and module_match:
            classification = "hvqk_module_detail"
        if (
            classification == "fallback"
            and (module_match or flow_match)
            and "test" in normalized_question
        ):
            classification = "module_flow_tests"

        answer_lines: List[str] = []

        try:
            if classification == "current_tp":
                product_state = self._fetch_product_state(
                    product_collection, ctx.product_code
                )
                answer_lines.append(self._format_current_tp_answer(ctx, product_state))

            elif classification == "list_tests":
                tests = self._sample_test_instances(test_collection, ctx)
                answer_lines.append(
                    self._format_test_list_answer(
                        ctx, tests, flows_catalog, modules_catalog
                    )
                )

            elif classification == "hvqk_flow":
                flows = self._sample_flow_rows(flow_collection, ctx, keyword="hvqk")
                if not flows:
                    flows = self._sample_flow_rows(
                        flow_collection, ctx, keyword="water"
                    )
                answer_lines.append(
                    self._format_flow_answer(
                        ctx, flows, "HVQK", "HVQK/Waterfall flow content"
                    )
                )
                hvqk_summary = self._summarize_hvqk_modules(test_collection, ctx)
                answer_lines.append(self._format_hvqk_summary(hvqk_summary))
                hvqk_modules = self._fetch_hvqk_module_inventory(hvqk_collection, ctx)
                answer_lines.append(self._format_hvqk_listing(hvqk_modules))

            elif classification == "hvqk_module_detail":
                module_name = module_match or "requested module"
                hvqk_entries = self._fetch_hvqk_configs(
                    hvqk_collection,
                    ctx,
                    module_name=module_match,
                )
                answer_lines.append(
                    self._format_hvqk_module_detail(module_name, hvqk_entries)
                )

            elif classification == "tp_snapshot":
                modules = self._fetch_module_summary(module_collection, ctx)
                artifact_summary = self._fetch_artifact_summary(
                    artifacts_collection, ctx
                )
                answer_lines.append(
                    self._format_snapshot(ctx, modules, artifact_summary)
                )
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
                continuity_rows = self._fetch_test_rows(
                    test_collection,
                    ctx,
                    extra_filters={
                        "scrum": "TPI",
                        "module_name": {"$regex": "VCC", "$options": "i"},
                        "instance_name": {"$regex": "CONT", "$options": "i"},
                    },
                    limit=min(100, self.valves.max_test_instance_rows),
                )
                lines = [
                    self._format_flow_answer(ctx, flows, "VCC", "VCC continuity flows")
                ]
                lines.append(
                    self._format_detailed_tests(
                        continuity_rows,
                        title="TPI_VCC continuity instances",
                        limit=min(100, self.valves.max_test_instance_rows),
                    )
                )
                answer_lines.extend(lines)

            elif classification == "test_class_filter":
                # Extract test class name from question (e.g., "VminTC")
                test_class_name = self._extract_test_class_from_question(question)
                if not test_class_name:
                    test_class_name = "VminTC"  # Default fallback
                filters: Dict[str, Any] = {
                    "test_type": {"$regex": f"^{test_class_name}$", "$options": "i"}
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
                descriptor = f"test class {test_class_name}"
                if module_label:
                    descriptor += f" in {module_label}"
                if flow_match:
                    descriptor += f" / {flow_match}"
                answer_lines.append(
                    self._format_detailed_tests(
                        rows,
                        title=f"Settings of {descriptor}",
                        limit=self.valves.max_test_instance_rows,
                    )
                )

            elif classification == "setpoints":
                matches = self._fetch_setpoints(setpoints_collection, ctx, "VMIN")
                answer_lines.append(self._format_setpoint_answer(ctx, matches, "Vmin"))

            elif classification == "array_repair":
                # Q10: Filter for array repair tests per methodology:
                # - instance_name contains 'REPAIR' but not 'REPAIR_RESET'
                # - status != 'Bypassed'
                # - test_type = 'PrimePatConfigTestMethod'
                # - test_type_detail = 'FUSECONFIG'
                filters: Dict[str, Any] = {
                    "instance_name": {
                        "$regex": "REPAIR",
                        "$options": "i",
                    },
                    "status": {"$ne": "Bypassed"},
                    "test_type": {"$regex": "^PrimePatConfigTestMethod$", "$options": "i"},
                    "test_type_detail": {"$regex": "^FUSECONFIG$", "$options": "i"},
                }
                rows = self._fetch_test_rows(
                    test_collection,
                    ctx,
                    extra_filters=filters,
                    limit=self.valves.max_test_instance_rows,
                )
                # Filter out REPAIR_RESET in post-processing
                rows = [
                    r for r in rows
                    if "repair_reset" not in (r.get("instance_name") or "").lower()
                ]
                if rows:
                    answer_lines.append(
                        f"✅ Yes, array repair is present with {len(rows)} test(s):"
                    )
                    answer_lines.append(
                        self._format_detailed_tests(
                            rows,
                            title="Array repair instances (FUSECONFIG)",
                            limit=self.valves.max_test_instance_rows,
                        )
                    )
                else:
                    answer_lines.append(
                        "❌ No array repair tests found matching the criteria "
                        "(REPAIR in name, not Bypassed, PrimePatConfigTestMethod, FUSECONFIG)."
                    )

            elif classification == "hot_repair":
                # Q11: Hot array repair = array repair tests in SDT subflows
                # Same filters as array_repair PLUS subflow contains 'SDT'
                filters: Dict[str, Any] = {
                    "instance_name": {
                        "$regex": "REPAIR",
                        "$options": "i",
                    },
                    "status": {"$ne": "Bypassed"},
                    "test_type": {"$regex": "^PrimePatConfigTestMethod$", "$options": "i"},
                    "test_type_detail": {"$regex": "^FUSECONFIG$", "$options": "i"},
                    "subflow": {"$regex": "SDT", "$options": "i"},
                }
                rows = self._fetch_test_rows(
                    test_collection,
                    ctx,
                    extra_filters=filters,
                    limit=self.valves.max_test_instance_rows,
                )
                # Filter out REPAIR_RESET in post-processing
                rows = [
                    r for r in rows
                    if "repair_reset" not in (r.get("instance_name") or "").lower()
                ]
                if rows:
                    answer_lines.append(
                        f"🔥 Yes, hot array repair is present with {len(rows)} test(s) in SDT flow:"
                    )
                    answer_lines.append(
                        self._format_detailed_tests(
                            rows,
                            title="Hot array repair instances (SDT + FUSECONFIG)",
                            limit=self.valves.max_test_instance_rows,
                        )
                    )
                else:
                    answer_lines.append(
                        "❌ No hot array repair tests found matching the criteria "
                        "(REPAIR in name, not Bypassed, PrimePatConfigTestMethod, FUSECONFIG, SubFlow contains SDT)."
                    )

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
                summary = self._format_snapshot(
                    ctx,
                    modules,
                    self._fetch_artifact_summary(artifacts_collection, ctx),
                )
                answer_lines.append(summary)
                answer_lines.append(
                    self._format_test_list_answer(
                        ctx, tests, flows_catalog, modules_catalog
                    )
                )

            result = (
                "\n\n".join(answer_lines)
                if answer_lines
                else "I could not build a response."
            )
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
