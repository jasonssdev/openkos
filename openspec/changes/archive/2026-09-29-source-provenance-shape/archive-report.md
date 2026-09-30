# Archive Report: source-provenance-shape

**Date archived:** 2026-09-29
**Issue:** #1076
**ADR:** none (a data-shape correction; the docs and canonical example already described it)
**Tasks:** all checked.

## PR

| PR | Scope |
|---|---|
| #1107 | Ingest stops writing `provenance: [raw/<file>]` on Source concepts; archived in the same PR |

## Delta specs merged into `openspec/specs/`

Composed with `gentle-ai sdd-archive-compose` (exit 0); both MODIFIED headings appear exactly once in the living spec.

| Domain | Requirements before → after |
|---|---|
| ingestion | 57 → 57 ("Ingest Raw Copy and Source Concept Generation" and "OKF-Native Provenance" modified) |

## Notes

No consumer reads a Source's own `provenance`. Lint's raw-resource exclusion is kept so workspaces written before this change stay clean.
