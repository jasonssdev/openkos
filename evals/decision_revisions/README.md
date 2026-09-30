# `decision_revisions` — measuring the decision-revision-detector's Phase A leaves (#1014)

Issue #1014's own order was to measure the Phase A leaves --
`resolution/decision_subject.py`'s subject pass, and
`resolution/decision_revision.py`'s direction rule, candidate generation and
judge -- **before** any Phase B plumbing (caching, the service, the CLI
surface) was built on top of them. This harness gated that step: Phase B
started only after the owner read its numbers, and has since shipped
(`application/revisions.py`, `state/revision_findings.py`, the `revisions`
verb).

```bash
uv run python evals/decision_revisions/run_decision_revisions_eval.py --self-test
uv run python evals/decision_revisions/run_decision_revisions_eval.py --print-fixture
uv run python evals/decision_revisions/run_decision_revisions_eval.py --runs 15
uv run python evals/decision_revisions/run_decision_revisions_eval.py \
    --runs 15 --model qwen3:8b --temperature 0.0 --seed 7
uv run python evals/decision_revisions/run_decision_revisions_eval.py \
    --runs 15 --vector-source reindex
uv run python evals/decision_revisions/run_decision_revisions_eval.py \
    --rescore evals/decision_revisions/results/runs-20260928T103525Z-qwen3-8b.json
```

The runner drives the REAL production leaves through their own public API --
`derive_subjects`, `plan_revision_candidates`, `judge_pairs` -- never a
reimplementation of any of them. What is measured is what ships.

## The fixture is deliberately NOT AMI

`revision_fixture_library.py` is hand-written, in a domain that is not the
AMI meeting corpus. This project's thesis evaluation (C2) measures against AMI
separately; if this harness's own fixture were drawn from AMI too, tuning a
prompt against numbers this harness reports would contaminate that later,
independent evaluation. Keeping the two fixtures disjoint is what keeps the
AMI evaluation honest.

Two fixtures live here, and they are never mixed:

- **`revision_fixture_library.py`** (`load_library_fixture()`) is the REAL
  fixture a live run measures: hand-written meeting notes from a volunteer
  committee running a small community library -- 8 sources (two committee
  meetings and a volunteer huddle share one date, two sources are undated,
  one Decision cites two meetings with different dates), 42 Decisions, 46
  labelled pairs over all four verdicts -- this is the `"original"` split
  (see "The confirmation split" below for the 12 pairs added afterward).
  Its hard cases are tagged in
  `LabelledPair.hard_case` (the tag vocabulary is in the module docstring),
  and more than half the pairs are UNRELATED hard negatives on purpose, so a
  judge that answers one verdict for everything scores badly instead of
  well.
- **`revision_fixtures.py`** (`load_fixture()`) keeps T1's tiny SYNTHETIC
  placeholder. `--self-test` pins exact numbers against it and never runs
  over the real fixture's model-dependent stages.

**The real fixture's labels are owner-adjudicated** (T3, 2026-09-27: all 16 contested labels accepted as proposed). Every
pair where a careful reader could reasonably pick a different verdict is
flagged `contested` with a one-line note. T3 is the owner settling every
contested pair BEFORE any live run's numbers are trusted. To review them in
one file:

```bash
uv run python evals/decision_revisions/run_decision_revisions_eval.py --print-fixture
```

prints every labelled pair (both Decisions, their resolved dates, the
expected verdict and later side, the note and the hard-case tag), contested
pairs first. `fixture-adjudication.md` is that output, committed; after
changing a label, regenerate it with the same command.

`expected_later_id` follows only from the sources' dates, never from the
narrative. `--self-test` enforces that, and the rest of the fixture's
structure, with a pure `fixture_integrity` check over both fixtures: every
cited source and paired Decision exists, no pair repeats, two sides share a
source only when the pair is tagged `shared-source`, `expected_later_id`
equals `pair_direction`'s holder over the fixture's own dates, and an
`undated`/`equal-date`/`multi-date` tag has exactly that no-direction
reason. It was checked that this can fail: renaming one pair's reference to
a nonexistent Decision, and separately pointing one pair's
`expected_later_id` at its earlier side, each turned `--self-test` red with
a specific message; both were reverted by the inverse edit.

