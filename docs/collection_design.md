# Mongo Collection Design

This blueprint standardizes how test-program (TP) ingest data is stored in MongoDB so downstream
jobs can query across products without re-parsing the raw reports. Every derived collection hangs
off the canonical ingest document whose `_id` equals `{tp_name}:{git_hash}`.

## Anchor: `ingest_artifacts`
- `_id = {tp_name}:{git_hash}` (already implemented in `MongoWriter`).
- Carries the parsed integration report, product metadata, setpoints summary, and warning log.
- Each derived collection keeps a `tp_document_id` field pointing back to this anchor so we can
  dereference shared fields (product code, revision metadata, ingestion timestamps) without
  duplicating them everywhere.

### Cross-collection conventions
1. **Foreign key**: `tp_document_id` (string) always matches an `_id` in `ingest_artifacts`.
2. **Traceability fields**: store `tp_name`, `git_hash`, and `product_code` to simplify reporting
   pipelines that cannot perform `$lookup`s.
3. **Deterministic `_id`s**: `{tp_document_id}|{entity}` so retries replace the same document and we
   can bulk delete by prefix.
4. **Timestamps**: `ingested_at` captures the exact UTC time rows are written, mirroring the anchor.
5. **Compound indexes**: each collection declares a `{tp_document_id:1, discriminator:1}` index to
   enable fast filtering by TP + module/test/flow without scanning the entire collection.

## `module_summary`
Summaries from `Reports/PASReport_ModuleSummary.csv`—one document per module.

| Field | Type | Notes |
| --- | --- | --- |
| `_id` | string | `{tp_document_id}|module:{module_name}` |
| `tp_document_id` | string | Foreign key back to anchor |
| `tp_name`/`git_hash`/`product_code` | string | Copied from anchor metadata |
| `module_name` | string | CSV `Module Name` |
| `totals` | object | `{tests, tests_run, kill, edc, monitor, float, bypassed, always_bypassed}` |
| `percentages` | object | `{kill_pct, kill_monitor_pct}` (numbers) |
| `derived` | object | Pre-computed ratios such as `run_rate = tests_run/tests`,
  `bypass_rate = bypassed/tests` (stored with two decimal precision) |
| `source_path` | string | Absolute path to the CSV to aid debugging |
| `ingested_at` | datetime | UTC timestamp |

**Indexes**
- `{tp_document_id: 1, module_name: 1}` (unique)
- `{module_name: 1, kill_pct: -1}` for dashboards tracking hottest modules across TPs.

## `test_instances`
Row-level data from `Reports/PASReport.csv` (current `pas_records`). This collection replaces the
old name so the query vocabulary matches engineering terminology.

| Field | Type | Notes |
| `_id` | string | `{tp_document_id}|instance:{instance_name}` (append `:partition` when duplicates exist) |
| `tp_document_id`, `tp_name`, `git_hash`, `product_code` | string | as above |
| `module_name`, `instance_name`, `status`, `bypass` ... `instance_user` | mixed | Same columns we already parse into `models.PASRecord` |
| `kill_ratio` | float | `kill_pat_count / max(monitor+kill, 1)` to highlight risky tests |
| `monitor_ratio` | float | `monitor_pat_count / tests_run` |
| `module_summary_id` | string | direct reference to the owning module summary document |
| `ingested_at` | datetime | UTC |

**Indexes**
- Unique `{tp_document_id: 1, instance_name: 1}`
- Secondary `{tp_document_id: 1, module_name: 1, status: 1}` to quickly answer "show me failing
  scans for module X".

## `port_results`
Full fidelity from `Reports/PASReport_PortLevel.csv`, which analysts use when spelunking a specific
pin/port. The header contains a duplicate `ModuleUser`; we persist the first as `module_user` and
map the second to `port_owner`.

