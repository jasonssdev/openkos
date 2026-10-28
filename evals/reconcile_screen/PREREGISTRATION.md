# Pre-registration: the merged-body reconciliation model screen (#1563)

**Status: REGISTERED when this file merges to `main`, which happens before any scored model
call (#1556: pre-register, and merge the pre-registration, before any run).** The labels are
adjudicated (see "Third adjudication"), the owner's questions are decided (see "Decisions the
owner made"), the J bar is recomputed and Stage 0 is done. Everything below binds from the
merge; a change after it is a new registration, not an edit to this one. No scored call has
been made: Stage 0 called only calibration inputs that are not fixture cases. A measurement: it
adopts nothing and changes no production code, default, prompt or task key. Part of #1556; refs
#1563, #1557.

## Before this could be registered

1. **Done, with two deviations (below).** The 121 claims were labelled blind a second
   time with the sheet from `run_reconcile_screen.py blind-sheet`, by a Claude model rather
   than the owner, and the 2 disagreements were resolved by a third model (Claude Opus) on
   the owner's delegation, recorded in `fixture.json` (`adjudication` with `first`,
   `second`, `resolved`, `reason`; the first label stays as `alt_label`), each re-anchored
   with literals that pass `integrity_problems` (none retired), and listed in
   [`ADJUDICATION.md`](ADJUDICATION.md).
2. **Done** for the floors and the class balance (`forecast`, below) and for the J bar
   (`j-table`, below), both on the adjudicated labels; the unit test
   `test_the_j_bar_on_the_real_reference_is_pinned` pins that four outright wins clear J
   and three do not.
