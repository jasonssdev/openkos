# Proposal: event-date-local-time — a Source's event_date may carry a naive local time

Refs #1349. Related: #1014 (decision evolution), ADR-0023, ADR-0025.

## Intent

`event_date` is a calendar date, so two Decisions made on one day always
resolve to `equal` and every same-day REVERSES or REFINES finding stays
undirected. Real meeting series (workshops, sprint days, the AMI scenarios)
run several meetings on one day. The only workaround today is to invent
dates. This change lets `event_date` carry an optional time so those
findings can be directed from true evidence.

## Decision (owner, 2026-10-13)

Widen the existing `event_date` key. Do not add a separate key and do not add
an ordinal. The accepted tradeoff: an older engine reads a date-time value as
malformed and treats the Source as undated. Recorded in ADR-0052, which amends
ADR-0023 decision 1 on the value format only.

## Scope

### In scope

- `--event-date` and the stored value accept `YYYY-MM-DD`,
  `YYYY-MM-DDTHH:MM` and `YYYY-MM-DDTHH:MM:SS`. Naive local time only; an
  offset, `Z`, fractional seconds, a space separator and week dates are
  refused (CLI exit `2`; a stored value is malformed as today).
- Canonical write: `YYYY-MM-DDTHH:MM`, with `:SS` only when non-zero. A
  date-only value is written byte-identically.
- Direction compares at the finest precision both sides carry. Either side
  date-only means a date comparison, and the same day is `equal`.
- A date and a date-time on the same day, reached through one Decision, are
  `multiple`.
- Every consumer that renders or compares a resolved event date: the
  ingest preview line, the superseded-history label, the revisions report,
  the stored findings readback (old date-only rows stay readable).

### Out of scope

- File-name inference stays date-only. A directory or glob `--event-date`
  stays refused.
- An incoming `date:` frontmatter key stays date-only.
- Time zones, an ordinal `event_sequence`, a separate `event_time` key.

## Affected specs

- `ingestion`: Source Event Date Frontmatter Key, Explicit `--event-date`
  Ingest Flag, Re-Ingest Event-Date Carry-Forward (MODIFIED).
- `decision-revision-detection`: Decision Event Date Resolution, Direction
  Comes Only From Resolved Source event_date (MODIFIED).
- `query-answer`: date phrase precision (ADDED).

## Compatibility

Date-only bundles are byte-identical and nothing migrates. A bundle that
records a date-time is read by an older engine as malformed for that Source
(undated, warned, never carried forward).

## Delivery

Two slices: this ADR and spec change, then the implementation with its tests
and `docs/cli.md`.
