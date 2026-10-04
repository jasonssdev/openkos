# Archive Report: retire-superseded-sources

**Date archived:** 2026-10-04
**Issue:** #1268 (deliverable 2)
**ADR:** ADR-0046, "Effective deprecation follows the provenance of a superseded Source; its export stays edge-only", Accepted at archive
**Tasks:** all checked except 4.3 (the final gates run), which was executed in the PR's CI rather than ticked here.

## PR

Implementation merged in #1284.

## Delta specs merged into `openspec/specs/`

Each delta was composed with `sdd-archive-compose` (exit 0); every MODIFIED heading matched exactly once in the living spec.

| Domain | Requirements before → after |
|---|---|
| deprecated-status-export | 5 → 6 (1 added, 1 modified) |
| forget-command | 22 → 23 (1 added, 1 modified) |
| list-command | 10 → 10 (1 modified) |
| privacy-purge | 18 → 18 (1 modified) |
| status-aware-retrieval | 5 → 5 (1 modified) |
| typed-relationships | 7 → 7 (1 modified) |