## The confirmation split

`LabelledPair.split` (`"original"` or `"confirmation"`) marks a second
measurement inside `revision_fixture_library.py`, added by #1014's judge
prompt fix (task T1) to re-measure that fix honestly. The first live run's
diagnosis read all 46 `"original"` pairs to find its two failure patterns
(a refinement with an absolute/exclusive qualifier narrowed or extended,
misread as a reversal; two different subjects sharing a lure word, misread
as related); re-measuring the fix against only those same 46 pairs would
inflate the result, since the prompt could simply be fit to what the
diagnosis already saw.

The confirmation pairs are ~12 NEW Decisions, written in the same
community-library domain, with fresh vocabulary, BEFORE the prompt changes
-- and they never reuse the diagnosis's own lure phrases ("at all times",
"only", "in general", "children's", "dropped", "stays"). Every confirmation
pair is `contested=False`: each one is meant to be unambiguous to a careful
reader (`fixture_integrity` enforces this, and flags a confirmation pair
that is contested), so a doubtful call from the original 46 can never leak
into what the fix is measured against. They cover the same hard cases the
fix targets, plus reversal and reaffirm/refine controls -- see
`revision_fixture_library.py`'s own "Confirmation split" docstring section
for the exact mix and every pair's rationale.

**`original` and `confirmation` are never blended.** Every judge metric
this harness reports -- REVERSES/REFINES precision and recall, the
REVERSES<->REFINES confusion, the confusion matrix, actionable rate, and
stability -- is computed once per split, plus a separately-labelled `all`,
never as one pooled number that hides which split it came from
(`rows_for_split`/`pairs_for_split`). Candidate-stage recall and direction
accuracy report the same three ways, since filtering the fixture's own
pairs by `split` is trivial once judged rows carry it. Each stored
`runs-*.json` row also carries its own `split`, so a past run can be
rescored by split later without spending another Ollama call. The bars
B1-B8 below read the **original** split, exactly as they did before this
split existed -- adding the confirmation split does not move them.

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
candidate stage misses (design's own "hard cases on purpose": a paraphrase
whose embeddings still fall short of `EMBEDDING_SIMILARITY_THRESHOLD`) still
deserves a judge-quality number. The report labels every metric `(a)` or
`(b)`.

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
     `SUBJECT_OVERLAP_THRESHOLD` (0.5) -- a purely lexical number, computed
     directly from `subject_overlap`. This is a DIAGNOSTIC only (#1014
     sub-change 3): candidate generation itself no longer blocks on it --
     `plan_revision_candidates` blocks by embedding cosine similarity
     (`EMBEDDING_SIMILARITY_THRESHOLD`) -- so this metric is independent of,
     and no longer feeds, the source-disjointness/`resolved_with`/top-k
     exclusions candidate generation applies.
2. **Candidate-stage recall**: of the adjudicated TRUE pairs (labelled
   REVERSES/REFINES/REAFFIRMS -- UNRELATED is excluded, since the candidate
   stage proposing an unrelated pair is not itself a miss), how many
   `plan_revision_candidates` proposed at all, and which specific pairs it
   never offered. **A judge can only be as good as the candidates it is
   shown** -- this number is the ceiling on stage (a)'s numbers below it.
