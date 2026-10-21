# Proposal: recheck-changed-findings

Refs #1523 (found while wiring contradictions and revisions into To review, #1476).
Governing decisions: ADR-0053, ADR-0059, ADR-0060, ADR-0072 and the new ADR-0077.

## Intent

A contradiction or revision row whose pages changed is listed `fresh: false` and
every write against it answers `Stale` (`row_inputs_changed`). Nothing the app
can call makes it current: the queue refresh recomputes identity, relation-type
and volatility rows only, and re-detection is a model call. The maintainer chose
option 1 of #1523: a cost-consented re-check of the two pages, with the same
consent rules as the check.

Success: a changed finding opens with **Check again**; agreeing quotes and runs
the one pair, and the row is made current or leaves To review; nothing else in
the workspace is re-detected.

## Scope

### In Scope

1. `finding_recheck` (application): a quote and a run for one `contradiction` or
   `revision` row, over the existing detection narrowed to that pair.
2. `pending_queue.retire_open_by_key`; `finding_inputs` (the contradiction input
   digests and their persist, moved out of `cli.curate`).
3. `openkos.api.quote_finding_recheck` (+ `FindingKind`, `BatchJudgingKind`),
   `run_judging` dispatch, `QuoteRequest.decision_key` on `POST /v1/judging/quote`.
4. To review: Check again for a changed finding, its consent and its result.
5. ADR-0077, `docs/decision-wording.md`, the CHANGELOG.

### Out of Scope

- Re-detecting the whole bundle, or a model call anywhere but the one pair.
- Option 2 and 3 of #1523 (retiring changed findings in the free refresh;
  leaving them to the daemon).
- A watch refusal (still ingested through its own quote).

## Approach

See ADR-0077. The run publishes its own row because the free refresh cannot: the
refresh replaces rows from persisted verdicts of kinds it can recompute.

## Rollback

Additive within `API_VERSION` 1. Reverting restores the "can only wait" screen;
the run only replaces or retires an open row (as stale), never deletes one.
