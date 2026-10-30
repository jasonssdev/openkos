# Pre-registration: demoting a Source block under the context budget (#1703)

**Status: PROPOSED. It becomes REGISTERED when the owner has confirmed the section
"The owner confirms" and this file has merged to `main`, before the first model call.**
A change after that point is a new registration, not an edit to this one. It generates
no number itself: it fixes what will be generated, how it will be read, and what each
outcome means. Production code, prompts and defaults are unchanged by this file and by
the run. Every bar marked **(proposed)** is the author's proposal until the owner
confirms it.

It is modelled on `evals/decision_evolution/` and `evals/series_boost/` and reuses
their bundle, questions, scoring, bootstrap and blind-judging code. The treatment is
not in production; it lives in `measure_source_demotion.py` only, as a wrapper around
the one function `answer()` bounds its context blocks with.

## Question

When the context `query` builds exceeds its budget, does cutting a **Source** block
down to a short excerpt, because a derived page of that Source (a Decision, Concept
or Event that names it in `provenance`) is already in the context, improve answers,
without hurting control questions, citations or latency?

This is the retrieval half of #1703 ("do not send whole Source bodies as context when
derived pages exist for them; send an excerpt or the derived pages, and keep the Source
for citation"). The sizing half shipped in PR #1714 (ADR-0084). A `SUPPORTED` verdict
licenses a separate production change with its own OpenSpec change and ADR. It changes
nothing here.

### What the shipped code does today (verified, so the premise is stated correctly)

The issue speaks of "dropping whole blocks". On the code as it stands that is the rare
case, and the measurement is about something narrower:

- `fusion.select_top` already caps Sources at `max(1, limit // 2)` of the 8 slots
  (#1220), so Sources cannot fill every slot.
- `answer._bound_bodies` shares the budget by water-filling
  (`prompt_budget.fair_shares`): a short derived page keeps its whole body and the
  long Sources split what is left, each cut to a query-aware excerpt. That is what the
  warning "the model read only part of N document(s)" reports (`excerpted_titles`).
- A block is **omitted whole** (`omitted_titles`) only when its share rounds to zero.
  On this bundle at 12288 the stored runs show 0 omitted blocks (`RESULTS.md`, `off`:
  0), and the model-free assembly of this harness shows the same.

So the treatment does not turn omitted blocks into kept ones. It **re-allocates the
budget**: a Source with a derived sibling is cut harder, up front, and the characters
it frees go to the other blocks of the same prompt. The measurement asks whether that
re-allocation helps. A reader should not expect it to shorten the prompt: when the
budget binds, the prompt stays about as long as before.

## What this does not touch

- Production code, prompts, defaults. `answer()` is called as shipped, with the
  packaged defaults for the sufficiency check, query-aware excerpts, the limit (8), the
  generation ceiling (8192) and `revision_history` (off). Only the wrapper differs
  between the arms.
- The window. It is 12288 in both arms (below).
- Ranking, fusion and the Source-share cap. The set of 8 retrieved concepts is the same
  in both arms; only how their bodies share the budget differs.
- Model choice: `qwen3:8b` only, as in the earlier registrations.

## Arms

| Arm | Treatment | `context_window` | Role |
|---|---|---|---|
| `base` | `answer()` as shipped | 12288 | baseline |
| `demote` | when the context exceeds its budget, each Source block longer than `DEMOTED_SOURCE_CHARS` whose derived page is in the same context is cut to that size first, then all blocks share the budget as shipped | 12288 | candidate |

The treatment, exactly (`demote` and `demoting` in the harness):

1. Run the shipped bound. If it cut nothing (the context fits), stop: the prompt is the
   shipped one. The treatment never acts on a context that fits.
2. Otherwise a block is **demoted** when it is a `Source`, its body is longer than
   `DEMOTED_SOURCE_CHARS`, and another block of the same call is a non-Source document
   whose `provenance` names it.
3. A demoted body is cut to at most `DEMOTED_SOURCE_CHARS` with the engine's own
   query-aware excerpt (the same windows and elision marker any excerpted block gets).
4. The shipped bound runs again on the cut bodies, so the freed characters go to the
   other blocks.
5. Nothing is added, removed or reordered. A demoted Source **keeps its numbered
   block, its label and its citation**, marked excerpted. It is not reduced to a bare
   label: the engine deliberately refuses a label with no body, because a numbered
   block with nothing in it is a citation with no support (#882).

The two arms differ in this wrapper only. Seeds are the run number, the same in both
arms: n = 3 runs (seeds 1 to 3). Embeddings use `bge-m3`. `base` is **generated again**
rather than taken from the earlier `off` arm: the latency bar compares the two, and a
baseline from another day is not a fair denominator.

## Data

The synthetic bundle of `evals/decision_evolution/evolution_bundle.py` (a Spanish
product, two meeting series of 5 dated meetings, ten `Source` documents of about 4 KB
each, 32 `Decision` pages that cite their Source in `provenance`, 5 `Concept` pages and
a `Project` page that cite none) and its 12 evolution questions and 8 control questions
(`evolution_questions.py`): 12 evolution questions and 8 control questions, each a
Spanish and an English pair asked over the Spanish bundle. Committed, synthetic; no
private corpus is read. The question-set hash is stored in `contexts.json`.

Why this bundle. It is the one whose 12288 behaviour is already measured (`off`:
judged-correct 0.361 on evolution, so there is headroom; Sources are cut to excerpts,
not omitted), and its Decisions carry the answer in full while the series identity
lives only in the Source title, so a demoted Source is exactly the case the issue
describes. No new bundle is authored here: a new bundle would be a new registration.

**The guardrail set.** The 56 questions of `evals/retrieval_gold/questions.v1.json`
over the built gold bundle (`~/openkos-eval-runs/retrieval-gold/bundle-v1`). Unlike a
hand-made bundle it was produced by `openkos ingest`: 61 Sources and 364 documents
with real `provenance`, so the demotion can fire on it. It is a no-chat check
(embedder only), bar G1.

## The window, and why not 24576

**12288.** It is `DEFAULT_CONTEXT_WINDOW`, the value `openkos init` writes on a host
below 32 GiB, and the value on which the issue observed the warning. It is also where
the treatment acts: at 24576 the Sources of this bundle fit whole (`RESULTS_HISTORY_24576.md`:
0 blocks omitted, and `h24_off` delivered both gold values on 11 of 12 evolution
questions), the shipped bound cuts nothing, step 1 of the treatment stops, and every
`demote` prompt would equal its `base` prompt. A measurement there has no exposure. It
would also be a different claim ("shorten what already fits"), which the issue does not
make. 24576 and 16384 are not tested.

## Metrics

All rates are the share of cells judged `correct`, averaged over runs, then over the
two language variants of a base question, then over base questions (the cluster).
Definitions are those of `evals/decision_evolution/PREREGISTRATION.md`, "Metrics":
**judged-correct**, **mf-correct**, **contamination** (an answer cites a document of the
other series or states a value of it), and **mean seconds per answer** (wall-clock of
one `answer()` call, sufficiency check included, failed calls excluded). Added here:

- **treated**: a question whose `demote` prompt differs from its `base` prompt. Every
  rate, bar and cell below is over treated questions only. The other questions have a
  byte-identical prompt by construction, so asking them would measure nothing.
- **demoted Source**: a Source that phase 1 recorded as demoted for that question.
- **citation pair**: one (answered cell, demoted Source) where the answer cites a
  derived page of that Source, that is, the answer used what the Source backs. The pair
  is a **hit** when the Source itself is cited too. Both arms are read against the same
  demoted Sources (the ones phase 1 recorded), so `base` shows how often the Source is
  cited with no demotion. The **citation rate** of an arm is hits over pairs, pooled.

## Bars

The constants below are the ones in `measure_source_demotion.py::BARS` (and the three
after them); the self-test fails if this file and the code disagree.

```
CONTEXT_WINDOW = 12288
BOOTSTRAP_SEED = 1703
RESAMPLES = 10_000
DEMOTED_SOURCE_CHARS = 1600
EXPOSURE_MIN = 5
EVO_CLUSTERS_MIN = 3
CONTROL_CLUSTERS_MIN = 2
DISPLACED_MAX = 1
HEADROOM_MAX = 0.75
BENEFIT_MIN = 0.2
CORROBORATION_MIN = 0.1
CONTROL_DROP_MAX = 0.1
CONTAMINATION_RISE_MAX = 0.05
LATENCY_MEAN_RATIO_MAX = 1.1
CITATION_DROP_MAX = 0.1
CITATION_PAIRS_MIN = 10
NEW_EMPTY_MAX = 0.05
FAILED_MAX = 0.15
UNJUDGED_MAX = 0.1
KAPPA_MIN = 0.6
```

`demote` against `base`, over treated questions (all (proposed)):

- **B0, headroom.** The `base` judged-correct rate on treated evolution questions is
  below `HEADROOM_MAX`. Otherwise **NO-HEADROOM**.
- **B1, benefit.** On treated evolution questions the judged-correct difference
  (`demote` minus `base`) is at least `BENEFIT_MIN` and its one-sided 95% lower bound is
  above 0. The bound is a seeded cluster bootstrap over the treated base questions
  (`RESAMPLES` resamples, seed `BOOTSTRAP_SEED`); the two languages of a question are one
  cluster.
- **B2, corroboration.** The mf-correct difference on treated evolution questions is at
  least `CORROBORATION_MIN`, so a gain cannot rest on the judge alone.
- **B3, no harm on controls.** On treated control questions the judged-correct
  difference is at least `-CONTROL_DROP_MAX`.
- **B4, no more mixing.** The contamination rise is at most `CONTAMINATION_RISE_MAX`
  on treated evolution questions **and** on treated control questions. A demoted Source
  loses the text that names its series, so this is the failure to watch.
- **B5, latency.** The `demote` mean seconds per answer is at most
  `LATENCY_MEAN_RATIO_MAX` (1.1) times the `base` mean. The budget binds in both arms,
  so no saving is expected; the bar only refuses a slowdown (the same 1.1 as the
  series-boost registration, for a treatment that adds no model call).
- **B6, citation integrity.** The citation rate of `demote` is at least the citation
  rate of `base` minus `CITATION_DROP_MAX`. "The demoted Source must still be cited when
  used" is read this way because the model reports which numbered blocks it drew on
  (#753): a Source reduced to an excerpt may stop being named, and then the answer cites
  the derived page but not the meeting it came from. With fewer than `CITATION_PAIRS_MIN`
  pairs in either arm the bar cannot be read and the verdict is **INCONCLUSIVE**, never a
  pass. Separately, phase 1 checks by construction that every demoted Source keeps its
  numbered block and is marked excerpted (an invariant, below).
- **B7, no new empty answers.** The share of answered cells with no citation (the
  engine's no-answer, including a sufficiency refusal) in `demote` exceeds that of
  `base` by at most `NEW_EMPTY_MAX`. Less evidence in the prompt could make the
  sufficiency check refuse more; this catches it.
- **G1, gold guardrail.** Over the 56 gold prompts no block present in `base` is absent
  from `demote` and `demote` omits no more blocks than `base` (56 of 56). Prompts that
  differ are reported as exposure on a real ingest, not as a failure. This checks that
  the demotion drops nothing; it is **not a measurement of answer quality** on the gold
  set.

**Verdicts.** `INCONCLUSIVE` when an invariant failed, more than `FAILED_MAX` of the
calls failed or timed out, more than `UNJUDGED_MAX` of the answered cells have no
verdict, the second judge's kappa is below `KAPPA_MIN`, a kind (evolution or control)
has no treated question, or a citation arm has too few pairs; reported, never rescued by
more runs. Then `NO-HEADROOM` (B0). Then `SUPPORTED` when B1 to B7 and G1 all hold,
otherwise `REJECTED` naming the failed bars.

## n and the stopping rule

- **n = 3 runs per arm** (seeds 1 to 3) over the treated questions, at most 20: at most
  60 cells per arm. Seed variance is small next to question variance; the binding n is
  the number of treated base questions, which is also why B1's interval will be wide.
- **Phase 1, embedder only (`contexts`).** Assembles all 40 prompts (20 questions, 2
  arms) with a capturing stub in place of the chat model, checks the invariants, and
  decides who is treated.
- **Invariants** (a violation is INCONCLUSIVE, no chat call): the `base` arm is never
  demoted; the patch ran in `demote`; every demoted Source keeps its block and is marked
  excerpted; no block of `base` is missing from `demote`; `demote` omits no more blocks
  than `base`; no history block (the default is off); a `demote` prompt differs from its
  `base` prompt only where the patch ran.
- **Exposure futility stop, after phase 1** (reads only prompts, ends only this axis,
  ends in **NO-EXPOSURE**): fewer than `EXPOSURE_MIN` of the 20 prompts differ between
  the arms, **or** fewer than `EVO_CLUSTERS_MIN` evolution base questions are treated,
  **or** fewer than `CONTROL_CLUSTERS_MIN` control base questions are treated (B3 and the
  control half of B4 would be vacuous). Neither arm is run.
- **Harm futility stop, after phase 1** (ends in **REJECTED**): more than
  `DISPLACED_MAX` questions have both gold values in the `base` prompt and not in the
  `demote` prompt. A model cannot state a value it was not shown, and the treatment only
  removes text from a Source.
- No other stopping rule. The decision rule is never consulted to stop early, extend or
  re-run. A failed or timed-out call is reported as "n failed of TOTAL" and not re-run.

## Judging

As in `evals/decision_evolution/PREREGISTRATION.md`, with one addition the protocol
needs. **Blind Claude Opus judges** (one per range of cells) read a sheet of opaque
codes holding, per cell: the question, the gold in words, **the reference text** (the
quoted gold Decision pages and the meeting each was recorded in, a function of the
question alone and so identical for both arms), the answer and the ids it cited, and
nothing else: no arm, no run, no check result, no score, no verdict, no mention of a
treatment. The reference is there so a judge can tell a value the documents support from
one that was invented; the sheet tells them to credit nothing outside the gold and the
reference. **One blind Claude Sonnet judge** repeats a seeded sample of 40 cells for
agreement only (Cohen's kappa, gate `KAPPA_MIN`); **the first judge's label stands**, no
disagreement is hand-resolved. The sheet's key is written next to it and kept away from
the judges. Disclosed as before: model judges of the family that authored the gold, so a
shared misreading is invisible to the agreement figure.

## Cost forecast

Sources: `evals/decision_evolution/RESULTS.md`, the `off` arm (same configuration as
`base`): mean 14.9 s per answer, median 15.5 s, p90 18.9 s, over 60 cells. The budget
binds in both arms, so `demote` is forecast at the same size; that row is an estimate.
The upper bound assumes all 20 questions are treated; the model-free assembly treats
about 7 of 20 with a stand-in embedder, so a real run is likely well under it.

| Step | Cells | Low | Typical | High |
|---|---|---|---|---|
| `base`, up to 20 q x 3 runs (12 to 25 s each, typical 14.9 s) | 60 | 0.20 h | 0.25 h | 0.42 h |
| `demote` (estimate: 12 to 25 s each, typical 15 s) | 60 | 0.20 h | 0.25 h | 0.42 h |
| `contexts` (embedder, 40 prompts) | 0 chat | 0.03 h | 0.05 h | 0.05 h |
| `guardrail` (reindex the gold bundle, 112 assemblies, **unmeasured**; the history-24576 run's `contexts` + `guardrail` took about 2.5 min) | 0 chat | 0.05 h | 0.10 h | 0.40 h |
| model loads (about 2 min each, at most two) | | 0.03 h | 0.07 h | 0.07 h |
| **Total** | up to 120 | **0.5 h** | **0.7 h** | **1.4 h** |

If a futility stop fires, only `contexts` and `guardrail` run: about 0.1 h.

**Hard cap: 2 GPU hours**, after which the run stops and the verdict is read on what
exists (cells missing is INCONCLUSIVE). Judging uses no GPU. Ollama serializes, so this
is one job at a time. The GPU is reserved by the owner: ask before the first model call.

## Exact commands

One Ollama job at a time; each runs to completion before the next. Record
`git rev-parse HEAD` and `ollama list` first. `OUT` defaults to
`~/Dev/Projects/openkos-eval-runs/source-demotion` (outside the repository).

```sh
M=evals/source_demotion/measure_source_demotion.py

# model-free, anywhere
OLLAMA_HOST=127.0.0.1:1 uv run python $M --self-test

# phase 1: embedder only. Read phase1_verdict and stop_reason of contexts ONLY.
uv run python $M contexts
uv run python $M guardrail

# phase 2, only if phase1_verdict is null; both arms, treated questions, one job
uv run python $M run

# judging (no model here): sheets, then the judges fill them, then ingest
uv run python $M judge-sheet
uv run python $M judge-sheet --second-sample 40
uv run python $M judge-ingest --sheet SHEET --key KEY --judge opus --out-file OUT/judgments-first.json
uv run python $M judge-ingest --sheet SHEET2 --key KEY2 --judge sonnet --out-file OUT/judgments-second.json

# model-free from here
OLLAMA_HOST=127.0.0.1:1 uv run python $M score
OLLAMA_HOST=127.0.0.1:1 uv run python $M decide
```

`score` prints every metric and applies no bar; `decide` applies them, and prints the
phase-1 stop directly when `contexts.json` records one. The identity stamp (harness
commit, model digest, hash of the prompts sent) is written into every stored file.

## Outputs

`RESULTS.md` in this directory: the commit, model digests, treated questions and how
many blocks were demoted, cells run, failed and unjudged, the table of every bar, per
base question rates, the citation pairs, the verdict, the cost in seconds against the
forecast, and the limits below. Nothing from the private corpus is read (the gold
bundle is read, as `retrieval_gold` does, and only counts are published). The verdict
updates #1703 and nothing else.

## Disclosed before scoring

- No model has answered any question under these arms. Nothing was run on GPU. The
  model-free self-test builds the bundle with a stub embedder and checks the prompts,
  the scoring and the bars. The stub retrieves differently from `bge-m3`, so its count of
  treated questions (7 of 20, exactly at the evolution floor of 3 base questions) is
  only a sign that the treatment can reach a prompt, not a forecast of exposure.
- **The 1600-character cap is a judgment call.** It is two excerpt windows
  (`2 x 800`, the engine's own window target), about 40% of a 4 KB Source. It was not
  tuned and nothing was run to choose it. A different cap is a different registration.
- **A cut Source, not a bare citation.** "Demote" could also mean sending a Source as
  its citation only. That is rejected up front for the false-provenance reason above; it
  is not an arm here.
- **The treatment acts only on a context that already overflows.** That follows the
  issue ("when the context exceeds its budget") but means the shipped prompt of a
  question that fits is never touched, and the expected effect is a re-allocation inside
  an unchanged budget, not a shorter prompt.
- **The bundle favours the treatment.** Its Decisions state the whole answer, so a
  demoted Source can only lose redundant text. A library whose derived pages are thin
  would behave differently; the controls and B4 are there to catch a gain that is only
  that, and G1 on the ingested gold bundle is the one check on an ingest-shaped library.
- **Headroom.** `off` scored 0.361 on evolution questions at 12288, well under
  `HEADROOM_MAX`; the treated subset may differ. The bars are reused from the earlier
  registrations unchanged where they have an analogue: moving one after seeing a number
  would be a bar set after a result.
- **B1's +0.2 is a high bar for a re-allocation** and the prior that the treatment
  clears it is low. It is kept because the question is "does it improve answers"; a
  treatment that is only neutral and faster is not a verdict here (see the owner's item
  5).
- Bars with no earlier analogue (B6, B7, the exposure floors, `DISPLACED_MAX`,
  `LATENCY_MEAN_RATIO_MAX` at 1.1) are the author's picks.

## Limits (assumptions, labelled)

- Synthetic, templated Spanish text from one product. Real transcripts are noisier and
  longer, and a real library has derived pages of uneven quality (#1350, #1687).
- A handful of treated base questions: the interval is wide, and a real gain smaller
  than `BENEFIT_MIN` is invisible by design.
- Provenance is correct by construction in the bundle; a wrong `provenance` link would
  demote the wrong Source, and is not represented.
- Tests 12288 only, one cap, one model, one machine. It says nothing about 16384 or
  24576, nor about Sources far larger than 4 KB.
- Citation integrity is read from the model's self-reported use of numbered blocks (#753),
  not from whether the wording was supported.

## The owner confirms

Nothing is confirmed yet. Items to confirm, each as proposed or changed:

1. The treatment: demote only when the context overflows; only a Source longer than the
   cap with a derived sibling in the same context; cut (query-aware excerpt), not reduced
   to a bare citation; `DEMOTED_SOURCE_CHARS = 1600`.
2. The window: 12288 only, and the reason 24576 is out (no exposure).
3. The bars marked **(proposed)**: B0 headroom at 0.75; B1 benefit of +0.2 with a
   positive cluster-bootstrap lower bound; B2 corroboration of +0.1; B3 control drop at
   most 0.1; B4 contamination rise at most 0.05 on both kinds; B5 mean latency at most
   1.1x `base`; B6 citation-rate drop at most 0.1 with at least 10 pairs per arm; B7 new
   empty answers at most 0.05; G1 56 of 56; the INCONCLUSIVE floors (15% failed, 10%
   unjudged, kappa 0.6).
4. The futility stops: fewer than 5 prompts differ, or fewer than 3 evolution or 2
   control base questions treated (NO-EXPOSURE); more than 1 question losing a gold value
   (REJECTED). All before any chat call.
5. Whether a neutral-and-faster outcome should be a second registered verdict. As
   written there is none: a treatment that does not clear B1 is REJECTED on B1 even if it
   changes nothing else. The budget binds in both arms, so no speed-up is expected, and
   adding a verdict after seeing a number would be a bar set after a result.
6. The judging protocol, including the reference text on the sheet.
7. GPU time: the forecast (0.5 to 1.4 h, typical 0.7 h), the 2 h hard cap, and when the
   GPU is free (it is reserved by the owner).
