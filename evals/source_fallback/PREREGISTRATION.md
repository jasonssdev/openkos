# Pre-registration: a bounded Source fallback when the sufficiency check refuses (#1741)

**Status: PROPOSED. It becomes REGISTERED when the owner has confirmed the section
"The owner confirms" and this file has merged to `main`, before the first model call.**
A change after that point is a new registration, not an edit to this one. It generates
no number itself: it fixes what will be generated, how it will be read, and what each
outcome means. Production code, prompts and defaults are unchanged by this file and by
the run. Every bar marked **(proposed)** is the author's proposal until the owner
confirms it.

It is modelled on `evals/source_demotion/` and `evals/attach_at_ingest/` and reuses the
bundle, questions and scoring of `evals/decision_evolution/` and the unanswerable
questions of `evals/query_grounding/`. The treatment is not in production; it lives in
`measure_source_fallback.py` only, as a wrapper around `answer()` that acts on one
verdict: `no_match_cause == "insufficient_context"`.

## Question

When the pre-synthesis sufficiency check refuses a question (`NO_ANSWER_IN_CONTEXT`,
`llm_invoked=False`, no second attempt), does one bounded second try over the **raw
Source text** recover the answers that extraction lost, **without answering questions
the library genuinely cannot answer**?

This is the proposal of #1741. A `SUPPORTED` verdict licenses a separate production
change with its own OpenSpec change and ADR (opt-in key, result field, CLI, MCP). It
changes nothing here.

### Why this is not #1703 again