3. **Done.** The five questions are decided (owner, 2026-10-27; see "Decisions the owner
   made").
4. **Done.** Stage 0 (below) measured the latencies and recorded the model digests; the
   forecast is recomputed from them and is within the budget.

## Second labelling: recorded, with a deviation (read first)

**The owner did not label this sheet.** The owner delegated the blind labelling, and a
Claude model labelled the sheet blind (it read only the blind sheet, none of
`fixture.json`, the README, this draft or any other label). Agreement below is therefore
measured against a **Claude labeller, not the owner**, so it is a statement about two
readings of the same rubric, not about the owner's judgment. The owner's own review of
every disagreement, and every resolution, was not performed before registration (the
owner delegated it, and it is not one of the preconditions above); the owner can still
override a resolution, but only as a recorded amendment before any scored call. The labels were imported with
`run_reconcile_screen.py import-sheet` into [`second_labels.json`](second_labels.json) (labels keyed by
fixture id, plus the labeller and seed). The filled sheet itself is not committed, per
the README.

**Result** (`adjudicate`): 119 of 121 claims agree (0.983), Cohen's kappa 0.975, 2
disagreements: `protocol-seams-04` (first `A`, Claude `AB`) and `stoicism-01` (first `AB`,
Claude `A`). Each moves a claim between one-sided and shared, so neither can be re-scored
until it has a `once` literal (or is retired), per the README.

**What this did not do.** It resolved nothing; the third adjudication below did.

## Third adjudication: recorded, with a deviation (read first)

**Deviation: the disagreements were adjudicated by a third model (Claude Opus) on the
owner's delegation, blind to annotator identity**, not by the owner. For each claim the
adjudicator read only the two notes, the sheet's rule and the two candidate labels in sorted
order, without knowing which annotator gave which and without reading any annotator's
notes, any stored model run, result or score; its verdicts were written down before the
sources were revealed. Both resolved to the second label: `protocol-seams-04` is `both`
(Note B's "The Protocol seams" states it) and `stoicism-01` is `a_only` (Note B touches the
Enchiridion without saying the dichotomy comes from it). Each claim was re-anchored
(`protocol-seams-04`: anchor and `once` `Protocol`; `stoicism-01`: anchor `*Enchiridion* 1`,
no `once`), so both are scored and neither is retired. No registered margin or decision
rule changed. The owner may override either resolution at registration.

## What the labels are (read before any number)

**Adjudicated, one author's first.** The first author chose the claims and labelled which
note states each, flagging 2 as arguable; the two adjudicated claims are flagged too (4). The volatility screen's second adjudication found its first author
had anchored on the registry default and moved that floor from 0.778 to 0.481. The
equivalent hazard here is **over-calling `both`**: a claim the first author reads as stated
in both notes is one the model must state exactly once, and a reader who sees only one note
stating it turns it into a one-sided claim and removes the dedupe test from it. Of the 33
`both` claims, 2 are flagged; the rest are shared facts with a literal both notes carry.

**The floors (the adjudicated labels, strict, no model; `forecast`).** The first author's
labels gave `sentence_union` 93 / 0.770, `stack` 0.729, `longer_only` 0.672 and
`absorbed_only` 0.654.

| baseline | claims kept | mean case score |
| --- | --- | --- |
| `sentence_union` (the reference for J) | 94 / 121 | **0.777** |
| `stack` (what production keeps on a refusal) | 88 / 121 | 0.728 |
| `longer_only` | 81 / 121 | 0.673 |
| `absorbed_only` | 79 / 121 | 0.655 |
| `survivor_only` | 75 / 121 | 0.617 |

Class balance: 42 only in NOTE A, 46 only in NOTE B, 33 in both (unchanged: the two
adjudicated claims swap sides). The strongest baseline gets 27 claims wrong (28 on the
first author's labels); 13 of 15 cases are merged perfectly by no baseline (14); no claim is wrong
under every baseline (0), because each kind is kept by one trivial predictor.

**What the J bar is.** The reference is wrong in 27 claims and scores below 1.0 in 13
cases (28 and 14 on the first author's labels). With the harness's own bootstrap over
cases (10,000 resamples, seed 42), on the adjudicated labels (`run_reconcile_screen.py
j-table`, no model; `reconcile_core.j_tolerance` and `constant_gain_bound`):

- a model that matches the reference on every case and is better by a constant **+0.05**
  on every case (a case score capped at 1.0, so the 2 cases already perfect gain nothing)
  clears J: point 0.043, 95% lower bound 0.033 (13 of 15 cases can gain); **+0.10** gives
  point 0.087, lower bound 0.067;
- a model that matches the reference everywhere except winning *k* cases outright (the
  reference's lowest cases, to 1.0) clears J from *k* = 4 (win 3 does not;
  `test_the_j_bar_on_the_real_reference_is_pinned` pins both). It tolerates **no** outright
  loss (the reference's highest cases, to 0.0) at any *k* from 4 to 13 (every case the
  reference scores below 1.0), because one such loss is a delta of -1.0 against gains of at
  most 0.44 a case. Partial losses are different: a model reads on its gains and losses per
  case, not on its mean alone.

Stated before data so a J pass is not over-read: **a pass shows a model keeps more of what
the notes say, and says shared facts once, better than a sentence-level union with no model
does. It does not show the merged document reads well**, which this screen does not score.

**Label uncertainty.** The report gives strict and lenient accuracy for every model, and
`strict (1st)`, the same answers scored against the first author's labels, as a sensitivity
analysis. **The decision uses strict accuracy on the resolved labels.** Lenient and the
first-author sensitivity are reported and never decide. A model whose verdict differs
between the two is recorded as label-dependent, not selected on the better one.

## Question

Is a model call for **merged-body reconciliation justified**: does any local model produce a
merged body that keeps more of the two notes' claims, states shared ones once and invents
nothing, than a no-model sentence union does, on a fixture whose labels have been checked by
a second person? Secondarily: does the task need a different model than the global
`qwen3:8b`, and does a better merge come with more invented figures?

## Items, arms, runs

- **Items:** the 15 cases in `fixture.json` as committed at the registration commit (12 from
  repository documents, 3 from the `good-life-demo` bundle; 121 claims).
- **Arm:** `default`, the shipped prompt, one chat call per case and run through production's
  `reconcile_merged_body`. There is no second arm.
- **Runs:** n = 5 seeds (1 to 5) per (model, case), the `OllamaClient` seed. Five shows
  stability, not power (the fixture, not the run count, limits power; `evals/edge_typing/`
  takes n = 15 as its floor after a ranking reversed at n = 3).
- **Models:** baseline `qwen3:8b` (the global default, which is what `merge` uses today).
  Candidates, the other #1556 screens' list: `gemma4:26b-a4b`, `qwen3:14b`, `qwen3.6:27b`,
  `qwen3.6:35b-a3b`, plus `gemma2:27b` (installed). Judge-only roles are not a separate
  candidate class.
- **Scoring of failures:** a backend error, a timeout or a refused reply scores as the
  stacked body, the outcome production gives the user in that case.
- Model digests: the table under "Forecast and budget" (recorded by Stage 0; the harness
  also stamps the full digest in-band on every run, and a run whose stamped digest differs
  from the table is void).

## Metrics

Computed by `reconcile_core.summarize` / `run_reconcile_screen.py score`:

1. **Strict accuracy** (primary, deciding): the mean over cases of each case's mean over runs
   of the share of its claims kept (anchors survive; a shared claim exactly once).
2. **Lenient accuracy** (reported): also accepts a flagged claim in any number.
3. **First-author sensitivity** (reported): the same bodies against the first author's kinds.
4. **Retention** (claims whose anchors survive, over bodies the model produced), by kind;
   **dedupe rate** (shared claims kept that are stated once); **fabrication rate** (bodies
   with at least one digit-bearing word or code span in neither note nor the title); failure
   rate by kind (`backend`, `timeout`, `refused`); median and p95 latency; stability (cases
   whose score is identical across runs).
5. The floors above, recomputed from the resolved labels.

## Decision rule

The deciding statistic is a paired difference of per-case mean strict scores, against the
resolved labels, with a 95% percentile bootstrap over cases (10,000 resamples, seed 42).

- **J (justified):** a model clears J iff its paired gain over the **reference baseline** has a
  95% lower bound above 0. The reference is the model-free predictor with the highest strict
  score on the resolved labels, fixed from the labels alone before any run (`sentence_union`
  on the first author's). If no model clears J the verdict is "a model call is not justified
  on this fixture", and the product stays as it is: `merge` keeps asking the global model and
  keeps the stack on a refusal.
- **Vetoes** (any one drops a candidate): V1 failure rate (backend, timeout or refused) <=
  baseline + 0.02 and <= 0.10 absolute; V2 median call latency <= 2.0x the baseline's; V3
  fabrication rate <= baseline + 0.10.
- **Selection:** a candidate replaces `qwen3:8b` for this task only if its paired gain over the
  baseline has a point estimate above 0 and a 95% lower bound above 0, no veto trips, **and** it
  clears J. Otherwise the baseline stays and the result is recorded as it is, with no retry
  under tuned bars (#1269 rule 4).
- **Confirmation:** a selected model is rerun with seeds 11 to 15 against the baseline under
  the same rule; "confirmed" means selection holds again.
- **What a pass licenses.** Not a config default and not a `reconcile` task key (the key set is
  closed by #508's rule that a default must be justified on a fixture). A pass licenses one
  confirmation run on notes that ingest actually produced, because this fixture's notes are
  repository documents. Adoption is a separate decision this screen does not make.

## Forecast and budget (Ollama wall-clock; MEASURED by Stage 0)

Calls: 15 cases x 1 arm x 5 runs = **75 per model; 450 for six models.**

**Stage 0 (run 2026-10-27 with `run_reconcile_screen.py stage0`; no scored case was
called).** Per model, in order: one warm-up call (this is the model load, and is in no
statistic below), three timed calls on a small calibration case, and one timed call on a large
one, all seed 1, timeout 240 s, server defaults, one model at a time and the model unloaded
before the next. The calibration cases are in no way fixture cases: two notes about sourdough
baking, which no fixture case is about (`stage0-small`, 267 characters of notes; the smallest
scored case is 721) and (`stage0-large`, 2,151 characters; the largest scored case is 2,458).
`--self-test` pins that neither shares an id or a note with a fixture case. No call failed and
none was refused. n = 4 timed calls per model, so the p95 (nearest rank) is the large call; read
it as the cost of the largest case, not as a tail estimate.

| model | digest (sha256, as `/api/tags` reports it) | warm-up (load) s | small median s (n=3) | large-case s (n=1) | median s (n=4) | p95 s (n=4) | forecast min |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `qwen3:8b` | `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41` | 6.2 | 1.58 | 12.30 | 1.58 | 12.30 | 8.5 |
| `gemma4:26b-a4b` | `001e5dafc3c77684c2307ebc6ab8e336e10c9b18eca52acf547d72fc83c3ca8c` | 16.3 | 1.18 | 9.76 | 1.19 | 9.76 | 6.9 |
| `qwen3:14b` | `bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8` | 11.1 | 2.64 | 24.02 | 2.64 | 24.02 | 16.3 |
| `qwen3.6:27b` | `bcbdbd4bcf86f2c4a56cd1698fb31f9434f7dd99752f71b636c750aaa67a49a5` | 24.1 | 6.40 | 53.35 | 6.41 | 53.35 | 36.5 |
| `qwen3.6:35b-a3b` | `a7eb95c53bcf96b4bdd008d0fab4a5dac88047d9c1a7a9ab88ed453423fbd87c` | 21.9 | 1.09 | 10.73 | 1.09 | 10.73 | 7.5 |
| `gemma2:27b` | `53261bc9c192c1cb5fcc898dd3aa15da093f5ab6f08e17e48cf838bb1c58abfe` | 21.4 | 4.70 | 34.57 | 4.70 | 34.57 | 24.1 |

**How the forecast is built.** A reconciliation reply is a rewritten document, so latency
tracks the notes' size: the small case is below every scored case, so the median of the four
calls (which sits on the small ones) would understate the screen several-fold. Each case's
latency is interpolated linearly in its two notes' combined character count between the small
and the large measurement (never below the small one), summed over the 15 cases, times 5
runs, plus the model's measured warm-up in place of the assumed 60 s load. Two points are a thin
basis for a line; this is an estimate, the scored runs will report their own medians, and the
forecast carries no claim beyond the budget check below.

| model | mean s per call (interpolated) | forecast |
| --- | --- | --- |
| `qwen3:8b` | 6.71 | 8.5 min |
| `gemma4:26b-a4b` | 5.29 | 6.9 min |
| `qwen3:14b` | 12.88 | 16.3 min |
| `qwen3.6:27b` | 28.88 | 36.5 min |
| `qwen3.6:35b-a3b` | 5.70 | 7.5 min |
| `gemma2:27b` | 19.00 | 24.1 min |
| **screen total** | | **1.66 h** |
| confirmation (5 seeds, baseline + `qwen3:14b`, the draft's assumption) | 2 x 75 calls | 0.41 h |
| confirmation (5 seeds, baseline + `qwen3.6:27b`, the slowest candidate) | 2 x 75 calls | 0.74 h |

**Budget: 2.5 h of Ollama wall-clock** from the first scored call. The measured screen total,
**1.66 h, is within it**, so no candidate is dropped before the screen. With the
confirmation the total is 2.07 h when the selected candidate is `qwen3:14b` and
2.40 h if it were `qwen3.6:27b`, the slowest (28.9 s per call against
`qwen3:8b`'s 6.7); both are within budget, so the confirmation keeps its 5 seeds.

**Per-call timeout.** Four times the baseline's median (1.58 s), floor 60 s,
as in #1557: **60 s**, passed to `run` as `--timeout 60` (the harness default is 240 s). Read
this before the screen runs: `qwen3.6:27b` took 53.3 s on the 2,151 character
case, and its replies to the larger scored cases (up to 2,458 characters) may run past 60 s; a
timeout scores as the stacked body and V1 counts it. `gemma2:27b` took 34.6 s. The rule is
the registered one and is not changed here.

**Observed, not a decision.** On the Stage 0 medians, V2 (median call latency <= 2.0x the
baseline's, 3.17 s) would pass `gemma4:26b-a4b` (1.19) and
`qwen3.6:35b-a3b` (1.09), and would not pass `qwen3:14b` (2.64),
`gemma2:27b` (4.70) or `qwen3.6:27b` (6.41). Those medians sit on the small
case; V2 is applied on the scored runs' medians, not on these four calls, and every candidate
stays in the screen.

One Ollama job at a time, models one at a time, server defaults, and this work the only user
of the server. The harness never contacts a model in `--self-test`, `forecast`, `blind-sheet`
or `adjudicate`; `stage0` and `run` do.

## Commands

```sh
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/reconcile_screen/run_reconcile_screen.py forecast --runs 5
uv run python -u evals/reconcile_screen/run_reconcile_screen.py stage0   # Stage 0, already run; calls only calibration inputs
uv run python -u evals/reconcile_screen/run_reconcile_screen.py run --model qwen3:8b --runs 5 --timeout 60
uv run python evals/reconcile_screen/run_reconcile_screen.py score
```

## Decisions the owner made (2026-10-27)

Each was an open question in this draft; the owner decided all five on 2026-10-27, as
recommended.

1. **A literal anchor is the test of "kept".** Decided by the owner, 2026-10-27. A model that
   keeps a claim in other words is scored as having lost it. The production prompt says "keep
   concrete details; do not summarize away substance", the anchors are the concrete part
   (identifiers, figures, quoted terms), and a rewrite that changes them is a change a reader
   of the merged document would have to re-check anyway. The accepted cost is a bias toward
   models that copy.
2. **"Exactly once" is the standard for a shared claim.** Decided by the owner, 2026-10-27. It
   is the prompt's own wording; strict accuracy decides and lenient accuracy is reported.
3. **Pooled decides; the two strata are reported, with no stratum rule.** Decided by the owner,
   2026-10-27. Twelve cases are repository documents and three are the demo bundle; three
   cases cannot carry a veto.
4. **`sentence_union` is the reference, not the stack.** Decided by the owner, 2026-10-27. It is
   the strongest baseline, the harder bar and the honest one (0.777 against 0.728 on the
   adjudicated labels); the stack is still reported.
5. **`gemma2:27b` is a candidate, as listed.** Decided by the owner, 2026-10-27. It is installed,
   the only model recommended for another task on this list, and its 8k context window fits
   these notes (at most about 1,400 characters each) well.

## Known limits (carried from the README)

One author's labels until the blind labelling; anchors are literals, so a paraphrase is scored
as a loss; prose quality and coherence are not scored; the notes are repository documents, not
ingest-produced concept bodies, and are shorter than a real merge; a shared claim counts one
literal; 2 of 121 claims are flagged arguable; fabrication sees only digit-bearing words and
code spans; five seeds; English only.
