---
type: Decision
title: "ADR-0077: A changed finding is re-checked for its one pair, and the run publishes its own row"
description: A changed contradiction or revision is made current from the app by a cost-consented re-detection of that one pair, quoted by its decision key through a new openkos.api function (not a widened quote_judging), whose run replaces or retires the open row itself instead of waiting for the free refresh.
status: Proposed
date: 2026-10-20
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-20T00:00:00Z
sensitivity: public
---

# ADR-0077: A changed finding is re-checked for its one pair, and the run publishes its own row

- **Status:** Proposed
- **Date:** 2026-10-20

## Context

A contradiction or revision row whose pages changed lists as `fresh: false`, and
every write against it answers `Stale` (`row_inputs_changed`). Nothing in the app
made one current (#1523): the model-free queue refresh (#1391) republishes rows
from verdicts the engine already stored, for the kinds it can recompute
(identity, relation type, volatility), and re-detecting a contradiction or a
revision is a model call that only `openkos contradictions`, `openkos revisions`
and a daemon pass made. The maintainer chose to let the person agree to that
call from the app, with the same consent rules as the batch check
([ADR-0059](0059-judging-is-a-quote-bound-application-surface-that-persists-per-chunk-and-never-merges.md)).

Three forces shaped it. The detection is bundle-wide by construction
(`plan_candidates`, `plan_revisions`), but the person agrees to one pair, so the
spend must be that pair's one call. `openkos.api` is versioned
([ADR-0060](0060-the-stable-python-api-is-a-versioned-facade-with-one-error-root.md))
and its classifier counts a widened parameter annotation as breaking, so adding
the two kinds to the `kind` of `quote_judging` would mean `API_VERSION` 2 for a
change whose clients are additive. And the judging run's write-through to an open
row is amend-only on purpose: replacing a row whose inputs moved is a producer's
job, and the free refresh, the identity producer, cannot recompute these kinds.

## Decision

1. **One pair, through the existing detection.** `finding_recheck` quotes and
   runs a `contradiction` or `revision` row. The quote is LLM-free: it finds the
   row's pair in the engine's own plan and binds its work item to the pair's
   current input digests, with `calls` 1. The run judges only that candidate, by
   handing `find_contradictions` / `judge_revisions` a plan narrowed to it. No
   detection logic is written; nothing else in the bundle is judged.
2. **A new function, not a widened one.** `openkos.api.quote_finding_recheck(
   workspace, kind, decision_key)` quotes it; `run_judging` runs the token like
   any other (it dispatches on the token's kind). `quote_judging` keeps its batch
   kinds, so its signature, and `API_VERSION`, do not move. The HTTP quote route
   takes an optional `decision_key` and serves whichever applies.
3. **The run publishes its own row.** After the verdict is stored (pages
   unchanged since the call began), the run replaces the open row with the new
   finding, or retires it as stale when the finding no longer holds (a consistent
   or uncertain contradiction, a non-actionable revision). A verdict about pages
   that moved during the call stores nothing and leaves the row. The retirement
   is a new `pending_queue.retire_open_by_key`; it is not a human resolution.
4. **Consent is the batch check's.** The same quote-bound token, the same
   `QuoteStale` on a moved binding or pair, the same one active run, the same
   locality and confidential rules; the screen words it as "Check these two
   again?".

## Consequences

**What becomes easier:**

- A changed finding no longer waits for a terminal or a daemon pass; Check again
  makes it decidable, or takes it out of To review, for one call.
- No new detection path to keep honest: the pair goes through the code `curate`,
  the verbs and the daemon use, over the same input-digest convention (now
  shared in `application/finding_inputs`).

**What becomes harder, and accepted:**

- A revision's candidate plan reads the vectors the last reindex stored, so a
  decision edited since then has no candidate until it is reindexed: its quote
  says there is nothing to check yet. The check never embeds.
- `judge_revisions` used to stamp its stored finding with digests taken after
  the model call, so a page edited during it left a finding that read as fresh
  for bytes it was never judged on. It now pins the pair's digests before the
  call and drops the finding if they moved, as contradictions do through
  `persist_findings`. The fix is in `judge_revisions` itself, so the `revisions`
  verb and the daemon get it too; a verdict dropped this way is judged again on
  the next run.
- The run is a second producer of these two kinds' rows, beside the daemon's
  pass; both go through `queue_producers.publish`, so the statistics stay one
  vocabulary.

## Alternatives considered

- **Widen `quote_judging`'s `kind`.** One function, but `API_VERSION` 2.
- **Retire changed findings in the free refresh (option 2 of #1523).** No model
  call and no consent, but the finding silently leaves To review and returns only
  on a later detection pass, with no way for a person to ask for it.
- **Leave it to the background engine (option 3).** Nothing to build; the app
  cannot say when it will happen, and the goal is that a person never needs a
  terminal.
- **Re-detect the whole bundle and publish every row.** The refresh's shape, but
  it spends the batch's calls to settle one row the person is looking at.
