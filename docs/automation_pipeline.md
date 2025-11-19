# Automation Pipeline

The daily scanner discovers new TP releases on each product's `NetworkPath`, copies the full folder into the local `Test Programs` directory, and then invokes the existing ingestion pipeline so MongoDB stays current.

## How it works

1. `Tools/daily_scanner.py` loads every entry from `Products.json` (array or single object) to learn each product code and network share.
2. For each `NetworkPath`, the script lists the most recent folders (sorted by modification time). A small state file keeps track of which TP folders have already been ingested so repeat runs only target genuinely new releases.
3. Every new folder triggers `copy_network_files.ps1`, copying the release from the network share into the repo's `Test Programs/<TP_NAME>` directory (or a custom `--tp-root`).
4. Once the copy succeeds, the scanner calls `run_ingestion` the same way the single-TP CLI does, so git-hash dedupe ensures Mongo `_id = tp_name:git_hash` remains unique.
5. Successful runs update `state/daily_scanner_state.json` with the git hash and Mongo `_id`, preventing duplicates on future scans. Each attempt is also written to a JSON-lines log so failures are easy to audit, and final failures are copied into an alerts file for downstream monitoring.

## Prerequisites

- Ensure `TPFD_TP_ROOT`, `TPFD_MONGO_*`, and other ingestion env vars are configured if you use non-default values.
- The account running the scanner must have read access to each `NetworkPath` UNC share and permission to run PowerShell scripts.
- `copy_network_files.ps1` uses `robocopy`, so it must be available on the host (Windows default).

## Typical commands

Dry run showing which folders would be processed without copying or ingesting:

```powershell
C:/Users/ianimash/source/repos/venvs/tp_front_desk/Scripts/python.exe Tools/daily_scanner.py --dry-run --product-codes 8PXMCV --max-network-scan 5
```

Daily ingest across all products, persisting to Mongo and updating the state file:

```powershell
C:/Users/ianimash/source/repos/venvs/tp_front_desk/Scripts/python.exe Tools/daily_scanner.py --max-network-scan 10 --max-per-product 2
```

Reprocess a specific product (ignoring the state file) and limit to a single new TP:

```powershell
C:/Users/ianimash/source/repos/venvs/tp_front_desk/Scripts/python.exe Tools/daily_scanner.py --product-codes 8PXMCV --max-per-product 1 --force
```

## Operational notes

- Use `--state-file` to relocate or reset the ingestion ledger; deleting the file forces the next run to treat every TP folder as new.
- `--limit` enforces a global cap on ingestion attempts across all products, useful for time-boxed cron jobs.
- Combine `--no-persist` with `--dry-run` when validating access to new network paths without touching MongoDB.
- `--log-file` defaults to `logs/daily_scanner.log` and records every attempt (including warnings) as JSON; ship or tail this file to track historical success/failure rates.
- `--alerts-file` captures only the final failed attempts and can feed alerting jobs; delete it after triage if you want a clean slate.
- Use `--max-retries`/`--retry-delay` to control how aggressively the scanner retries flaky network copies or ingest runs (default: three attempts with a 30s pause).
- Schedule the command via Windows Task Scheduler or any orchestrator, pointing at the repository venv Python executable.

## Alert monitoring

`Tools/monitor_alerts.py` reads `state/daily_scanner_alerts.jsonl`, detects new entries, and optionally triggers a shell command for each one. Typical usage from Task Scheduler:

```powershell
Set-Location C:\Users\ianimash\source\repos\tp_front_desk
C:/Users/ianimash/source/repos/venvs/tp_front_desk/Scripts/python.exe Tools/monitor_alerts.py --print-json --notify-command "powershell.exe -File notify_team.ps1 -Message '{tp_name} failed ({status})'"
```

- Exit code is `1` when new alerts are found, letting schedulers flag the run or dispatch email. Exit `0` means no new failures.
- The script keeps its own pointer (`state/alert_monitor_state.json`) so each alert is sent exactly once; pass `--reset` if you purposely want to replay history.
- Use `--verbose` while testing to see each command execution before wiring it into your alerting pipeline.

## Config service

Use `Tools/config_service.py` to keep `Products.json` in sync with the latest ingested revisions and to validate `NetworkPath` entries. See `docs/config_service.md` for end-to-end instructions and sample commands.