`evals/source_demotion/RESULTS.md` (#1703, REJECTED on B1, B2 and B6) cut a Source to a
short excerpt because a derived Decision of it was already in the context. Nothing was
added: the Decisions already stated every answer, so the characters freed from the
Source bought no correctness (0.556 to 0.556), and the cost showed up in citations
(0.500 to 0.333). That treatment **re-allocated a fixed budget over evidence the model
already had**, and the issue states the lesson itself ("budget changes alone did not
move correctness"). This treatment differs on three points that decide whether the
#1703 result transfers:

| | #1703 demotion | #1741 fallback |
|---|---|---|
| Where the answer lives | in a derived page already in the context | **only in the raw Source** (extraction lost it) |
| What changes | how a fixed budget is shared | **new evidence enters the prompt** where there was none |
| When it acts | every over-budget context | **only after a refusal**, on no other path |
| What it can hurt | citations, mixing | **abstention itself**: a refusal becomes an answer |

So the #1703 null result predicts nothing about B1 here, and the risk to measure is not
the one #1703 measured. The refusal is a feature of this engine (a sufficiency check
that false-refused nothing in 100 grounded checks, `evals/query_sufficiency/README.md`),
so the primary bar on the cost side is B3: **the fallback must not turn a correct
abstention into a fabricated answer.**

### What the shipped code does today (verified)

- `answer()` runs the sufficiency check on the assembled context and, on a refusal,
  returns `NO_ANSWER_IN_CONTEXT` with `no_match_cause="insufficient_context"` and no
  synthesis. Nothing retries.
- `fusion.select_top` caps `Source` documents at `max(1, limit // 2)` of the 8 slots, so
  on a library with many Sources the compiled concepts crowd them out of the context;
  the Source that holds a lost fact may not be in the refused context at all.
- A Source that is in the context is cut to its fair share of one window shared with up
  to seven other blocks (`_bound_bodies`, `prompt_budget.fair_shares`), by BM25 against
  the question since ADR-0082.

## What this does not touch

- Production code, prompts, defaults. `answer()` is called as shipped (sufficiency check
  on, query-aware excerpts on, limit 8, generation ceiling 8192, `revision_history` off).
- The context window: `CONTEXT_WINDOW = 12288` in both arms. The "wider budget" of the
  issue is a **wider share per Source, not a wider window**: three Sources share the
  whole prompt budget that eight blocks share today (below).
- Ranking and fusion of the shipped path. The fallback reads the same fused ranking, cut
  differently.
- Model choice: `qwen3:8b` for the answer, the sufficiency check and the re-check. The
  external evaluation of #1741 used a separate `gemma4:26b-a4b` sufficiency model; that
  is item 6 of "The owner confirms", not an arm here.

## Arms

| Arm | Treatment | Role |
|---|---|---|
| `base` | `answer()` as shipped | baseline |
| `fallback` | the same call; **only when it returns `insufficient_context`**, one bounded fallback (below) | candidate |

The `fallback` cell is **derived from the `base` cell** (same call, same seed, same
answer), not a second independent call: a question the fallback does not fire on is
byte-identical in both arms by construction, and the comparison is paired exactly. The
fallback's own time and calls are added to the `fallback` arm.

The treatment, exactly (`run_fallback` in the harness):

1. **Trigger.** `no_match_cause == "insufficient_context"` and the base call did not
   fail. No other verdict fires it (`zero_hits`, `all_unreadable`, `empty_query`, a
   sufficiency check that failed open).
2. **Candidates.** The first `FALLBACK_SOURCES = 3` documents of `type: Source`, in the
   fused ranking of the same candidate pool (`pool.pool_limit(8)`), deduplicated. The
   Source cap of `select_top` does not apply: this is the second look at the Sources.
3. **Budget (bounded).** The shipped budget, measured by construction exactly as
   `_bound_bodies` does (`prompt_budget.budget_chars`, same window, same reply reserve,
   same overhead rule), shared by those Sources with `fair_shares`; each Source is cut to
   a query-aware excerpt of 800-character windows (ADR-0082, the shipped
   `prompt_budget.bounded_text`). The blocks together never exceed the budget (an
   invariant). The prompt is therefore no larger than the shipped one; each Source's share
   is about 8/3 of what it had among eight blocks.
4. **One re-check.** The shipped sufficiency prompt, once, over the new context. A
   failed re-check, a backend error or an empty context returns the **original refusal**.
5. **One synthesis.** The shipped system prompt, once. The reply must carry a usable
   `USED:` line naming the blocks it drew on; **a reply with no usable attribution is a
   refusal** (no uncited answer is ever returned from sources).
6. **No recursion, no retry, fail closed.** A `BackendError` in step 4 or 5 returns the
   original refusal; the fallback never turns an error into an answer. At most
   `MAX_EXTRA_CALLS = 2` extra model calls per question (one re-check, one synthesis);
   a refused re-check costs one.

### How answered-from-sources provenance is represented

The harness records, per fallback answer, `origin: "sources"` (against `"compiled"` for a
shipped answer and `"refusal"` for no answer), a banner line that opens the answer text:

    [answered from sources, not compiled objects]

and the citations as **Source line ranges**: `{id, start, end}` with 1-based line numbers
of the Source **body** (after the frontmatter), one range per excerpted window of each
block the model reported using. Invariants (a violation is INCONCLUSIVE): a sourced
answer has at least one range and the banner; a base answer never carries the banner.

A production change, if the verdict licenses one, would carry the same three things as
fields rather than prose: an `answer_origin` on the answer result (`compiled` or
`sources`), citations with line ranges, and a visible marker on every surface (CLI,
`query --json`, MCP). That is a proposal for the later change, **not** part of this
registration. Two properties are fixed here because they make the provenance honest: the
marker is **unconditional** (a sourced answer can never look like a compiled one), and
the citations are **ranges the model was actually shown** (a quoted excerpt, not a whole
Source).

## Data

Committed, synthetic; no private corpus is read. Fixture hash (`fixture_sha256()`, over
the item ids, conditions, bundles and texts):
`7c1be799ffcdf20e54bca5448d58d1bd46754f363db947faa3110c0baaaa41d3`.

**Answerable point facts, 8 chains.** The 4 control base questions of
`evals/decision_evolution/evolution_questions.py` (a Spanish and an English pair each,
the current value of a decision) plus 4 Spanish questions derived by this harness for the
4 chains no control asks about (`consejo/alerts`, `consejo/export`,
`ingenieria/retention`, `ingenieria/refresh`): **12 questions, 8 clusters**. Gold is the
chain's last position, scored with the existing markers (`mde.score_answer`:
`mf_correct` = the value named, from the right series, cited).

