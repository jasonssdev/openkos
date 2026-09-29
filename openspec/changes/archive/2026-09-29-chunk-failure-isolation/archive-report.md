# Archive Report: chunk-failure-isolation

**Date archived:** 2026-09-29
**Issue:** #1053
**Status:** archived in the same PR as the implementation

## Delta merged into `openspec/specs/ingestion/spec.md`

| Section | Requirement | Canonical count after merge |
|---|---|---|
| MODIFIED | Extraction Degrades Gracefully on LLM Unavailability | 1 |
| ADDED | Chunked Extraction Isolates a Single Failed Window | 1 |

The canonical spec went from 51 to 52 requirements. It was composed with `gentle-ai sdd-archive-compose` (exit 0), and no delta markers remain.

## What shipped

- A chunk whose extraction call fails with a `BackendError`-family error is retried once. If the retry also fails, the chunk is skipped and the other chunks' objects are kept.
- `BackendUnavailable` is never retried and still degrades the whole file.
- If every chunk fails, the Source-only degrade is unchanged.
- Unchunked sources are unchanged.
- The loss is named on stderr and recorded as the `extraction_notice` token `chunk-extraction-partial`, which a plain re-ingest retries.

## Out of scope

A dedicated `lint` / `status` surface for `chunk-extraction-partial` is a follow-up; stderr and the self-healing re-ingest disclose the loss today.
