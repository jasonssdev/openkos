# `decision_revisions` — measuring the decision-revision-detector's Phase A leaves before Phase B is built (#1014)

Issue #1014's own order: measure the already-merged Phase A leaves --
`resolution/decision_subject.py`'s subject pass, and
`resolution/decision_revision.py`'s direction rule, candidate generation and
judge -- **before** any Phase B plumbing (caching, the service, the CLI
surface) gets built on top of them. Phase B starts only after the owner
reads the numbers this harness produces.

```bash
uv run python evals/decision_revisions/run_decision_revisions_eval.py --self-test
uv run python evals/decision_revisions/run_decision_revisions_eval.py --runs 15
uv run python evals/decision_revisions/run_decision_revisions_eval.py \
    --runs 15 --model qwen3:8b --temperature 0.0 --seed 7
```

The runner drives the REAL production leaves through their own public API --
`derive_subjects`, `plan_revision_candidates`, `judge_pairs` -- never a
reimplementation of any of them. What is measured is what ships.

## The fixture is deliberately NOT AMI

`revision_fixtures.py` is hand-written, in a domain that is not the AMI
meeting corpus. This project's thesis evaluation (C2) measures against AMI
separately; if this harness's own fixture were drawn from AMI too, tuning a
prompt against numbers this harness reports would contaminate that later,
independent evaluation. Keeping the two fixtures disjoint is what keeps the
AMI evaluation honest.

**As shipped, `load_fixture()` returns T1's tiny SYNTHETIC placeholder set**
-- a handful of invented decisions, built only to give `--self-test`
something to run the real pipeline over with zero network calls. It is not
meant to measure anything about the production judge, and a live run against
it (`--runs N` with no `--self-test`) measures the placeholder, not a real
question. T2 replaces `load_fixture`'s contents with hand-written, dated
meeting notes and owner-adjudicated labels; T3 is the owner settling every
`LabelledPair.contested` case BEFORE any live run's numbers are trusted.

## Stage order

Every run: **subject pass** (once) -> **candidate generation** (once) ->
**the judge**, `--runs` times, over BOTH:

- **(a) the real candidate set** -- only pairs `plan_revision_candidates`
  actually proposed, i.e. what a live end-to-end pipeline would show the
  judge; and
- **(b) every labelled pair directly** -- including a pair the candidate
  stage never offered.

The judge is run twice per iteration on purpose: (a) alone cannot tell "the
judge is wrong" apart from "the judge was never asked", and a pair the
candidate stage misses (design's own "hard cases on purpose": paraphrased
subjects lexical overlap can miss) still deserves a judge-quality number. The
report labels every metric `(a)` or `(b)`.

Subject pass and candidate generation run exactly once per invocation, not
once per judge run: both are deterministic given one subject-pass reply per
Decision, so only the judge's own sampling varies across `--runs`.

## What it reports, and what each metric means

**Every rate is `k of n`, never a bare percentage** -- a filtered probe
hides its complement (project memory), and this harness's own acceptance bar
requires it.

1. **Subject pass**
   - *subject produced*: of every Decision, how many the subject pass
     returned a `DecisionSubject` for (a malformed reply degrades that one
     Decision to `None`, never aborts the batch).
   - *evidence kept (of produced)*: of the Decisions that produced a
     subject, how many also kept a verbatim `evidence` quote.
   - *pair overlap >= threshold (of pairs with both subjects)*: of the
     LABELLED pairs where both sides produced a subject, how many clear
     `SUBJECT_OVERLAP_THRESHOLD` (0.5) -- a prior, purely lexical number,
     computed directly from `subject_overlap`, independent of the source-
     disjointness/`resolved_with`/top-k exclusions candidate generation
     applies on top of it.
2. **Candidate-stage recall**: of the adjudicated TRUE pairs (labelled
   REVERSES/REFINES/REAFFIRMS -- UNRELATED is excluded, since the candidate
   stage proposing an unrelated pair is not itself a miss), how many
   `plan_revision_candidates` proposed at all, and which specific pairs it
   never offered. **A judge can only be as good as the candidates it is
   shown** -- this number is the ceiling on stage (a)'s numbers below it.
3. **Judge** (both stages, primarily read from (b) since it has no recall
   gap):
   - a confusion matrix, `(expected, observed)` -> count, over the fixture's
     four-value vocabulary (REVERSES/REFINES/REAFFIRMS/UNRELATED);
   - precision and recall of REVERSES and of REFINES specifically;
   - the REVERSES<->REFINES confusion, named explicitly rather than buried
     in the matrix -- REFINES read as REVERSES (or the reverse) is the
     costliest confusion this judge can make, since #S8/S9's
     `RELATION_FOR_VERDICT` would then write the wrong relation type
     (`supersedes` vs `revises`) into the bundle;
   - *actionable rate*: of every judged row, how many satisfy
     `is_actionable_revision` -- REVERSES or REFINES, confidence >= 0.7,
     both quotes verified. REAFFIRMS is never actionable by that gate's own
     contract (design.md Decision 6), so a 100% REAFFIRMS-only fixture would
     read 0% here and that is correct, not a bug.