They are asked over two copies of the bundle of `evals/decision_evolution`
(`evolution_bundle.py`: 2 meeting series, 10 `Source` transcripts of about 4 KB, 32
`Decision` pages that cite their Source):

- **`loss` (12 questions).** The bundle with `decisions/` **removed**: extraction lost
  the fact, the raw transcript holds it. The harness checks that, for every one of these
  questions, a Source of the question's series contains the gold value. This is the
  situation of #1741 (21 of 43 wrong point-fact answers traced to extraction), made
  total: every point fact is lost.
- **`intact` (12 questions, ids `c1i-es`, ...).** The full bundle. The compiled path can
  answer; the fallback should rarely fire. Reported as a readout, see B-bars.

**Genuinely unanswerable questions, 14 (the abstentions to protect):**

- 10 adjacent questions of `evals/query_grounding/grounding_corpus.py` (general RAG
  topics whose vocabulary overlaps the corpus and which it does not answer), verbatim, over
  its 14-document bundle (which has `Source` transcripts), where the sufficiency check
  was measured to refuse all of them.
- 4 **absent-fact** questions authored here (`ua11` to `ua14`), asked over the `loss`
  bundle: a supplier of cloud hosting, an approved quarter budget, a new hire, a
  translation language. Every meeting mentions each topic ("el contrato con el proveedor
  de nube", "el presupuesto del trimestre", ...) with a status and **never a value**, so
  the Source is on topic and holds no answer. This is the case where a fallback over raw
  text is most tempted to invent one. The harness checks the topics are in the Sources.

Why not the gold-set bundle of `evals/retrieval_gold/`: it has no gold answer text, only
gold spans, so correctness cannot be scored without a judge. Scoring here is
deterministic (markers and citations), which removes the judge and its agreement bar and
keeps the whole decision reproducible from stored cells.

## Metrics

Per cell (one question, one run, one arm): `outcome` (`ok` or `failed`), the answer text,
the cited ids, the `origin`.

- **correct** (answerable questions): the existing `mf_correct`: the gold value is named,
  the answer is not a refusal, and no value or citation of the other series appears.
- **foreign value**: a value of the other series is stated (contamination).
- **false answer** (unanswerable questions): `outcome == "ok"` and at least one citation.
  A refusal, or an answer with all citations stripped by the shipped `USED:` attribution,
  is an abstention.
- **sourced answer**: an answer whose `origin` is `sources`.
- **precision**: among sourced answers on answerable questions, the share that are correct.
- **span share**: among correct sourced answers on `loss` questions, the share whose cited
  excerpt text contains the gold value (the citation really supports the answer).
- **extra calls** and **seconds per answer**: wall clock of one cell, the fallback's
  re-check and synthesis included in the `fallback` arm, failed calls excluded from the
  rates.

Rates are over clusters (language variants of a base question are one cluster), averaged
over runs; false answers are pooled over cells (the unanswerable items have no variants).

## Bars

The constants are the ones in `measure_source_fallback.py::BARS` and the lines after it;
the self-test fails if this file and the code disagree.

```
CONTEXT_WINDOW = 12288
BOOTSTRAP_SEED = 1741
RESAMPLES = 10_000
MAX_EXTRA_CALLS = 2
FALLBACK_SOURCES = 3
FIRE_MIN = 6
REACH_MIN = 0.5
HEADROOM_MAX = 0.6
BENEFIT_MIN = 0.25
SPAN_SHARE_MIN = 0.8
FALSE_ANSWER_RISE_MAX = 0.05
PRECISION_MIN = 0.7
CONTAMINATION_RISE_MAX = 0.05
LATENCY_MEAN_RATIO_MAX = 1.25
FAILED_MAX = 0.15
```

`fallback` against `base` (all (proposed)):

- **B0, headroom.** The `base` correct rate on `loss` questions is below `HEADROOM_MAX`
  (0.6). Otherwise **NO-HEADROOM**. (On the lossy bundle it is expected near 0; the bar
  exists so a surprising baseline cannot be mistaken for a gain.)
- **B1, benefit.** On `loss` questions the correct difference (`fallback` minus `base`) is
  at least `BENEFIT_MIN` (0.25) and its one-sided 95% lower bound is above 0 (seeded
  cluster bootstrap over the 8 base questions, `RESAMPLES`, `BOOTSTRAP_SEED`).
- **B2, the citation supports the answer.** The span share is at least `SPAN_SHARE_MIN`
  (0.8) and there is at least one correct sourced answer. A correct answer whose cited
  lines do not contain the value is the model's prior, not the Source, and must not count
  as recovery.
- **B3, abstention is protected.** On the 14 unanswerable questions the false-answer
  rate of `fallback` minus that of `base` is at most `FALSE_ANSWER_RISE_MAX` (0.05; with
  42 cells that is two cells). **This is the bar the issue's "correct abstention is a
  property to protect" maps to.**
- **B4, precision.** Among sourced answers on answerable questions the correct share is at
  least `PRECISION_MIN` (0.7), with at least one sourced answer. The fallback converts a
  safe refusal into an answer; an answer that is wrong is worse than the refusal it
  replaced. (The intact bundle is in this pool: the fallback fires there only after a
  false refusal.)
- **B5, no more mixing.** The foreign-value rate rises by at most `CONTAMINATION_RISE_MAX`
  (0.05) on `loss` questions. Raw transcripts of two similar series are the easiest place
  to cite the wrong meeting.
- **B6, cost.** The mean seconds per answer over all cells of `fallback` is at most
  `LATENCY_MEAN_RATIO_MAX` (1.25) times that of `base`. Extra model calls per cell (mean
  and max) and the fire rate are reported; the maximum is bounded by the invariant
  `MAX_EXTRA_CALLS`.

Readouts, no bar: the number of fallbacks that fired per condition, the `intact` correct
rates of both arms (the fallback can only help there: it fires only on a refusal, and a
refusal of an answerable question is never correct), the per-cluster table, the share of
fired cells whose fallback prompt holds the gold value, the refused re-checks.

**Verdicts.** `INCONCLUSIVE` when an invariant failed, any cell is missing or any fired
fallback is unrun, or more than `FAILED_MAX` (0.15) of the base calls failed; reported,
never rescued by more runs. Then `NO-EXPOSURE` and `NO-REACH` (below), then `NO-HEADROOM`
(B0). Then `SUPPORTED` when B1 to B6 all hold, otherwise `REJECTED` naming the failed
bars.

## n and the stopping rule

- **n = 3 runs per arm** (seeds 1 to 3) over 38 items: 114 base cells; the `fallback`
  arm adds no base calls (derived), only the fallback calls on fired cells (at most 78).
  The binding n is the number of base questions (8 for B1), so B1's interval will be wide.
- **Pass 1 (`run`).** The shipped `answer()` for every cell, plus, for every refusal, the
  **model-free plan** of its fallback (which Sources, the budget, whether the gold value
  is in the assembled prompt). No fallback model call has been made.
- **Futility stops, read after pass 1, before any fallback call** (`futility`):
  - **NO-EXPOSURE:** fewer than `FIRE_MIN` (6) of the 36 `loss` cells refused. If the
    shipped engine refuses almost none of these, there is nothing to recover. Ends this
    axis.
  - **NO-REACH:** fewer than `REACH_MIN` (0.5) of the fired `loss` cells have the gold
    value in the assembled fallback prompt. A model cannot state a value it was not shown;
    retrieval, not the fallback, is then the limit. Ends this axis.
- **Pass 2 (`fallback`).** Re-check and synthesis on the fired cells, only if no stop
  fired.
- No other stopping rule. The decision rule is never consulted to stop early, extend or
  re-run. A failed or timed-out call is reported as "n failed of TOTAL" and not re-run.

## Judging

None. Correctness, contamination and false answers are deterministic functions of the
stored answer text and cited ids (the markers of `evolution_bundle.py`, the citation list
of `answer()`). The span share is a deterministic substring check. There is no judge, no
sheet and no agreement bar; the cost is that **correctness is a marker match, not a
reading of the answer**: an answer that names the value in the wrong sense is scored
correct, one that paraphrases it is scored wrong. The same proxy scored the earlier
decision-evolution registrations (`mf-correct`) and it is stated here as a limit.

## Cost forecast

Estimates, **unmeasured** for this harness. Sources: `evals/decision_evolution/RESULTS.md`
(mean 14.9 s per answer at 12288 on this bundle), the sufficiency probe (median 1.1 s per
check), the demotion run (0.4 h for 66 cells).

| Step | Cells | Low | Typical | High |
|---|---|---|---|---|
| pass 1: 114 shipped answers (8 to 25 s each; a refusal skips synthesis) | 114 | 0.25 h | 0.40 h | 0.80 h |
| pass 1: indexing 3 bundles with `bge-m3` | 0 chat | 0.03 h | 0.05 h | 0.15 h |
| pass 2: re-check (1 to 3 s) and synthesis (8 to 25 s) on fired cells (at most 78) | 78 | 0.10 h | 0.30 h | 0.60 h |
| model loads (about 2 min each, at most two) | | 0.03 h | 0.07 h | 0.07 h |
| **Total** | | **0.4 h** | **0.8 h** | **1.6 h** |

If a futility stop fires, only pass 1 runs: about 0.5 h typical.

**Hard cap: 2.5 GPU hours**, after which the run stops and the verdict is read on what
exists (cells missing is INCONCLUSIVE). Ollama serializes, so this is one job at a time.
The GPU is reserved by the owner: ask before the first model call.

## Exact commands

One Ollama job at a time; each runs to completion before the next. Record
`git rev-parse HEAD` and `ollama list` first. `OUT` defaults to
`~/Dev/Projects/openkos-eval-runs/source-fallback` (outside the repository).

```sh
M=evals/source_fallback/measure_source_fallback.py

# model-free, anywhere
OLLAMA_HOST=127.0.0.1:1 uv run python $M --self-test

# pass 1: the shipped answers and the fallback plans. Then read futility ONLY.
uv run python $M run
OLLAMA_HOST=127.0.0.1:1 uv run python $M futility

# pass 2, only if futility is null
uv run python $M fallback

# model-free from here
OLLAMA_HOST=127.0.0.1:1 uv run python $M score
OLLAMA_HOST=127.0.0.1:1 uv run python $M decide
```

`score` prints every metric and applies no bar; `decide` applies them. The identity stamp
(harness commit, model digest, hash of the prompts sent) is written into the store.

## Outputs

`RESULTS.md` in this directory: the commit, model digests, cells run, failed and fired,
the table of every bar, the per-cluster rates, the fire rate and fallback outcomes
(sourced, refused re-check, unattributed, error), the cost in seconds and calls against the
forecast, the verdict and the limits below. The verdict updates #1741 and nothing else.

## Disclosed before scoring

- No model has answered any question under these arms. Nothing was run on GPU. The
  model-free self-test checks the trigger, the bound, the single re-check, the provenance
  marking, the scoring and the bars on stub callables and synthetic cells.
- **The `loss` bundle is a total loss, the best case for the treatment.** Every Decision
  is removed, so the compiled path has nothing and the baseline is expected near 0: B1's
  headroom is maximal and a gain here is an **upper bound** on a real library, where
  extraction lost some of the facts (21 of 43 wrong answers in the external evaluation) and
  kept others, possibly stale or partial. The `intact` readout shows the other end. A
  partial-loss bundle is a different registration.
- **Synthetic, templated, Spanish.** The 10 transcripts are about 4 KB with 36 filler lines;
  the external evaluation used long AMI meeting transcripts (English, overlapping speakers,
  disfluent) where finding the passage is far harder. The fallback's reach (NO-REACH) is
  likely to be higher here than in that setting.
- **Three Sources of ten.** `FALLBACK_SOURCES = 3` is a judgment call, about the number that
  gives each Source over twice its present share; a library of thousands of Sources makes
  the right Source far less likely to be in the first three. The cap was not tuned.
- **One re-check is a second chance for the same judge.** The sufficiency check was
  measured to false-refuse 0 of 100 grounded checks and to refuse the adjacent questions;
  a second look at different text is not independent evidence that the first refusal was
  wrong. B3 is the measurement that matters, and it has 42 cells: two false answers is the
  bar's whole margin.
- **Adjacent questions are easy refusals.** They are about general topics; the absent-fact
  questions are the hard ones and there are 4 of them (12 cells). A rise concentrated there
  would be 3 cells of 12 and is below the resolution of the pooled bar. The per-item
  counts are reported with the verdict.
- **The bars are the author's picks.** `FAILED_MAX`, `BOOTSTRAP_SEED` and `RESAMPLES` are
  carried over from the earlier registrations; no earlier registration had an analogue of
  B2 to B4, and the latency ratio is looser (1.25) than the 1.1 of #1703 because this
  treatment adds model calls where #1703 added none. `BENEFIT_MIN = 0.25` is a quarter of the questions
  recovered; the external evaluation's total gap between compiled base and plain RAG was
  0.23 (0.49 to 0.72).
- The fire rate is a function of the sufficiency model. A stronger separate sufficiency
  model (the external evaluation's `gemma4:26b-a4b`) refuses a different set.

## What a `SUPPORTED` verdict does not establish

That it holds on real, long, noisy transcripts; that it holds at a different window,
model or sufficiency model; that a partially lossy library behaves like a totally lossy
one; or that the answer is true (OpenKOS preserves representations, not truth). It
establishes that, on this fixture, a bounded second read of the Sources recovered point
facts the extraction had lost, cited the lines that hold them, and did not fabricate on
the unanswerable questions.

## Limits (assumptions, labelled)

- A handful of base questions (8) and 14 unanswerable items: wide intervals; a real effect
  smaller than `BENEFIT_MIN` is invisible by design.
- Correctness is a marker match and citation is checked by substring, not by reading.
- Sources are retrieved by the same dense and lexical signals that retrieved the refused
  context; a fact in a Source that neither signal ranks in the first three is out of reach
  by construction (reported as NO-REACH).
- One model, one window, one machine; `seed` is the run number, and a local model at
  default temperature is not deterministic across seeds.
- Line numbers cite the Source body as stored; a Source re-ingested later has different
  lines.

## The owner confirms

1. The treatment: trigger only on `insufficient_context`; at most 3 Sources from the same
   fused pool, cut to the shipped budget by query-aware excerpts; one re-check; one
   synthesis; unattributed replies and errors fail closed to the original refusal;
   `FALLBACK_SOURCES = 3`; the "wider budget" read as a wider **share per Source** at the
   same window, not a wider window.
2. The provenance representation: `origin`, the unconditional banner
   `[answered from sources, not compiled objects]`, citations as Source body line ranges.
3. The fixture: the `loss` bundle is the Marea bundle with every Decision removed (total
   loss, best case), plus the `intact` readout; point facts are the 4 control questions
   plus 4 derived Spanish ones; unanswerable are the 10 adjacent grounding questions plus
   4 authored absent-fact questions. Whether a partial-loss variant should be registered
   instead of, or in addition to, the total loss.
4. The bars marked **(proposed)**: B0 headroom 0.6; B1 benefit of +0.25 with a positive
   cluster-bootstrap lower bound; B2 span share 0.8; **B3 false-answer rise at most 0.05
   (two cells of 42), versus a strict 0.0**; B4 precision 0.7; B5 contamination rise at
   most 0.05; B6 mean latency at most 1.25x with the fire rate and extra calls reported;
   the INCONCLUSIVE floor of 15% failed calls.
5. The futility stops: fewer than 6 refused `loss` cells (NO-EXPOSURE); fewer than half of
   the fired `loss` cells holding the gold value in the fallback prompt (NO-REACH). Both
   read after pass 1 and before any fallback call.
6. Whether the sufficiency check and the re-check should run on a separate stronger model
   as in the external evaluation (`gemma4:26b-a4b`) rather than `qwen3:8b`; as written,
   `qwen3:8b` throughout, so the result is about the mechanism and not about a model pair.
7. Scoring without a judge (deterministic markers and substring checks), with the
   disclosed loss of precision.
8. Whether a neutral outcome (no B1 gain, no B3 harm) should be a second registered
   verdict. As written there is none: a treatment that does not clear B1 is REJECTED on B1.
9. GPU time: the forecast (0.4 to 1.6 h, typical 0.8 h), the 2.5 h hard cap, and when the
   GPU is free (it is reserved by the owner).
