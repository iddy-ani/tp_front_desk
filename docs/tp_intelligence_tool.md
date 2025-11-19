# Test Program Intelligence Tool

The **Test Program Intelligence** Open WebUI tool answers common PDE-style questions ("current TP", "what tests", HVQK flows, SDT coverage, etc.) using the normalized MongoDB collections produced by `Tools/ingest_tp.py`.

## Configuration

Set the Mongo connection information via environment variables or directly through the tool valves:

| Variable | Purpose | Default |
| --- | --- | --- |
| `TPFD_MONGO_URI` | Connection string used by `pymongo.MongoClient` | required |
| `TPFD_MONGO_DB` | Mongo database name | `tpfrontdesk` |
| `TPFD_MONGO_COLLECTION` | `ingest_artifacts` collection name | `ingest_artifacts` |
| `TPFD_MONGO_MODULE_SUMMARY_COLLECTION` | Module summary collection | `module_summary` |
| `TPFD_MONGO_TEST_INSTANCES_COLLECTION` | PAS test instances collection | `test_instances` |
| `TPFD_MONGO_FLOW_MAP_COLLECTION` | Flow map collection | `flow_map` |
| `TPFD_MONGO_SETPOINT_COLLECTION` | Setpoint collection | `setpoints` |
| `TPFD_MONGO_ARTIFACTS_COLLECTION` | Artifact reference collection | `artifacts` |
| `TPFD_MONGO_PRODUCT_COLLECTION` | Product configuration collection | `product_configs` |

Optional defaults can also be supplied via valves (`default_product_code`, `default_tp_name`) so analysts do not need to repeat them in each prompt.

## Supported Questions

The tool performs lightweight keyword classification before running Mongo queries. Current intents include:

- **current_tp** – "What is the current test program?"
- **list_tests** – "What tests does it have?"
- **hvqk_flow** – "Where is the HVQK waterfall?"
- **tp_snapshot** – "What does it look like?"
- **vcc_continuity** – "Where is the VCC continuity test?"
- **setpoints** – "What are the VminTC settings?"
- **array_repair** – "Is array repair running?"
- **hot_repair** – "Is HOT repair running?"
- **sdt_flow** – "Is there much content in the SDT flow?"

When a question does not match any intent, the tool returns a general snapshot plus a sample module/test breakdown.

## Usage Example

```text
> Run Test Program Intelligence
Question: What is the current test program for Panther Lake?
Product code: 8PXM
```

Example output:

```
✅ Current ingest for TP_PL_23Q4
Product: Panther Lake (8PXM)
Git hash: 1d23456
Ingested: 2024-07-11T01:22:04+00:00
Product config: Configured latest TP: TP_PL_23Q4; Network path: \\an
tp_share\...
Flow tables: FLOW1, FLOW2, FLOW3
```

## Implementation Notes

- The tool streams progress via the `EventEmitter` helper so Open WebUI users get real-time status updates.
- Mongo lookups rely on the deterministic `_id` / `tp_document_id` produced by the ingestion pipeline (`Tools/tp_ingest/persistence.py`).
- Each response pulls only a capped sample (configurable via `max_test_instance_rows` and `max_flow_rows`) to keep turn-around time low.
- The script lives in `Tools/test_program_intelligence.py` and can be executed manually for smoke testing using `python Tools/test_program_intelligence.py` once `TPFD_MONGO_URI` is set.
