# Archive Report: source-tag-sync

**Date archived:** 2026-09-30
**Issue:** #1093
**ADR:** ADR-0033, "Source tag sync is union-only and never tags below the Source's sensitivity", Accepted at archive
**Tasks:** all checked.

## PR

One PR carries the planning, the four implementation phases (pure resolution in `bundle/provenance.py`, the `application/lifecycle.py` service, the `sync-tags` verb, and the ingest advisory) and this archive.

## Delta specs merged into `openspec/specs/`

The ingestion delta was composed with `gentle-ai sdd-archive-compose` (exit 0), and its MODIFIED heading appears exactly once in the living spec. `tag-sync` is a new domain, copied whole.

| Domain | Requirements before → after |
|---|---|
| ingestion | 57 → 57 ("Converged Re-Ingest Source-Only Rewrite" modified) |
| tag-sync | new → 8 |

## Notes

- Tags carry the Source's information into FTS and the embedding header, so a concept classified below its Source is skipped and pointed at `set-sensitivity`. Tag values never appear in `log.md` or the commit message.
- Union only: a tag removed from a Source is not removed downstream, because synced tags cannot be told apart from hand-added ones.
