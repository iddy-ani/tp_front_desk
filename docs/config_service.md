# Config Service

`Tools/config_service.py` centralizes product metadata updates, validates network paths, and keeps `Products.json` aligned with actual ingestion history.

## Features

- **Status reporting** – shows each product’s configured `LatestTP`, the last revision ingested by the automation pipeline, processed counts, and optional network-path validation results.
- **Path validation** – with `--validate-paths`, UNC paths are checked for existence so you can catch stale or moved shares.
- **Sync latest revisions** – `--sync-latest` pulls the most recent ingested TP per product out of `state/daily_scanner_state.json`, updates `LatestTP`, and shuffles `ListOfReleases` so releases stay deduped and ordered. Pair this with `--apply` to write the changes back to `Products.json`.
- **JSON summaries** – pass `--print-json` to feed status/changes directly into dashboards or chat bots.

## Typical commands

Inspect current config vs. ingestion state (no file changes):

```powershell
Set-Location C:\Users\ianimash\source\repos\tp_front_desk
C:/Users/ianimash/source/repos/venvs/tp_front_desk/Scripts/python.exe Tools/config_service.py --validate-paths --print-json
```

Sync latest ingested revisions from the state file and persist them:

```powershell
Set-Location C:\Users\ianimash\source\repos\tp_front_desk
C:/Users/ianimash/source/repos/venvs/tp_front_desk/Scripts/python.exe Tools/config_service.py --sync-latest --apply
```

Dry run a scoped update for a single product:

```powershell
C:/Users/ianimash/source/repos/venvs/tp_front_desk/Scripts/python.exe Tools/config_service.py --product-codes 8PXMCV --sync-latest --validate-paths
```

## Notes

- `Products.json` accepts either a single object or an array. The service preserves the original shape when writing.
- The ingestion state file can be overridden with `--state-file` if the automation pipeline runs in another workspace.
- Updates are never written unless `--apply` is present; use this to preview upcoming changes in CI or local checks.
- `ListOfReleases` is automatically deduped when syncing, keeping the newest entries at the front so dashboards stay consistent.
