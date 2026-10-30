# Pre-registration: `revision_history` at the 24576 window (#1702)

**Status: PROPOSED. It becomes REGISTERED when the owner has confirmed the
section "The owner confirms" and this file has merged to `main`, before the
first model call.** A change after that point is a new registration, not an
edit to this one. It generates no number itself: it fixes what will be
generated, how it will be read, and what each outcome means. Production code
and defaults are unchanged by this file and by the run. Every bar marked
**(proposed)** is the author's proposal until the owner confirms it.

This is a follow-up to `PREREGISTRATION.md` and `RESULTS.md` in this
directory. It reuses that measurement's bundle, questions, judging protocol,
metrics, bootstrap and bar constants, and changes one thing: the window.

## Question

Does `revision_history = true` improve answers to "how did decision X change
from meeting b to meeting c, in order" **at the 24576 context window**, without
hurting other questions, and at a latency the owner can accept?

Why it is open. The registered history axis was **REJECTED on B1** by its
futility stop: at `context_window` 12288 every history block was attached and
then omitted for budget (67 omitted, 0 delivered, 0 of 12 evolution questions
gained a delivered position), so a model could not state an earlier position it
was never shown (`RESULTS.md`, "History axis"). That result is narrow by its
own account: it "does not say what history does when the budget leaves room".
Since then ADR-0084 made 24576 the window `openkos init` writes on hosts with
32 GiB or more, so the budget does leave room on those hosts. This
registration measures exactly that, once.

A `SUPPORTED` verdict licenses a separate production change with its own ADR
to turn the default on. It does not flip `DEFAULT_REVISION_HISTORY` here.

Not tested here: the window itself (settled by `RESULTS.md` and ADR-0084),
series scoping (waits on an owner product decision), 12288 and 16384.

## What this does not touch

- Production code, prompts, defaults. `answer()` is called as shipped; only
  `revision_history` differs between the arms. The window is 24576 in both.
  Sufficiency check, query-aware excerpts, the limit (8) and the generation
  ceiling (8192) are the packaged defaults.
