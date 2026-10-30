# `queue_outcomes`: how much of the pending-work queue is applied as proposed

**Question.** Per queue kind, what fraction of the rows a human answered were
applied exactly as proposed, versus modified or declined? The answer decides
whether any kind is a candidate for auto-apply, which would need its own ADR
(cf. ADR-0034). Tracked in [#1214](https://github.com/jasonssdev/openkos/issues/1214).

**Status: measured 2026-10-30. No kind is a candidate for auto-apply.** See
[Results](#results). No engine change was needed: the queue already records
`resolution` (`as_proposed`, `modified`, `declined`, `stale`), `resolved_by`,
`created_at` and `resolved_at` per row.

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

## Results

**Bar, fixed in #1214 before any number was read.** A kind is a candidate for
unattended application only if it has at least 30 answered rows and at least
95% of them were applied as proposed. `contradiction` and `revision` are
excluded. A candidate still needs its own ADR (cf. ADR-0034); the measurement
adopts nothing. The bar is applied per workspace: pooling was not part of it.

**Workspaces.** Every workspace on the owner's machine with a queue, read
2026-10-30 over its whole history (no `--since`/`--until`). Counts only.

| workspace | queue history | used |
|---|---|---|
| A, desktop app trial | 2026-10-02 to 2026-10-17 | yes |
| B, E2E 0.4.0 | 2026-10-02 | no: a copy of A (identical `created_at` on every row), answered less |
| C, E2E 0.3.1 | 2026-10-01 | yes, with the caveat below |
| D, the owner's vault | 2026-10-29 | yes |
| E, E2E 0.2.9 | none | no: `no-queue` |

**As proposed, of the rows a human answered.**

| kind | A | C | D | bar |
|---|---|---|---|---|
| identity | 21 of 56 | 0 of 0 | 3 of 5 | not met |
| relation_type | 49 of 52 (3 modified) | 50 of 50 | 0 of 0 | not met (see below) |
| volatility | 3 of 3 | 4 of 4 | 0 of 0 | not met: n < 30 |
| contradiction | 0 of 0 | 0 of 0 | none | excluded |
| watch_refusal | none | 0 of 0 (1 stale) | none | vacuous |

- **identity** is far from the bar: in A, 35 of 56 answers declined the
  proposed merge (25 through `adjudicate`, 10 through `keep-distinct`).
- **relation_type** is the only kind near it. A, the longest-lived workspace,
  reads 49 of 52 (94.2%): three answers changed the proposed type, so it misses
  95% by one row. C reads 50 of 50, but all 50 were resolved within 41 seconds
  (18:50:19 to 18:51:00 UTC), which is a bulk acceptance, not 50 reviewed
  decisions; it cannot carry the verdict. Pooling A and C would read 99 of 102,
  but the bar was not set on pooled data and is not re-set after seeing it.
- **volatility** is always accepted, on 7 answered rows: too few to read.

**Verdict.** No kind clears the bar. Nothing is proposed for auto-apply, and no
ADR follows. The cheapest re-read is this command on a workspace with a few
weeks of reviewed `relation_type` answers; the question to settle then is
whether its modification rate stays under 5%.
