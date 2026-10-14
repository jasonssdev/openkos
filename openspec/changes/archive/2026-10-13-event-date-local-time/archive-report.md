# Archive Report: event-date-local-time

**Change**: event-date-local-time — a Source's `event_date` may carry a naive local time
**Archived**: 2026-10-13
**Archive Path**: `openspec/changes/archive/2026-10-13-event-date-local-time/`

## Summary

The change is implemented and merged (PR #1355, Refs #1349). A Source's
`event_date` accepts `YYYY-MM-DD`, `YYYY-MM-DDTHH:MM` and `YYYY-MM-DDTHH:MM:SS`
(naive local time); direction compares at the finest precision both sides carry.
ADR-0052 is now Accepted. No verify-report was produced.

## Specs Synced

| Domain | Action | Requirement | Operation |
|--------|--------|-------------|-----------|
| ingestion | Updated | Source Event Date Frontmatter Key | MODIFIED (replaced) |
| ingestion | Updated | Explicit `--event-date` Ingest Flag | MODIFIED (replaced) |
| ingestion | Updated | Re-Ingest Event-Date Carry-Forward | MODIFIED (replaced) |
| decision-revision-detection | Updated | Decision Event Date Resolution | MODIFIED (replaced) |
| decision-revision-detection | Updated | Direction Comes Only From Resolved Source event_date | MODIFIED (replaced) |
| query-answer | Updated | Revision History Date Phrase Keeps The Stored Precision | ADDED (appended) |

Every delta heading name-matched an existing main-spec heading exactly (MODIFIED)
or was confirmed absent (ADDED). No REMOVED or RENAMED entries.

## Spec Corrections At Archive

The shape regex in `okf.py` bounds hour `00`-`23` and minute/second `00`-`59`
(Python 3.14 `fromisoformat` accepts `T24:00`). The ingestion `--event-date`
requirement now states this bound, in both the main spec and the archived
delta, so the spec does not overstate what is accepted.

## Archive Contents

- proposal.md: present
- specs/: ingestion, decision-revision-detection, query-answer deltas
- exploration.md, design.md, tasks.md: not produced by this change

## ADR-0052 Status

Updated from Proposed to Accepted in the ADR frontmatter, body status line, and
`docs/adr/README.md` index row.