| Field | Type | Notes |
| `_id` | string | `{tp_document_id}|port:{instance_name_port}` |
| `instance_name_port` | string | Raw key from CSV |
| `module_name`, `module_user`, `port_owner`, `port` | string | Ownership metadata |
| `status`, `bypass`, `bin`, `hb`, `sb`, `counter`, `plist` | mixed | Telemetry columns |
| `monitor_pat_count`, `kill_pat_count`, `skipped_pat_count` | int | counts |
| `content_directory`, `pattern_vrev`, `test_type`, `tp_options`, `scrum` | string | tracing |
| `partition`, `test_category`, `subflow`, `pattern_ratio`, `voltage_domain`, `corner`, `frequency` |
| `instance_name`, `module_summary_id`, `test_instance_id` | string | derived connections |
| `ingested_at` | datetime | UTC |

**Indexes**
- `{tp_document_id: 1, instance_name_port: 1}` unique
- `{tp_document_id: 1, module_name: 1, port: 1}` for per-module triage
- Partial index on `{status: 1}` for quick filtering of `Kill` / `EDC` statuses.

## `scoreboard`
Replaces `scoreboard_entries` with deterministic `_id`s.

| Field | Type | Notes |
| `_id` | string | `{tp_document_id}|score:{module}:{test_instance}:{base_number or 0}:{duplicate or 0}` |
| `module`, `test_instance`, `base_number`, `duplicate`, `in_range`, `extra_info` | existing scoreboard columns |
| `test_instance_id` | string | link into `test_instances` |
| `tp_document_id`, `tp_name`, `git_hash`, `product_code`, `ingested_at` | standard fields |

**Indexes**
- `{tp_document_id: 1, module: 1, test_instance: 1}` unique
- `{module: 1, base_number: 1}` to service historical lookups independent of TP.

## `flow_map`
Hierarchical relationships from `Reports/StartItemList.csv` (module ⟶ DUT flow ⟶ instance).

| Field | Type | Notes |
| `_id` | string | `{tp_document_id}|flow:{module}:{dutflow}:{instance}` |
| `module`, `dutflow`, `instance` | string | values from CSV |
| `sequence_index` | int | stable ordering based on CSV line number for UI replay |
| `linked_instance_id` | string | tie into `test_instances` when names match |
| `ingested_at`, `tp_document_id`, etc. | standard fields |

**Indexes**
- `{tp_document_id: 1, module: 1, dutflow: 1}`
- `{instance: 1}` for reverse lookups given a PAS instance.

## `artifacts`
Structured copy of what `collect_artifact_references` emits so we can query artifact presence
without re-walking the TP directory.

| Field | Type | Notes |
| `_id` | string | `{tp_document_id}|artifact:{relative_path}` |
| `relative_path`, `name`, `category`, `size_bytes` | from `models.ArtifactReference` |
| `sha256` | string | optional checksum when we extend the collector |
| `exists_on_disk` | bool | mark false when copy failed but metadata is kept |
| `ingested_at`, `tp_document_id`, etc. | standard |

**Indexes**
- `{tp_document_id: 1, category: 1}`
- `{category: 1, name: 1}` for quick "who published CAKE audit" style queries.

## Relationship summary
- All derived collections own `tp_document_id` plus deterministic `_id`s so re-ingesting the same
  TP replaces documents atomically.
- `module_summary` ⟶ `test_instances` ⟶ `port_results` form a hierarchy via the `*_id` fields.
- `scoreboard`, `flow_map`, and `artifacts` reference `test_instances` where possible, but they
  only require the TP foreign key so they can still persist even when PAS data is missing.

## Implementation notes
1. Extend `tp_ingest.models` with new dataclasses (`ModuleSummaryEntry`, `PortResultRow`,
   `FlowMapEntry`, `ArtifactRecord`) plus helper IDs.
2. Add parsers for `PASReport_ModuleSummary.csv`, `PASReport_PortLevel.csv`, and
   `StartItemList.csv` that stream the large files to avoid high memory usage.
3. Update `MongoWriter` to manage the new collections, enforce indexes, and expose methods such as
   `write_module_summary_entries` and `write_test_instances` (delete-by-TP, insert-many, return count).
4. Enhance `collect_artifact_references` to record SHA-256 when `--hash-artifacts` is set so the new
   collection can confirm copies.
5. Once the writer functions exist, call them from `ingest_tp.run_ingestion` after the existing
   persistence steps so a single ingestion transaction produces every collection in one go.

By following this structure every downstream consumer gets consistent IDs, referential integrity,
plus predictable indexes for dashboards and APIs.
