# Archive Report: deprecated-status-export

**Date archived:** 2026-09-30
**Issue:** #1075
**ADR:** ADR-0032, "Frontmatter `status: deprecated` is a marked export of computed supersession, never read back", Accepted at archive
**Tasks:** all checked; 6.3 (Non-Goals prose and the ADR flip) was done at archive.

## PRs (stacked to main)

| PR | Scope |
|---|---|
| #1108 | Planning, ADR-0032 (Proposed); projection, marker and read-back rule; `lint` drift scan; `repair` fixer |
| this PR | Writers: `reconcile`/`relate`, `forget`/`purge`, `merge`/`unmerge`; docs; archive |

## Delta specs merged into `openspec/specs/`

Each existing domain was composed with `gentle-ai sdd-archive-compose` (exit 0). Every ADDED and MODIFIED heading appears exactly once in its living spec, and the RENAMED reconcile requirement appears under its new name.

| Domain | Requirements before → after |
|---|---|
| deprecated-status-export | new → 5 |
| lint | 13 → 14 |
| privacy-purge | 16 → 17 |
| typed-relationships | 4 → 5 |
| reconcile-command | 10 → 10 ("Additive-Only, No Status/Lifecycle Write" renamed to "Additive-Only, Status Written Only As The Supersedes Export") |
| entity-resolution-merge | 17 → 17 |
| forget-command | 20 → 20 |
| okf-format-migration | 8 → 8 (Non-Goals prose edited at archive) |
| status-aware-retrieval | 5 → 5 (Non-Goals prose edited at archive) |

## Findings during apply

- `unmerge` does not refuse when the survivor was edited after the merge. It restores the ledger's verbatim bytes, so any later edit to the survivor is discarded. This was pinned by a test; it predates this change and is tracked in #1110.
