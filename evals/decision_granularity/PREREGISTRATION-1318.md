# Pre-registration: #1318, the collapse to one object

Written BEFORE any treatment run of this pass. Model `qwen3:8b`, generation
ceiling 8192, context window 12288, the real `extract_concept_union` over the
synthetic fixtures in `granularity_fixtures.py`, driven by
`run_granularity.py`.

## Diagnosis (from code and traces, before the treatment)

Every collapse in the stored baselines is `produced = 1`, judge skipped. The
mechanism, checked against the code and against live runs:

1. Both unchunked passes return the model's single object in the collapsed
   runs (a live probe of 6 runs per fixture printed each pass: `[('Event',
   'Architecture review, 10 February')]` twice). That half is model behaviour.
2. The pipeline already owns a recovery for exactly this shape: the #584/#642
   re-ask, which fires on a sole object that restates the source title (any
   length) or on a sole object from a source of 2000 characters or more. Both
   fixtures are 1210 and 1408 characters, so only the restate arm can fire.
3. It never fires, `reask_runs 0` in every collapsed live run, because
   `_restates_source_topic` answers no. The source title is the file stem
   `2026-02-10-architecture-review`; the model's object is `Architecture
   review, 10 February`. Containment (`_contains_source_topic`) compares
   `{2026, architecture, review}` with `{architecture, review, february}`;
   neither contains the other, because the date is written two ways. The
   participant-capture pass is also off: `_is_meeting_shaped` does not match
   `architecture-review` and the source has no speaker turns.
4. The judge is then skipped by design (#644, a single candidate), so nothing
   downstream can recover it.
5. The re-ask itself works on these sources: called by hand on the collapsed
   object it returned the Person `Rafael Okonkwo` and the dead-letter-queue
   Decision in 5 of 5 calls on `en-review-new-engineer`, and all three
   Decisions in 5 of 5 calls on `en-review-3-decisions`.

Exposure against the stored baseline runs (every arm, `produced = 1`): the
date-blind trigger flips from not firing to firing in 76 of 87 collapsed runs
(33 of 33 on `en-review-new-engineer`, 43 of 54 on `en-review-3-decisions`).
It flips in 0 of 104 collapsed control runs (`en-note-single-decision`), so
the control can only report that the treatment left a genuinely single-subject
note alone, not that it was at risk.

Not explained by this mechanism: on `en-review-3-decisions` 6 of 13 `produced
= 1` baseline runs have `judge_status = ok` (a judge that kept one of several
candidates), which the re-ask trigger, read before the judge, cannot touch.

## Treatment (one arm)

`datefold`: `concept._title_tokens` ignores date tokens, all-digit tokens and
full English/Spanish month names, so the containment arm of
`_restates_source_topic` treats a date-stamped file stem and a model title
that differs from it only by the date as the same topic. Applied by
monkeypatch in the harness; production is not edited until the result is read.
The prompt is the shipped one.

## Baseline

Two stored baseline arms of 15 runs per fixture, pooled to 30
(`results/runs-baseline-20261002T020803Z-qwen3-8b.json`, #1256, and
`results/runs-baseline-20261006T034051Z-qwen3-8b.json`, 0.5.1). A third
baseline arm of 15 runs on the three treatment fixtures is run on this branch
before the treatment and pooled in; the adoption rule below must hold against
the two-arm pool AND the three-arm pool.

Pooled two-arm values (`n of TOTAL`):

| fixture | metric | arm 1 | arm 2 | pooled |
| --- | --- | --- | --- | --- |
| `en-review-new-engineer` | person hit | 8 of 15 | 9 of 15 | 17 of 30 |
| `en-review-3-decisions` | split | 7 of 15 | 4 of 15 | 11 of 30 |
| `en-review-3-decisions` | recall | 0.978 | 0.778 | 0.878 |
| `en-review-new-engineer` | recall | 1.000 | 1.000 | 1.000 |
| `en-note-single-decision` | over-split | 0 of 15 | 0 of 15 | 0 of 30 |

## Metrics and adoption rule (fixed before any treatment run)

Arms: `datefold`, n = 15 per fixture, on `en-review-new-engineer`,
`en-review-3-decisions` and the control `en-note-single-decision`.

Targets, one-sided Fisher exact against the pooled baseline:

- T1 `en-review-new-engineer` person hit: at least 13 of 15 (p = 0.043 against
  17 of 30; 12 of 15 gives p = 0.112 and does not qualify).
- T2 `en-review-3-decisions` split: at least 11 of 15 (p = 0.022 against 11 of
  30; 10 of 15 gives p = 0.056 and does not qualify).

Adopt only if BOTH targets hold AND every guard holds:

- G1 the control over-splits (`n_decisions > 1`) in at most 1 of 15 runs;
- G2 mean topic recall on each of the three fixtures is not below the pooled
  baseline minus 0.05;
- G3 person stubs per run rise by no more than 0.5 on any fixture;
- G4 no errored runs;
- G5 mean latency on the control stays under 1.5x the pooled baseline; on the
  two target fixtures it stays under 2.0x (recovering a collapse legitimately
  adds a re-ask and a judge call, which is the cost being bought).

Anything short of that records the result and ships nothing. If only one
target holds, nothing ships and the split is reported. The control's exposure
is zero (see above), so G1 can only confirm the control is left alone; it is
not evidence of safety on other sources.

Also reported, not gating: `reask_runs` per run (the mechanism check: a
treated run that is still a collapse should show `reask_runs 1`), the
`produced = 1` count per fixture, decisions per run.

## Not in scope of this registration

A second candidate (a lower low-yield length threshold) is NOT registered. If
`datefold` fails its bar, any further candidate needs its own registration.
