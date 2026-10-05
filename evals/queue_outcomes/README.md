# `queue_outcomes`: how much of the pending-work queue is applied as proposed

**Question.** Per queue kind, what fraction of the rows a human answered were
applied exactly as proposed, versus modified or declined? The answer decides
whether any kind is a candidate for auto-apply, which would need its own ADR
(cf. ADR-0034). Tracked in [#1214](https://github.com/jasonssdev/openkos/issues/1214).

**Status: instrument only.** There is no result yet. The measurement needs a
workspace that has accumulated real queue history; this reader is what turns
that history into numbers once it exists. No engine change was needed: the
queue already records `resolution` (`as_proposed`, `modified`, `declined`,
`stale`), `resolved_by`, `created_at` and `resolved_at` per row.

## What it reads

`WORKSPACE/.openkos/findings.db`, `pending_items`, opened `mode=ro`. It never
creates, migrates or writes the store, and it never calls a model.

```
uv run python evals/queue_outcomes/read_queue_outcomes.py WORKSPACE
uv run python evals/queue_outcomes/read_queue_outcomes.py WORKSPACE --since 2026-10-01 --until 2026-11-01
uv run python evals/queue_outcomes/read_queue_outcomes.py --self-test
```

The period selects rows by `created_at`, half-open `[since, until)`, so every
row in it falls into exactly one outcome and the outcomes sum to the cohort.

## How to read the report

- Outcomes per kind: `as proposed`, `modified`, `declined`, `stale`, `open`.
- The headline is `as proposed N of M answered`, where *answered* is
  as proposed + modified + declined. Stale and open rows are outside it and are
  shown beside it. Nothing is printed as a bare percentage.
- **Skipped is not a recorded state.** Skipping at a prompt leaves the row
  open, so `open` means "unresolved so far": skipped and never-seen together.
  The report names this in its column header.
- **Vacuous kinds are flagged, not scored.** A `contradiction` row proposes no
  value (the direction is the human's), a `watch_refusal` is answered by the
  ingest itself, and a `revision` has no resolving write path, so none of them
  can read anything but `as_proposed` (or stay open). A 100% there says nothing
  about auto-apply. The list lives in `VACUOUS_AS_PROPOSED`; re-read
  `application/queue_resolution.py` when that module changes.
- **Lifetime caveat.** The queue is a derived cache; a rebuild or `purge`
  resets its history, as `openkos pending --stats` also warns.

## Limits

- A small cohort proves little. Read `n` before the fraction; a kind with a
  handful of answered rows is not a verdict.
- `as_proposed` is the engine's own comparison (`queue_resolution.py`), not an
  independent check; it asserts equality of the applied and proposed value.
- The self-test builds its synthetic queue through the engine's own schema
  (`pending_queue.ensure_schema`), so a schema change that this reader no
  longer matches fails it.
