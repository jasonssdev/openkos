# Archive Report: attach-at-ingest

**Date archived:** 2026-10-04
**Issue:** #1268 (deliverable 1)
**ADR:** ADR-0045, "Ingest attaches to an existing same-type, same-key concept instead of forking it", Accepted at archive
**Tasks:** all checked.

## PR

Implementation merged in #1283.

## Delta specs merged into `openspec/specs/`

Each delta was composed with `sdd-archive-compose` (exit 0); every MODIFIED heading matched exactly once in the living spec.

| Domain | Requirements before → after |
|---|---|
| ingestion | 63 → 66 (3 added, 4 modified) |
| folder-watch | 8 → 9 (1 added) |
| ingest-application-service | 11 → 12 (1 added) |
