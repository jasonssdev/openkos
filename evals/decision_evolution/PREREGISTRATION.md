# Pre-registration: decision-evolution answers, `revision_history` and the context window (#1702, #1703)

**Status: PROPOSED. It becomes REGISTERED when the owner has confirmed the
section "The owner confirms" and this file has merged to `main`, before the
first model call.** A change after that point is a new registration, not an
edit to this one. It generates no number itself: it fixes what will be
generated, how it will be read, and what each outcome means. Production code
and defaults are unchanged by this file and by the run. Every bar marked
**(proposed)** is the author's proposal until the owner confirms it.

## Questions

1. **History axis (#1702).** Does `revision_history = true` improve answers to
   "how did decision X change from meeting b to meeting c, in order" without
   hurting other questions? `DEFAULT_REVISION_HISTORY` is `False` because no
   harness has measured the key (`config.py`); this is that harness. A
   `SUPPORTED` verdict licenses a separate production change with its own ADR
   to turn the default on. It does not flip it here.
2. **Window axis (#1703, PR #1714).** Does a 24576-token context window
   improve the same answers enough to justify its latency? #1714 would make
   `openkos init` size `context_window` from memory (12288 below 32 GiB, 16384
   from 32 GiB, 24576 from 64 GiB). #1703 reports that a larger window alone
   did not improve quality and cost latency (about 100 s per answer at 32768
   against 10 to 20 s at 12288, observed once). **#1714 merges only if this
   axis is `SUPPORTED`: a quality gain that justifies the latency.** Any other
   verdict keeps #1714 held.

Series scoping (filtering retrieval by the series a question names) is **not
tested here**. It waits on an owner product decision. The bundle keeps both
series about the same product so a later registration can measure scoping on
it; this one only measures whether the history walk makes cross-series mixing
worse (B4).

## What this does not touch

- Production code, prompts, defaults. `answer()` is called as shipped; only
  `revision_history` (history axis) or the chat client's `context_window`
  (window axis) differ between arms. Sufficiency check, query-aware excerpts,
  the limit (8) and the generation ceiling (8192) are the packaged defaults.
- Edge detection and application (`revisions`, `reconcile --from-findings`).
  The edges are authored in the bundle as the engine would write them after the
  person accepted every finding. Wrong edges (#1350, #1687) are not tested.
- Model choice: `qwen3:8b` only, as in the issue.

## Arms

| Arm | `revision_history` | `context_window` | Role |
|---|---|---|---|
| `off` | `False` (default) | 12288 (default) | baseline of both axes |
| `on` | `True` | 12288 | history axis candidate |
| `w24576` | `False` (default) | 24576 | window axis candidate |

`off` is the baseline of the window axis too: it is the same configuration, so
its cells are generated once and shared. Seeds are the run number, the same in
every arm. Embeddings use `bge-m3`.

## Data

**The bundle** (`evolution_bundle.py`, committed, synthetic, no private
corpus): a Spanish product ("Marea", a tide-gauge panel) with two meeting
series, a product council and an engineering sync, each of 5 dated meetings
(10 `Source`s of about 4 KB, long enough that the context budget trims them).
Eight decision chains of 4 positions (32 `Decision`s): retention and export
format exist in both series with different values; four subjects exist in one
series. Every position after the first carries one `supersedes` or `revises`
edge to the previous one. A `supersedes` target is deprecated by
`lifecycle.deprecated_concept_ids`, so the `off` arm cannot retrieve it, which
is the state of a library after `reconcile --from-findings`. Six neutral
concept documents name no value. A Decision never names its series and never
mentions the position it replaces: the series lives in the Source title and
the order in the edges and the Source dates.

**The questions** (`evolution_questions.py`, hash in `contexts.json`):
12 evolution questions and 8 control questions, each a Spanish and an English
pair asked over the Spanish bundle. Six base evolution questions: three name
the series, three do not (their subject exists in one series only, so the
answer is still unique); spans of one, two and three hops. Four base controls
ask for the current position of a chain ("according to the most recent
decision"). The gold of an evolution question is two ordered positions and a
series. `qwen3:8b` answered Spanish questions over English bundles with no
measurable cost in the ask-screen, which is why English is asked over Spanish.

**The guardrail sets.**

- History: the 56 questions of `evals/retrieval_gold/questions.v1.json` over
  the built gold bundle. That bundle carries no `supersedes` or `revises` edge,
  so the walk has nothing to follow and every prompt must be byte-identical
  (G1). This is a no-op check of the shipped walk, **not a measurement of
  harm**: the 56-question set cannot detect harm from history because it holds
  no history. Harm is measured by the controls (B3, B4).
- Window: the same 56 questions through `retrieval_gold`'s model-free
  `delivered` metric at 12288 and at 24576 (WG).

## Metrics

All rates are the share of cells judged `correct`, averaged over runs, then
over the two language variants of a base question, then over base questions
(the cluster).

- **judged-correct**: the blind judge's verdict `correct` (see "Judging").
- **mf-correct** (model-free): the answer names both positions, names the
  earlier before the later (first mention; a proxy that is stricter than the
  judge, since "moved to X, from Y" reads as unordered), cites no document of
  the other series and states no value of the other series, and cites at least
  one document. For a control: names the current position with the same series
  rule.
- **contamination**: the share of answers that cite a document of the other
  series.
- **delivered** (phase 1, no chat): whether each gold value reaches the prompt.
- **mean seconds per answer**: wall-clock of one `answer()` call, sufficiency
  check included, failed calls excluded.

## Bars

The constants below are the ones in `measure_decision_evolution.py::BARS`; the
self-test fails if this file and the code disagree.

```
HEADROOM_MAX = 0.75
BENEFIT_MIN = 0.2
CORROBORATION_MIN = 0.1
CONTROL_DROP_MAX = 0.1
CONTAMINATION_RISE_MAX = 0.05
LATENCY_RATIO_MAX = 1.5
ON_P90_MAX_SECONDS = 60
FUTILITY_GAIN_MIN = 2
FAILED_MAX = 0.15
UNJUDGED_MAX = 0.1
KAPPA_MIN = 0.6
WINDOW_EXPOSURE_MIN = 5
WINDOW_LATENCY_MEAN_MAX_SECONDS = 30
WINDOW_LATENCY_RATIO_MAX = 2.0
```

**History axis, `on` against `off`** (all (proposed)):

- **B0, headroom.** The `off` arm's judged-correct rate on evolution questions
  is below `HEADROOM_MAX`. Otherwise **NO-HEADROOM**.
- **B1, benefit.** On evolution questions the judged-correct difference
  (on minus off) is at least `BENEFIT_MIN` and its one-sided 95% lower bound is
  above 0. The bound is a seeded cluster bootstrap over the 6 base questions
  (10 000 resamples, seed 1702); the two languages of a question are one
  cluster because they are correlated.
- **B2, corroboration.** The mf-correct difference is at least
  `CORROBORATION_MIN`, so a gain cannot rest on the judge alone.
- **B3, no harm.** On controls the judged-correct difference is at least
  `-CONTROL_DROP_MAX`.
- **B4, no more mixing.** The contamination rise on evolution questions is at
  most `CONTAMINATION_RISE_MAX`.
- **B5, cost.** The `on` median seconds per answer is at most `LATENCY_RATIO_MAX`
  times the `off` median, and the `on` 90th percentile is at most
  `ON_P90_MAX_SECONDS`.
- **G1, gold guardrail.** All 56 gold prompts are identical between `off` and
  `on` (56 of 56).

**Window axis, `w24576` against `off`** (all (proposed)):

- **Exposure.** At least `WINDOW_EXPOSURE_MIN` questions have a different prompt
  under the larger window (phase 1, no chat). Otherwise **NO-EXPOSURE**: the
  window could not have moved an answer on this bundle, which is neither a
  gain nor a refutation.
- **W0, headroom.** As B0, on the 12288 arm.
- **W1, quality.** As B1.
- **W2, corroboration.** As B2.
- **W3, no harm on controls.** As B3.
- **W4, latency.** The `w24576` mean seconds per answer is at most
  `WINDOW_LATENCY_MEAN_MAX_SECONDS` and at most `WINDOW_LATENCY_RATIO_MAX` times
  the `off` mean. This is what "justifies the latency" means: a gain that
  clears W1 to W3 at a latency over W4 is **REJECTED on W4** and #1714 stays
  held.
- **WG, gold guardrail.** On the 56 gold questions no question's `delivered@8`
  falls from 12288 to 24576 (`delivered-compare`; the model-free metric). It
  reports the gain beside it. It does not measure generated answers on the
  gold set.

**Verdicts, per axis.** `INCONCLUSIVE` when an invariant failed (an `off` or
`w24576` prompt carries a history block, or the `on` arm never attached one),
more than `FAILED_MAX` of the calls failed or timed out, more than
`UNJUDGED_MAX` of the answered cells have no verdict, or the second judge's
kappa is below `KAPPA_MIN`; reported, never rescued by more runs. Then
`NO-HEADROOM` or `NO-EXPOSURE`. Then `SUPPORTED` when every bar of the axis is
met, otherwise `REJECTED` naming the failed bars. `SUPPORTED` on the history
axis opens a production change (ADR, OpenSpec delta, default flip) and is not a
flip. `SUPPORTED` on the window axis is the only verdict that lets #1714 merge.

## n and the stopping rules

- **n = 3 runs per arm** (seeds 1 to 3) over 20 questions: 60 cells per arm.
  In the stored ask-screen runs (`qwen3:8b`, fixed context) 2 of 36 items moved
  between seeds, mean within-item standard deviation 0.028, so seed variance is
  small next to question variance; the binding n is 6 base clusters, which the
  bootstrap uses. More runs would not narrow the interval, more questions would.
- **Phase 1, embedder only (`contexts`).** Assembles all 60 prompts with a
  capturing stub in place of the chat model and checks the invariants.
- **Futility stops, after phase 1.** *History:* if the `on` arm delivers both
  gold values in fewer than `FUTILITY_GAIN_MIN` more evolution questions than
  `off` (`futility_stop`), the `on` arm is not run and the history axis is
  **REJECTED on B1**: a model cannot state a position it was not shown.
  *Window:* if fewer than `WINDOW_EXPOSURE_MIN` prompts differ
  (`window_futility_stop`), `w24576` is not run and the axis is
  **NO-EXPOSURE**. Each stop reads only prompts, can only end its axis, and is
  independent of the other.
- No other stopping rule. The decision rule is never consulted to stop early,
  extend or re-run. A failed or timed-out call is reported as "n failed of
  TOTAL" and not re-run.

## Judging

Following `evals/retrieval_gold/RESULTS-v1-baseline.md`: **blind Claude Opus
judges** (one per range of cells) read a sheet of opaque codes with the
question, the gold in words, the answer and the ids it cited, and no arm, run
or score; **one blind Claude Sonnet judge** repeats a seeded sample of 40 cells
for agreement only (Cohen's kappa, gate `KAPPA_MIN`); **the first judge's label
stands**, no disagreement is hand-resolved. This is a deviation from human
judges, disclosed in `RESULTS.md`, and the judges share a family with the
model that authored the questions' gold, so a systematic misreading of that
family is invisible to the agreement figure. The sheet's key is written next to
it and kept away from the judges.

## Cost forecast

Sources of the rates: the stored ask-screen records (`qwen3:8b`, window 12288,
192 answers: answer call median 4.8 s, p90 7.9 s, max 10.6 s; sufficiency
median 2.7 s, max 13.3 s, so about 8 s per query), and #1703's report (10 to
20 s per answer at 12288 on the owner's library, about 100 s at 32768, one
observation). No stored timing exists at 24576 or with history blocks, so those
rows are an interpolation and are labelled as such.

| Step | Queries | Low | Typical | High |
|---|---|---|---|---|
| `off`, 20 q x 3 runs (baseline of both axes) | 60 | 0.13 h | 0.25 h | 0.42 h |
| `on` (about 10 to 40 s each) | 60 | 0.17 h | 0.30 h | 0.67 h |
| `w24576` (about 15 to 100 s each, interpolated) | 60 | 0.25 h | 0.67 h | 1.67 h |
| `contexts` (embedder, 60 prompts) | 0 chat | 0.03 h | 0.05 h | 0.05 h |
| `guardrail` (reindex 489 gold documents, **unmeasured**) | 0 chat | 0.10 h | 0.20 h | 0.40 h |
| two model loads (about 2 min each) | | 0.07 h | 0.07 h | 0.07 h |
| **Total** | 180 | **0.75 h** | **1.5 h** | **3.3 h** |

**Hard cap: 5 GPU hours**, after which the run stops and each axis is read on
what exists (an axis with cells missing is INCONCLUSIVE). Judging uses no GPU.
Ollama serializes (a default server does not run two jobs faster), so this is
one job at a time. The GPU is reserved by the owner: ask before the first model
call. A generated-answer guardrail on the 56 gold questions (56 x 2 arms x 3
runs = 336 queries, about 1.4 h typical) is not in this plan; see item 7.

## Exact commands

One Ollama job at a time; each runs to completion before the next. Record
`git rev-parse HEAD` and `ollama list` first. `OUT` defaults to
`~/Dev/Projects/openkos-eval-runs/decision-evolution` (outside the repository).

```sh
M=evals/decision_evolution/measure_decision_evolution.py

# model-free, anywhere
OLLAMA_HOST=127.0.0.1:1 uv run python $M --self-test

# phase 1: embedder only. Read futility_stop and window_futility_stop ONLY.
uv run python $M contexts
uv run python $M guardrail

# phase 2 (each only if its futility stop did not fire); `off` is shared
uv run python $M run --axis history
uv run python $M run --axis window

# window gold guardrail (model-free metric over the stored gold run)
W=~/openkos-eval-runs/retrieval-gold
uv run python evals/retrieval_gold/run_retrieval_gold.py delivered \
  --workspace $W/bundle-v1 --run $W/run-v1.json --passages $W/passages/passages-v1.json \
  --query-aware --context-window 12288 --max-generation-tokens 8192 \
  --out $W/passages/delivered-w12288.json
uv run python evals/retrieval_gold/run_retrieval_gold.py delivered \
  --workspace $W/bundle-v1 --run $W/run-v1.json --passages $W/passages/passages-v1.json \
  --query-aware --context-window 24576 --max-generation-tokens 8192 \
  --out $W/passages/delivered-w24576.json
uv run python evals/retrieval_gold/run_retrieval_gold.py delivered-compare \
  --workspace $W/bundle-v1 --base $W/passages/delivered-w12288.json \
  --cand $W/passages/delivered-w24576.json

# judging (no model here): sheets, then the judges fill them, then ingest
uv run python $M judge-sheet
uv run python $M judge-sheet --second-sample 40
uv run python $M judge-ingest --sheet SHEET --key KEY --judge opus --out-file OUT/judgments-first.json
uv run python $M judge-ingest --sheet SHEET2 --key KEY2 --judge sonnet --out-file OUT/judgments-second.json

# model-free from here
OLLAMA_HOST=127.0.0.1:1 uv run python $M score --window-gold-regressed N --window-gold-total 56
OLLAMA_HOST=127.0.0.1:1 uv run python $M decide --window-gold-regressed N --window-gold-total 56
```

`N` is the number of gold questions the `delivered-compare` report lists as
regressing. `score` prints every metric and applies no bar; `decide` applies
them. The identity stamp (harness commit, model digest, hash of the prompts
sent) is written into every stored file.

## Outputs

`RESULTS.md` in this directory: the commit, model digests, cells run, failed
and unjudged, both axes' tables, per-base-question rates, the verdicts, the
cost in seconds against the forecast, and the limits below. Nothing from the
private corpus is read (the gold bundle is read, as `retrieval_gold` does, and
only counts are published). The verdicts update #1702 and #1703/#1714 and
nothing else.

## Disclosed before scoring

- No model has answered any of these questions. Nothing was run on GPU. The
  model-free self-test builds the bundle with a stub embedder and checks the
  prompts, the scoring and the bars.
- The bundle was authored knowing how the walk works (a `supersedes` target is
  deprecated and so hidden from the `off` arm). That favours the `on` arm and is
  the stated state of a library after the person accepted every finding; B0 and
  the controls exist to catch a gain that is only that.
- The bundle's Sources were lengthened with neutral filler so the context
  budget has something to trim; exposure of the window axis is a phase-1
  number, not an assumption.
- The self-test was written before the harness, observed failing, and
  mutation-checked once: see the PR description.

## Limits (assumptions, labelled)

- Synthetic, templated Spanish text from one product and 8 chains. Real
  transcripts are noisier and longer.
- English questions are asked over a Spanish bundle; the cross-lingual penalty
  was within +-0.04 for `qwen3:8b` in the ask-screen, not re-measured here.
- Six base clusters: the interval is wide, and a real gain smaller than
  `BENEFIT_MIN` is invisible by design.
- Mention order is a proxy for "in order", and the judges are models of one
  family (see "Judging").
- Edges are correct by construction. A library whose edges were applied wrongly
  (#1350) is not represented, and `revision_history` would show those as
  history.
- The window axis tests 24576 only, with `revision_history` at its default; it
  says nothing about 16384 or about both keys on together.
- Latency on 24576 and with history blocks is interpolated, not measured; one
  machine; the cap bounds the damage.

## The owner confirms

On 2026-10-29, before this file merged and before any result existed, the
owner gave a standing instruction to take the recommended option on open
choices; under it every item below is confirmed unchanged, as proposed, with
judging by delegated model judges disclosed in RESULTS. The owner did not
review the items one by one; this is recorded so the confirmation is not read
as more than it is.

1. The bars marked **(proposed)**: B0/W0 headroom at 0.75; B1/W1 benefit of
   +0.2 with a positive cluster-bootstrap lower bound; B2/W2 corroboration of
   +0.1; B3/W3 control drop at most 0.1; B4 contamination rise at most 0.05;
   B5 latency 1.5x median and 60 s p90; W4 mean latency at most 30 s and 2.0x;
   WG no regression on the gold `delivered@8`; G1 56 of 56 identical; the
   INCONCLUSIVE floors (15% failed, 10% unjudged, kappa 0.6).
2. The design: the synthetic bundle (including that `supersedes` hides the
   earlier positions from the `off` arm), 12 evolution and 8 control questions,
   Spanish and English pairs over a Spanish bundle, n = 3 runs per arm.
3. The futility and exposure stops (fewer than 2 more evolution questions
   delivered, or fewer than 5 prompts changed by the window, ends that axis
   before any chat call).
4. The judging protocol: blind Claude Opus judges, a Sonnet kappa sample, first
   label stands, model judges on the owner's delegation as in
   `retrieval_gold`.
5. The window axis and its rule: 24576 against 12288 with `revision_history` at
   its default, and **#1714 merges only if the axis is `SUPPORTED`** (a quality
   gain that clears W1 to W3 and WG at a latency inside W4), otherwise it stays
   held.
6. GPU time: the forecast (0.75 to 3.3 h, typical 1.5 h, window arm included),
   the 5 h hard cap, and when the GPU is free (it is reserved by the owner).
7. The gold-set guardrail for the history axis being a prompt-identity check
   (G1, no GPU, expected to hold by construction) and for the window axis the
   model-free `delivered` metric, rather than a generated-answer arm on the 56
   questions (about 1.4 h typical, not in the forecast).
8. Series scoping stays untested here, pending the owner's product decision.