3. **Judge** (both stages, primarily read from (b) since it has no recall
   gap). Stage (b)'s own metrics below are rendered three times each --
   `original`, `confirmation`, `all` -- never pooled into one number ("The
   confirmation split" above), and EACH of those three is itself rendered as
   three blocks: the blended numbers below (unchanged), the same rows
   restricted to directed pairs, and the undirected pairs scored
   change/no-change ("Undirected pairs are scored change/no-change,
   directed pairs unchanged" below):
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
     contract ([`design.md`](../../openspec/changes/archive/2026-09-29-decision-revision-detector/design.md) Decision 6), so a 100% REAFFIRMS-only fixture would
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

The untyped-undirected rule (above) adds three more, same posture:
`directed_rows`/`undirected_rows` (filter `JudgeRow`s by `.directed`) and
`change_precision_recall` (the undirected change/no-change scorer).
`rows_from_stored_json` is the `--rescore` counterpart to `judge_rows`,
rebuilding the same `JudgeRow` shape from a stored run's raw JSON instead
of a live `judge_pairs` batch.

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
- one true pair the candidate stage MISSES (scripted with a `_FakeEmbedder`
  vector pair whose cosine similarity sits below
  `EMBEDDING_SIMILARITY_THRESHOLD` -- the "paraphrase" hard case);
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

## `--vector-source {text,reindex}`: which shape the embedder measures

Phase B's re-plan (`openspec/changes/archive/2026-09-29-decision-revision-detector/design.md` Decision B5, "Keep 0.65, cite the
production-shape measurement, and make that measurement reproducible") adds
a second arm, so `EMBEDDING_SIMILARITY_THRESHOLD`'s docstring can cite a
committed, re-runnable measurement instead of an un-reproducible scratchpad
probe:

- **`text`** (default, unchanged): `embed_text` composes `title\n\nbody`
  directly and hands the WHOLE fixture to `embedder.embed()` in one batched
  call -- SHORT text, not a full OKF document. This is the shape the
  original offline calibration probe used.
- **`reindex`**: writes every fixture Decision as a real OKF `Decision`
  concept into a throw-away temporary bundle (`okf.dump_frontmatter`, never
  an f-string), runs the REAL `state.reindex.reindex` over it with the
  configured embedder, and reads the resulting vectors back through
  `VectorStoreDB.document_vectors` (Phase B, slice P1) -- the exact seam
  `application/revisions.py` uses in production (slice P5a). This measures
  the title+description+tags header plus CHUNKED body shape
  `EMBED_COMPOSITION_TAG` records, not the harness's own short string.

`embed_via_reindex` raises `VectorSourceMismatch` (never silently returns
fewer vectors) if the read-back count does not equal the number of
Decisions written -- a lost chunk must fail loudly, not score as a real
0/N recall miss. `--self-test` exercises both the happy path and this
checked-mismatch path with a model-free `_ReindexFakeEmbedder`, so the
poisoned-`OLLAMA_HOST` self-test sweep (`evals/run_self_tests.py`) stays
honest for this arm too.

Per Decision B5, the one committed live run `EMBEDDING_SIMILARITY_THRESHOLD`'s
docstring is meant to cite uses `--vector-source reindex`. That operator step
(requires a running local Ollama) has not been run as of this slice; the
docstring still cites only the title+body offline probe until it is.

## Pass/fail bars (stated before the first live run)

Committed before any live number was seen, so a result cannot move them.
All bars read stage **(b)** -- every labelled pair judged directly, pooled
over every run -- except B1 and B2, which are not model judgements. They
read the **`original`** split ("The confirmation split" above): the
confirmation pairs did not exist when these bars were set, and adding them
does not move any of the eight.

| Bar | Metric | Pass when | Why this level |
|---|---|---|---|
| B1 | Direction accuracy | 46 of 46 | Deterministic; anything lower is a bug, not a model property |
| B2 | Candidate-stage recall | >= 18 of 24 true pairs | The judge's ceiling; two `paraphrase` pairs are expected misses by design, so 75% leaves room for them and little else |
| B3 | REVERSES precision | >= 0.80 | A false REVERSES writes `supersedes` and hides a decision that is still current -- the costliest error |
| B4 | REVERSES recall | >= 0.60 | A missed reversal leaves a stale decision looking current, but the human still sees both |
| B5 | REFINES precision | >= 0.60 | A false REFINES writes `revises`, which hides nothing |
| B6 | REFINES recall | >= 0.50 | Refinements are the subtlest class; half is the floor for the verb to be worth running |
| B7 | REVERSES<->REFINES confusion | <= 10% of rows expected REVERSES or REFINES | The wrong relation type in the bundle; named separately because B3-B6 can pass while this one fails |
| B8 | Mean modal-verdict share | >= 0.80 | A judge that changes its answer run to run cannot back a durable finding |

The first live run uses production sampling: `--temperature` and `--seed`
left unset, exactly as a workspace without the #1013 keys runs. Pinning
both would make every run identical and B8 would measure nothing.

**Reading the result.** All eight pass: Phase B may start. Any bar fails:
the result is "no" for that axis, and the owner decides whether Phase B
waits for a fix (prompt, threshold, candidate stage) or proceeds with the
gap named. The thresholds `EMBEDDING_SIMILARITY_THRESHOLD` (0.65, the
production candidate blocking rule as of #1014 sub-change 3),
`SUBJECT_OVERLAP_THRESHOLD` (0.5, now a diagnostic only) and
`_ACTIONABLE_CONFIDENCE` (0.7) are revisited with these numbers before S6,
as [`design.md`](../../openspec/changes/archive/2026-09-29-decision-revision-detector/design.md) already requires.

## First live run against the bars

`results/decision-revisions-20260928T033104Z-qwen3-8b.md` (raw verdicts in
the matching `runs-*.json`): qwen3:8b, 15 runs, production sampling.
**Three of eight bars fail**, so the answer is "no" on those axes.

| Bar | Result | Verdict |
|---|---|---|
| B1 direction | 46 of 46 | pass |
| B2 candidate recall | 15 of 24 (0.62) | **fail** (needs 18) |
| B3 REVERSES precision | 121 of 173 (0.70) | **fail** (needs 0.80) |
| B4 REVERSES recall | 121 of 165 (0.73) | pass |
| B5 REFINES precision | 68 of 84 (0.81) | pass |
| B6 REFINES recall | 68 of 120 (0.57) | pass |
| B7 REVERSES<->REFINES confusion | 53 of 285 (0.19) | **fail** (needs <= 0.10) |
| B8 stability | 0.99 | pass |

Where the failures come from, read from the confusion matrix:

- **B3 and B7 share a cause: REFINES read as REVERSES** (37 of 120 REFINES
  rows). Those are 37 of the 52 false REVERSES; the other 15 are UNRELATED
  pairs. Each would write `supersedes` and hide a decision still in force.
- **B2**: 9 true pairs are never proposed -- 2 are the expected `paraphrase`
  misses, the other 7 are real candidate-stage gaps (chains through
  `no-charges-for-late-returns` and the Saturday thread account for 5).

## The undirected rule: an undirected REVERSES/REFINES is an untyped change

Root-caused after the prompt-fix runs above (qwen3:8b baseline, its rejected
variant, and qwen3:14b): every run's failures concentrate on pairs with NO
established direction (undated / equal / multiple dates). On the qwen3:8b
baseline, directed pairs judge at 89% correct; undirected pairs at 56%.
**Undirected REFINES is never answered at all -- 0 of 30** -- because REFINES
is directional by the judge prompt's own definition ("keeps the first's
choice but narrows, extends, or conditions it"), so a judge asked to apply
that definition with no established order has nothing to narrow relative to.

The owner's decision (2026-09-28, maximum automation, imperfect is fine, no
new human step): **an undirected REVERSES or REFINES verdict is an untyped
CHANGE.** The engine (`resolution/decision_revision.py`'s
`RevisionVerdict.is_untyped_change` / `relation_for`) never infers a relation
type from it. Detection stays fully automatic; the person picks both the
later side and the relation type together, in one combined keystroke, during
`reconcile`'s per-item walk (`openspec/changes/archive/2026-09-29-decision-revision-detector/design.md`
step 7 is updated to describe that prompt). The judge PROMPT itself is
UNCHANGED by this decision -- it is a scoring-and-consumption rule over the
judge's existing four-value vocabulary, not a fifth verdict and not a new
model call, which is exactly what lets every stored `runs-*.json` be
rescored for free (`--rescore` below).

**This rule was motivated by a diagnosis made AFTER seeing the data** (the
subgroup breakdown above), not stated in advance like bars B1-B8. Those
eight bars were fixed before any live run and are **never moved** by this
rule -- the "Rescored under the untyped-undirected rule" section below
reports the same eight thresholds, only recomputed with directed and
undirected rows scored differently, so the comparison stays honest about
what changed and what did not.

## Undirected pairs are scored change/no-change, directed pairs unchanged

Every judge-stage report (live run or `--rescore`) now shows THREE blocks per
split (`original`, `confirmation`, `all`), never replacing one with another:

1. The existing BLENDED four-way numbers (`#1014` task T1's own split
   reporting) -- unchanged, so the old view stays visible.
2. The same rows restricted to **directed** pairs only (`directed_rows`) --
   still the ordinary four-way REVERSES/REFINES/REAFFIRMS/UNRELATED scoring.
3. The **undirected** pairs only (`undirected_rows`), scored as a binary
   change/no-change classifier (`change_precision_recall`): expected
   REVERSES or REFINES collapses to CHANGE; observed `reverses` or
   `refines` collapses to change. Precision and recall of CHANGE are
   reported `k of n`, exactly as every other rate in this harness.

`JudgeRow.directed` (`True`/`False`/`None` for an unlabelled stage (a)
candidate row) is read from the fixture's own `LabelledPair.expected_later_id`
(non-`None` iff direction is known) -- never recomputed from a judged
verdict, and never stored in `runs-*.json`, so it is always derived fresh
against whichever fixture is loaded.

## `--rescore`: recomputing a report with zero model calls

```bash
uv run python evals/decision_revisions/run_decision_revisions_eval.py \
    --rescore evals/decision_revisions/results/runs-20260928T103525Z-qwen3-8b.json
```

Rebuilds every judge-stage, candidate-recall, and direction-accuracy metric
from a stored `runs-*.json`'s raw `rows_a`/`rows_b` -- zero Ollama calls,
zero re-judging. `pair_ids` are re-matched against the CURRENT
`load_library_fixture()` (`rows_from_stored_json`), so `expected`, `split`,
and `directed` are always freshly derived from the fixture, never trusted
from the stored JSON -- an older run's JSON carries no `split` key at all
(it predates task T1) and none ever carries a `directed` key (it predates
this rule); only what the model actually produced
(`observed`/`confidence`/`quote_0`/`quote_1`/`malformed`) is read back.
Candidate recall is recomputed from the stored `candidates` list; direction
accuracy is deterministic and reads only the fixture's own dates, never the
stored run. **The subject-pass section cannot be rescored** -- its raw
per-Decision replies are never persisted in `runs-*.json` -- so a rescored
report omits it rather than fabricating it.

Writes `decision-revisions-<stamp>-<model>-rescored.md` next to the input
file, `<stamp>`/`<model>` read from the JSON's own `generated_at`/`model`
fields, and prints the report.

## Rescored under the untyped-undirected rule

The three stored runs from the prompt-fix task (T3/T5, above) were rescored.
`runs-20260928T033104Z-qwen3-8b.json` (the very first live run, predating
the confirmation split) was tried too: its rows still resolve cleanly
against the current fixture (the confirmation pairs are additive, so an
older run's `pair_ids` are all still present), so it rescores without error
-- it is not committed as a fourth report here, since it belongs to an
earlier task's own delivery, not this one.

**Baseline, `runs-20260928T103525Z-qwen3-8b.json`** (qwen3:8b, base prompt,
original split, B1-B8 thresholds UNCHANGED):

| Bar | Metric | Result | Verdict |
|---|---|---|---|
| B1 | Direction accuracy | 46 of 46 | pass |
| B2 | Candidate-stage recall | 14 of 24 | fail |
| B3 | REVERSES precision (directed) | 94 of 117 (0.80) | pass |
| B4 | REVERSES recall (directed) | 94 of 120 (0.78) | pass |
| B5 | REFINES precision (all rows, unaffected) | 67 of 81 (0.83) | pass |
| B6 | REFINES recall (directed) | 67 of 90 (0.74) | pass |
| B7 | REVERSES<->REFINES confusion (directed) | 22 of 210 (0.10) | fail |
| B8 | Mean modal-verdict share | 0.98 | pass |

Read against the ORIGINAL (blended) numbers this same run reported (B3 0.70,
B4 0.73, B6 0.57, B7 0.19, all fail or borderline): **B3, B4 and B6 pass
once undirected rows are removed from the REVERSES/REFINES bars**, and B7
moves from a clear fail (0.19) to a near-miss (0.10, still technically not
`<=`) -- confirming the diagnosis: the failures the prompt-fix task chased
were concentrated in the undirected subgroup, not evenly spread across the
whole judge. B2 (candidate recall) and B8 (stability) are unaffected by
this rule and unchanged. This does **not** flip the prompt-fix decision
(that decision was already settled on other evidence, in the section
above) -- it only shows what the ORIGINAL fixture's numbers were actually
telling us.

**The other two stored runs, in one line each:**

- `runs-20260928T121032Z-qwen3-8b.json` (the REJECTED prompt variant):
  directed B3 90/105 (0.86), B4 90/120 (0.75), B6 75/90 (0.83), B7 15/210
  (0.07) -- all four pass under the new rule too, but this run stays
  rejected: it was rejected for the guest-wifi-access regression on the
  original split, a defect this rescoring does not touch.
- `runs-20260928T145434Z-qwen3-14b.json` (qwen3:14b, base prompt): directed
  B3 90/112 (0.80) pass, B4 90/120 (0.75) pass, B5 60/105 (0.57) **fail**,
  B6 60/90 (0.67) pass, B7 45/210 (0.21) **fail** -- worse than qwen3:8b on
  REFINES precision and confusion even under the new rule, consistent with
  the task doc's "capacity is not the lever" conclusion.

## Adopted judge prompt: the `reverses`/`refines` clauses

A/B on qwen3:8b, 15 runs, production sampling, same fixture. Baseline
`runs-20260928T103525Z` (prompt `6de49030`) against treatment
`runs-20260928T173355Z` (prompt `d8238af6`); original split, untyped-undirected
rule:

| Metric | Baseline | Treatment |
|---|---|---|
| B3 REVERSES precision (directed) | 94 of 117 (0.80) | 87 of 94 (0.93) |
| B4 REVERSES recall (directed) | 94 of 120 (0.78) | 87 of 120 (0.73) |
| B6 REFINES recall (directed) | 67 of 90 (0.74) | 75 of 90 (0.83) |
| B7 confusion (directed) | 22 of 210 (0.105), fail | 18 of 210 (0.086), pass |
| Undirected change precision | 60 of 60 | 52 of 52 |
| Undirected change recall | 60 of 75 (0.80) | 52 of 75 (0.69) |
| Confirmation split | one pair wrong (15 rows) | every metric 1.00 |

The adoption rule stated before this run required undirected change recall
not to drop, and it dropped on one pair (`guest-wifi-access` /
`guest-wifi-password-rotation`: 15 of 15 detected, now 7 of 15). **The owner
adopted the prompt anyway**, trading a missed finding (nothing written, the
decision stays visible) for fewer false REVERSES (which would hide a decision
still in force). Every judge bar passes on directed pairs; B2 (candidate
recall) remains the open failure.

An earlier variant that also added an `unrelated` clause (prompt `5223c59f`,
`runs-20260928T121032Z`) was rejected: it lost the same undirected pair and
did not fix the shared-word pattern.

## Tool-agnostic by construction

This harness names no personal AI-agent tooling, memory system, or IDE. It
runs the same way for any contributor with `uv` and this repository, exactly
as `AGENTS.md`'s "Do not" section requires.