4. **Direction accuracy**: `pair_direction`, over the fixture's OWN resolved
   dates, compared against the fixture's `expected_later_id` -- **never**
   against a judge reply (direction is never read from a model, ADR-0025).
   This is a deterministic pure function measured against deterministic
   fixture data, so it should read 100%; anything lower is a bug in this
   harness or in `pair_direction`, never a property of the model. Also
   reports how many pairs had no established direction, and why (undated,
   equal dates, multiple dates) -- via `pair_direction`'s own `reason` field.
5. **Stability**: modal-verdict share per pair, across `--runs` judgements
   of stage (b). Needs no labels.

## Pure scoring functions (no LLM, no I/O)

Every number above is computed by a small, independently importable, pure
function in `run_decision_revisions_eval.py`: `subject_pass_stats`,
`pair_overlap_stats`, `candidate_recall`, `confusion_matrix`,
`precision_recall`, `reverses_refines_confusion`, `actionable_rate`,
`pair_stability`, `direction_accuracy`. None of them touches a network or a
model -- they read only the `JudgeRow`/`RevisionCandidatePlan`/
`DecisionSubject` shapes the real pipeline (`run_pipeline`) already produced.

## `--self-test`: model-free, zero network

`--self-test` runs the REAL pipeline (`run_pipeline`) over
`revision_fixtures`'s tiny synthetic set through a `ScriptedBackend` -- a
canned-JSON `LLMBackend` that returns one scripted reply per `.chat()` call,
in call order, and raises loudly if its script runs out. This proves the
WIRING (subject pass -> candidate generation -> judge, through the real
production functions) and the SCORING MATH together, then asserts exact
expected numbers -- not just "it ran without raising."

The synthetic fixture is built to cover, on purpose:

- one pair of each of the four verdicts (REVERSES, REFINES, REAFFIRMS,
  UNRELATED);
- one true pair the candidate stage MISSES (scripted with two subjects that
  share no lexical tokens at all -- the "paraphrase" hard case);
- one pair with NO established direction (one side deliberately undated);
- one malformed judge reply (degrades to an `UNRELATED`, `malformed=True`
  row, never raises);
- one REVERSES<->REFINES confusion (one of three judged runs on the REVERSES
  pair is scripted to answer `refines` instead).

CI runs this (and every other harness's `--self-test`) via
`evals/run_self_tests.py`, against a deliberately unreachable `OLLAMA_HOST`
-- a harness that reaches for a model fails loudly there, immediately,
instead of quietly passing on a machine that happens to have Ollama running.

### The self-test can fail, and that was checked, not assumed

A self-test that always passes proves nothing about the math it claims to
guard (project memory: a test that passes first try should be distrusted
until it is shown it can fail). While developing this harness, one scoring
line was mutated -- `precision_recall`'s two denominators
(`true_positive + false_positive` / `true_positive + false_negative`) were
swapped -- and `--self-test` failed immediately and specifically:

```
FAIL REVERSES precision 2 of 2, recall 2 of 3: expected PrecisionRecall(precision=(2, 2), recall=(2, 3)), got PrecisionRecall(precision=(2, 3), recall=(2, 2))
```

The mutation was then reverted by the exact inverse edit (never
`git checkout --`, which would have discarded every other change in the
file), `__pycache__` was purged, and the self-test was re-run to confirm
green again before anything was committed.

## Live runs

A live run (no `--self-test`) builds a real `OllamaClient` --
`--model`/`--temperature`/`--seed` pin its sampling, and it runs with
production's own `DEFAULT_MAX_GENERATION_TOKENS`/`DEFAULT_CONTEXT_WINDOW`
(never the client's unbounded defaults -- see `evals/contradictions/README.md`
for why that distinction matters). It writes two files under `results/`:

- `decision-revisions-<stamp>-<model>.md` -- the human-readable report, every
  metric above, `k of n` beside every rate.
- `runs-<stamp>-<model>.json` -- every raw per-run verdict for both stage (a)
  and stage (b), so the numbers can be RESCORED later (a prompt-wording
  question, a bug in a scoring function) without spending another Ollama
  call.

`--runs` defaults to 15 -- the repo's own measured floor for judged-pair
stability (`evals/contradictions/README.md`: "five runs is not enough on
this metric, and that is the finding" -- 5 runs could not tell two MODELS
apart on a comparable judge). Never compare two runs of this harness measured
at different `--runs` counts, on different fixture contents, or under
different client settings (mirrors `evals/contradictions/README.md`'s own
warning) -- and never compare a run against T1's synthetic placeholder to a
run against T2's real fixture; they are not the same measurement.

## Tool-agnostic by construction

This harness names no personal AI-agent tooling, memory system, or IDE. It
runs the same way for any contributor with `uv` and this repository, exactly
as `AGENTS.md`'s "Do not" section requires.