- Edge detection and application (`revisions`, `reconcile --from-findings`).
  The edges are authored in the bundle as the engine would write them after the
  person accepted every finding. Wrong edges (#1350, #1687) are not tested.
- Model choice: `qwen3:8b` only, as in the registered measurement.

## Arms

| Arm | `revision_history` | `context_window` | Role |
|---|---|---|---|
| `h24_off` | `False` (default) | 24576 | baseline |
| `h24_on` | `True` | 24576 | candidate |

The two differ in exactly one key. `h24_off` is the configuration of the
earlier `w24576` arm, but it is **generated again** here rather than reused: the
latency bar compares the two arms, and a baseline measured weeks earlier on
another machine state is not a fair denominator. Seeds are the run number, the
same in both arms: n = 3 runs (seeds 1 to 3). Embeddings use `bge-m3`.

## Data

Unchanged from `PREREGISTRATION.md`: the synthetic bundle in
`evolution_bundle.py` (a Spanish product, two meeting series of 5 dated
meetings, eight decision chains of 4 positions, `supersedes` and `revises`
edges authored as the engine would write them) and the questions in
`evolution_questions.py` (12 evolution questions and 8 control questions, each a
Spanish and an English pair asked over the Spanish bundle; the question-set hash
is stored in `contexts.json`). The gold of an evolution question is two ordered
positions and a series.

**The guardrail set.** The 56 questions of
`evals/retrieval_gold/questions.v1.json` over the built gold bundle. That bundle
carries no `supersedes` or `revises` edge, so the walk has nothing to follow and
every prompt must be byte-identical between `h24_off` and `h24_on` (G1). This is
a no-op check of the shipped walk at the 24576 window, **not a measurement of
harm**; harm is measured by the controls (B3, B4).

## Metrics

All rates are the share of cells judged `correct`, averaged over runs, then
over the two language variants of a base question, then over base questions
(the cluster). Definitions are those of `PREREGISTRATION.md`, "Metrics":
**judged-correct**, **mf-correct**, **contamination**, **delivered** (phase 1,
no chat) and **mean seconds per answer** (wall-clock of one `answer()` call,
sufficiency check included, failed calls excluded). One addition for phase 1:

- **history delivered**: the number of evolution prompts of `h24_on` whose
  context blocks include at least one history block (a block attached by the
  walk **and** kept by the budget; an attached block that was omitted does not
  count).

## Bars

The constants below are the ones in `measure_decision_evolution.py::BARS_H24`
(which takes B0 to B4, the futility gain and the INCONCLUSIVE floors from
`BARS` unchanged); the self-test fails if this file and the code disagree.

```
HEADROOM_MAX = 0.75
BENEFIT_MIN = 0.2
CORROBORATION_MIN = 0.1
CONTROL_DROP_MAX = 0.1
CONTAMINATION_RISE_MAX = 0.05
FUTILITY_GAIN_MIN = 2
FAILED_MAX = 0.15
UNJUDGED_MAX = 0.1
KAPPA_MIN = 0.6
HISTORY_DELIVERY_MIN = 5
H24_LATENCY_MEAN_RATIO_MAX = 1.5
```

`h24_on` against `h24_off` (all (proposed)):

- **B0, headroom.** The `h24_off` judged-correct rate on evolution questions is
  below `HEADROOM_MAX`. Otherwise **NO-HEADROOM**.
- **B1, benefit.** On evolution questions the judged-correct difference
  (`h24_on` minus `h24_off`) is at least `BENEFIT_MIN` and its one-sided 95%
  lower bound is above 0. The bound is a seeded cluster bootstrap over the 6
  base questions (10 000 resamples, seed 1702); the two languages of a question
  are one cluster.
- **B2, corroboration.** The mf-correct difference on evolution questions is at
  least `CORROBORATION_MIN`, so a gain cannot rest on the judge alone.
- **B3, no harm.** On controls the judged-correct difference is at least
  `-CONTROL_DROP_MAX`.
- **B4, no more mixing.** The contamination rise on evolution questions is at
  most `CONTAMINATION_RISE_MAX`.
- **B6, latency.** The `h24_on` mean seconds per answer is at most
  `H24_LATENCY_MEAN_RATIO_MAX` (1.5) times the `h24_off` mean. It is the
  analogue of W4 with the ratio only: W4's 30 s absolute cap was set for the 12288
  window and `h24_off` itself ran at a mean of 40.4 s as `w24576` (`RESULTS.md`),
  so an absolute cap would fail the baseline. 1.5x is the ratio B5 used for the
  same question at 12288 (history on against off), and is the author's pick for
  "history may cost half again as much as the answer it improves, no more". B6
  is named so it does not collide with B5, which this registration does not use.
- **G1, gold guardrail.** All 56 gold prompts are identical between `h24_off`
  and `h24_on` (56 of 56).

**Verdicts.** `INCONCLUSIVE` when an invariant failed (a `h24_off` prompt carries
a history block, or `h24_on` never attached one), more than `FAILED_MAX` of the
calls failed or timed out, more than `UNJUDGED_MAX` of the answered cells have no
verdict, or the second judge's kappa is below `KAPPA_MIN`; reported, never
rescued by more runs. Then `NO-HEADROOM`. Then `SUPPORTED` when every bar is met,
otherwise `REJECTED` naming the failed bars.

## n and the stopping rule

- **n = 3 runs per arm** (seeds 1 to 3) over 20 questions: 60 cells per arm,
  120 chat cells. Reasons as in `PREREGISTRATION.md`: seed variance is small next
  to question variance; the binding n is the 6 base clusters.
- **Phase 1, embedder only (`contexts --axis history24576`).** Assembles all 40
  prompts (20 questions, 2 arms) with a capturing stub in place of the chat model
  and checks the invariants.
- **Futility stop, after phase 1** (`futility_h24`). If fewer than
  `HISTORY_DELIVERY_MIN` (5) of the 12 evolution prompts of `h24_on` deliver a
  history block, **or** `h24_on` delivers both gold values on fewer than
  `FUTILITY_GAIN_MIN` (2) more evolution questions than `h24_off`, then **neither
  arm is run** and the axis is **REJECTED on B1**: a model cannot state a position
  it was not shown. The first number reuses the existing minimum of the earlier
  window exposure stop (5 prompts); the second is the earlier history futility
  stop unchanged. The stop reads only prompts and ends only this axis.
- No other stopping rule. The decision rule is never consulted to stop early,
  extend or re-run. A failed or timed-out call is reported as "n failed of
  TOTAL" and not re-run.

## Judging

Identical to `PREREGISTRATION.md`, "Judging". **Blind Claude Opus judges** (one
per range of cells) read a sheet of opaque codes holding, per cell, the question,
the gold in words, the answer and the ids it cited, and nothing else: no arm, no
run, no check result, no score, no verdict, no history flag. **One blind Claude
Sonnet judge** repeats a seeded sample of 40 cells for agreement only (Cohen's
kappa, gate `KAPPA_MIN`); **the first judge's label stands**, no disagreement is
hand-resolved. The sheet's key is written next to it and kept away from the
judges. Disclosed as before: model judges rather than human ones, of the family
that authored the gold, so a shared misreading is invisible to the agreement
figure.

## Cost forecast

Sources: `RESULTS.md`, `w24576` cells (same configuration as `h24_off`): mean 40.4
s per answer, median 38.4 s, p90 55.9 s, over 60 cells. There is no stored timing
with a history block at 24576; the `h24_on` rows are an estimate and labelled so.

| Step | Queries | Low | Typical | High |
|---|---|---|---|---|
| `h24_off`, 20 q x 3 runs (about 30 to 60 s each, typical 40.4 s) | 60 | 0.50 h | 0.67 h | 1.00 h |
| `h24_on` (estimate: 40 to 100 s each, typical 55 s) | 60 | 0.67 h | 0.92 h | 1.67 h |
| `contexts` (embedder, 40 prompts) | 0 chat | 0.03 h | 0.05 h | 0.05 h |
| `guardrail` (reindex 489 gold documents, **unmeasured**; observed total of `contexts` + `guardrail` was about 2.5 min) | 0 chat | 0.05 h | 0.10 h | 0.40 h |
| model loads (about 2 min each, at most two) | | 0.03 h | 0.07 h | 0.07 h |
| **Total** | 120 | **1.3 h** | **1.8 h** | **3.2 h** |

If the futility stop fires, only `contexts` and `guardrail` run: about 0.1 h.

**Hard cap: 4 GPU hours**, after which the run stops and the axis is read on
what exists (cells missing is INCONCLUSIVE). Judging uses no GPU. Ollama
serializes, so this is one job at a time. The GPU is reserved by the owner: ask
before the first model call.

## Exact commands

One Ollama job at a time; each runs to completion before the next. Record
`git rev-parse HEAD` and `ollama list` first. `OUT` defaults to
`~/Dev/Projects/openkos-eval-runs/decision-evolution-h24` (outside the repository,
separate from the earlier run).

```sh
M=evals/decision_evolution/measure_decision_evolution.py

# model-free, anywhere
OLLAMA_HOST=127.0.0.1:1 uv run python $M --self-test

# phase 1: embedder only. Read the futility_stop of contexts ONLY.
uv run python $M contexts --axis history24576
uv run python $M guardrail --axis history24576

# phase 2, only if futility_stop is false; both arms, one job
uv run python $M run --axis history24576

# judging (no model here): sheets, then the judges fill them, then ingest
uv run python $M judge-sheet
uv run python $M judge-sheet --second-sample 40
uv run python $M judge-ingest --sheet SHEET --key KEY --judge opus --out-file OUT/judgments-first.json
uv run python $M judge-ingest --sheet SHEET2 --key KEY2 --judge sonnet --out-file OUT/judgments-second.json

# model-free from here
OLLAMA_HOST=127.0.0.1:1 uv run python $M score --axis history24576
OLLAMA_HOST=127.0.0.1:1 uv run python $M decide --axis history24576
```

`score` prints every metric and applies no bar; `decide` applies them, and prints
the REJECTED-on-B1 verdict directly when `contexts.json` records the futility
stop. The identity stamp (harness commit, model digest, hash of the prompts sent)
is written into every stored file.

## Outputs

`RESULTS_HISTORY_24576.md` in this directory: the commit, model digests, cells
run, failed and unjudged, the table of every bar, per-base-question rates, the
verdict, the cost in seconds against the forecast, and the limits below. Nothing
from the private corpus is read (the gold bundle is read, as `retrieval_gold`
does, and only counts are published). The verdict updates #1702 and nothing else.

## Disclosed before scoring

- No model has answered any question under these arms. Nothing was run on GPU.
  The model-free self-test builds the bundle with a stub embedder and checks the
  prompts, the scoring and the bars.
- **Headroom is marginal.** `w24576` (the `h24_off` configuration) scored 0.750
  judged-correct on evolution questions, exactly `HEADROOM_MAX`. B0 requires
  strictly below, so if `h24_off` reproduces that figure the verdict is
  **NO-HEADROOM**, which is a legitimate outcome and not a failure of the run.
  Even a score of 0.70 would leave B1 needing 0.90 from `h24_on`. The bar
  constants are reused unchanged, on purpose: moving them after seeing that
  number would be a bar set after a result. The owner may change them at
  confirmation, which makes this a different registration.
- The bundle was authored knowing how the walk works (a `supersedes` target is
  deprecated and so hidden from the baseline). That favours `h24_on`; B0 and the
  controls exist to catch a gain that is only that.
- At 24576 the budget may now keep history blocks, but the Sources still fill
  most of it; phase 1 reports how many are kept, and the stop above decides
  whether a chat run is worth its hours.

## Limits (assumptions, labelled)

- Synthetic, templated Spanish text from one product and 8 chains. Real
  transcripts are noisier and longer.
- Six base clusters: the interval is wide, and a real gain smaller than
  `BENEFIT_MIN` is invisible by design.
- Mention order is a proxy for "in order", and the judges are models of one
  family.
- Edges are correct by construction; a library whose edges were applied wrongly
  (#1350) is not represented.
- Tests 24576 only, with the other keys at their defaults. It says nothing about
  16384 or 12288 (where history delivers nothing on this bundle).
- Latency of `h24_on` is an estimate; one machine; the cap bounds the damage.

## The owner confirms

Nothing is confirmed yet. Items to confirm, each as proposed or changed:

1. The bars marked **(proposed)**: B0 headroom at 0.75 (and its marginal status,
   "Disclosed before scoring"); B1 benefit of +0.2 with a positive
   cluster-bootstrap lower bound; B2 corroboration of +0.1; B3 control drop at
   most 0.1; B4 contamination rise at most 0.05; **B6 mean latency at most 1.5x
   `h24_off`**; G1 56 of 56 identical; the INCONCLUSIVE floors (15% failed, 10%
   unjudged, kappa 0.6).
2. The design: the earlier synthetic bundle and 12 evolution and 8 control
   questions, Spanish and English pairs over a Spanish bundle, n = 3 runs per
   arm, `h24_off` generated again rather than reused.
3. The futility stop: fewer than 5 evolution prompts delivering a history block,
   or fewer than 2 more evolution questions with both gold values delivered, ends
   the axis (REJECTED on B1) before any chat call.
4. The judging protocol, as in `PREREGISTRATION.md`.
5. GPU time: the forecast (1.3 to 3.2 h, typical 1.8 h), the 4 h hard cap, and
   when the GPU is free (it is reserved by the owner).
