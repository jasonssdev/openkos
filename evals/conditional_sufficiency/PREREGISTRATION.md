# Pre-registration: running the sufficiency check less often (#1740)

**Status: PROPOSED. It becomes REGISTERED when the owner has confirmed the section
"The owner confirms" and this file has merged to `main`, before the first model call.**
A change after that point is a new registration, not an edit to this one. It generates
no number itself: it fixes what will be generated, how it will be read, and what each
outcome means. Production code, prompts and defaults are unchanged by this file and by
the run. Every bar and constant is the author's proposal until the owner confirms it.

It reuses the corpus, the 20 labelled questions, the `quote` verdict and the survivor
list of `evals/query_sufficiency/` (#760). Every treatment lives in
`measure_conditional_sufficiency.py` only.

## Question

Per query the compiled base pays for a pre-synthesis sufficiency check that re-sends the
whole assembled context to a second, larger model. Can that check be skipped, or made
cheaper, on the queries that do not need it, **while still refusing the 3 adjacent
questions that only the check catches**, refusing no more grounded questions, and
delivering the same answers?

A `SUPPORTED` verdict licenses a separate production change (a config value beside
`sufficiency_check`) with its own OpenSpec change and ADR. It changes nothing here.

### What the shipped code does today (verified)

`retrieval/answer.py::answer`, in order:

1. Assemble the numbered context blocks, then `user_content = _user_content(blocks, question)`.
2. When `sufficiency_check` is on: `_context_holds_the_answer` sends `_SUFFICIENCY_PROMPT`
   plus that same `user_content` to `sufficiency_llm` (the `sufficiency` task model, #1560)
   or to the answer model. A reply that IS the `NONE` sentinel is a refusal; a backend
   error fails open.
3. On a refusal the function returns `NO_ANSWER_IN_CONTEXT` with `llm_invoked=False`:
   **synthesis never runs**.
4. Otherwise one synthesis call with `_SYSTEM_PROMPT` and the same `user_content`; the
   reply's closing `USED:` line is parsed by `_split_attribution` and the citations are
   exactly the blocks it names. An explicit `USED: none` empties the citations (the
   answer then abstains in effect); a missing or unparsable line leaves every block cited.

So a grounded query pays check + synthesis, a refused one pays the check alone. The check
and the synthesis are **independent calls over one string**: neither reads the other's
output. That is what makes the measurement below possible.

### What the issue and #760 establish, and what they do not

- The issue measured 15.3 s per query for the compiled base against 5.7 s for single-call
  RAG, with the check on `gemma4:26b-a4b` and synthesis on `qwen3:8b`, over AMI meetings.
  This registration does **not** reproduce that setting; it measures the same
  configuration of models on the #760 corpus (disclosed below).
- `evals/query_sufficiency/README.md`: `USED:` attribution alone refuses 7 of 10 adjacent
  questions with 0 of 10 false refusals; 3 survive, and they survive **because they cite
  blocks** (per-run citation counts `[0,2,5]`, `[3,3,2]`, `[2,2,2]`). That is the premise
  to test: a gate that looks at the `USED:` line cannot see them unless it reads *which*
  blocks were cited.

## Method: one paired measurement per (question, run), arms replayed over it

For each of the 20 questions and each of 5 runs, three calls are made once and stored:

| Call | Model | Input | Stored |
|---|---|---|---|
| full check | check model | shipped prompt + full `user_content` | verdict, seconds |
| reduced check | check model | shipped prompt + top `REDUCED_BLOCKS` blocks | verdict, seconds |
| synthesis | answer model | shipped system prompt + full `user_content` | `USED:` attribution, cited blocks, seconds |

Each arm is then **replayed** over the stored triple in the order it would run, adding
only the seconds of the calls it would make (`simulate` in the harness). No arm asks a
model anything the stored triple does not already hold, so all arms see the same model
noise and the comparison is paired.

Phases: all checks first (check model warm), then all syntheses (answer model warm), so
no timing contains a model swap. The check model and the answer model are different
(below), so this is the cleanest reading, and it is also the **optimistic** one for
latency; see the limits.

## Arms

| Arm | Sequence | The check runs when |
|---|---|---|
| `baseline` | check on full context, then synthesize unless refused | always (shipped) |
| `cond_unattributed` | synthesize, then check on full context only if gated | the reply has no usable `USED:` line (`absent` or `unparsed`): the issue's literal first proposal |
| `cond_lowrank` | the same, with a wider gate | no usable `USED:` line, **or** the reply cites a block ranked below `LOW_RANK_TOP_K` (the issue's "cites low-scoring blocks"; context position is fused rank order) |
| `reduced` | check on the top `REDUCED_BLOCKS` blocks only, then synthesize unless refused | always, on a shorter context: the issue's second proposal |

In both conditional arms a check that refuses AFTER synthesis discards the answer and
returns the shipped refusal; a gate that stays shut delivers the answer as is. A reply
with an explicit empty `USED:` never opens the gate: it already abstains through its
citations, in every arm alike.

**Abstained** (the user sees no answer): the arm's check refused, **or** the delivered
reply cites nothing (`USED:` reported and empty). This is the shipped end-to-end
behaviour, applied identically in all four arms.

`LOW_RANK_TOP_K = 2` and `REDUCED_BLOCKS = 3` of 5 retrieved blocks are judgment calls.
Nothing was run to choose them and no development set exists; a different value is a
different registration.

## Data

`evals/query_grounding/grounding_corpus.py`: the 14-document Spanish bundle, 10 grounded
and 10 adjacent questions, 3 of the adjacent ones the **survivors** listed verbatim in
`run_query_sufficiency_probe._ATTRIBUTION_SURVIVORS` (imported, not copied). Retrieval is
the shipped seam (`bge-m3`, fused FTS + dense, limit 5, as #760) and is assembled once;
the 5 runs vary only the models' own sampling. Committed, synthetic; no private corpus.

## Models

Primary configuration (the issue's): check model `gemma4:26b-a4b`, answer model
`qwen3:8b`, `context_window` `DEFAULT_CONTEXT_WINDOW` (12288), the shipped generation
ceiling. Optional configuration B (the model #760 measured): the check on `qwen3:8b` too.
Each configuration has its own verdict; they are never pooled or combined.

## Metrics

Per arm, over the replayed cells:

- **survivors caught**: survivor questions (of 3) abstained in **all** runs.
- **adjacent caught**: adjacent questions (of 10) abstained in all runs.
- **false refusals**: grounded questions (of 10) abstained in **any** run.
- **delivered rate**: share of grounded cells that are not abstained.
- **seconds per query**: replayed wall-clock of one query (the check and synthesis calls
  the arm makes). Reported as mean, p50 and p95, for grounded cells and for all cells.
- **checks per grounded query**: the model-call reduction, reported.

Counts are the unit; there is no judge. Answer text: the conditional arms and `reduced`
deliver **the same stored synthesis reply** the baseline would, so an answer that is
delivered is identical in every arm. Answer correctness is therefore non-inferior by
construction exactly when no extra grounded answer is withheld, which is what B3 reads.
It measures no quality change beyond that, and none is claimed.

## Bars

The constants below are `measure_conditional_sufficiency.py::BARS`; the self-test fails
if this file and the code disagree.

```
RUNS = 5
BOOTSTRAP_SEED = 1740
RESAMPLES = 10_000
LOW_RANK_TOP_K = 2
REDUCED_BLOCKS = 3
CHECK_MEAN_FLOOR_S = 1.0
SURVIVORS_MIN = 3
ADJACENT_DROP_MAX = 0
FALSE_REFUSAL_RISE_MAX = 0
DELIVERED_DROP_MAX = 0.0
LATENCY_RATIO_MAX = 0.85
LATENCY_UPPER_MAX = 0.95
P95_RATIO_MAX = 1.0
FAILED_MAX = 0.15
```

Each treatment against `baseline` (all proposed):

- **B1, the survivors are kept.** Survivors caught is `SURVIVORS_MIN` (3 of 3). This is the
  issue's own bar and the reason this study exists.
- **B2, nothing else is lost.** Adjacent caught is at least the baseline's minus
  `ADJACENT_DROP_MAX` (0): the 7 that attribution already refuses must stay refused.
- **B3, no more withheld answers.** False refusals do not exceed the baseline's by more
  than `FALSE_REFUSAL_RISE_MAX` (0), **and** the delivered rate is not below the
  baseline's by more than `DELIVERED_DROP_MAX` (0.0).
- **B4, the gain.** On grounded cells the ratio (arm seconds over baseline seconds, pooled)
  is at most `LATENCY_RATIO_MAX` (0.85, a saving of at least 15%), **and** its one-sided
  95% upper bound is at most `LATENCY_UPPER_MAX` (0.95). The bound is a seeded cluster
  bootstrap over the 10 grounded questions (`RESAMPLES`, `BOOTSTRAP_SEED`).
- **B5, no worse tail.** The grounded p95 seconds do not exceed the baseline's times
  `P95_RATIO_MAX` (1.0).

Grounded cells carry the gain because they are what users ask; an adjacent query that is
refused is the rare case and is not where the saving is expected.

**Verdicts, per treatment.**

1. `INCONCLUSIVE` when more than `FAILED_MAX` (15%) of the 100 (run, question) cells lost a
   call. Reported, never rescued by more runs.
2. `NO-BASELINE` when the shipped check itself does not abstain on all 3 survivors in all
   runs, in this configuration. Then B1 cannot be read for any arm: the bar "keep the 3"
   presupposes the baseline has them. It is a finding about the check model, not a verdict
   on the arms.
3. `NO-EXPOSURE` when the mean full-check time on grounded cells is under
   `CHECK_MEAN_FLOOR_S` (1.0 s): there is nothing worth skipping.
4. `SUPPORTED` when B1 to B5 all hold, otherwise `REJECTED` naming the failed bars.

Stops 2 and 3 read the check rows alone, so they fire **before any synthesis call**
(`checks` prints them and `synth` refuses to start). A failed or timed-out call is
counted, not re-run. The three treatments share the paired cells, so no correction for
multiplicity is applied and none is needed: each is a separate yes/no against the same
baseline, with no arm chosen by its result. If more than one is `SUPPORTED`, the report
names the one with the lower grounded mean seconds and the owner decides.

## Expectations, stated before the run

- `cond_unattributed` is expected to be `REJECTED` on B1. By #760's own table the survivors
  emit a `USED:` line citing 2 to 5 blocks, so this gate stays shut on them. It is
  registered because it is the issue's literal proposal and because its B1 failure is the
  measurement of how much of the check's value is invisible to attribution.
- `cond_lowrank` can pass B1 only if the survivors cite a block ranked 3 or lower. That is
  unknown. Whether grounded answers also cite such blocks decides B4. The rank line is
  untuned.
- `reduced` is expected to trade latency against false refusals: a grounded answer whose
  quotation sits in block 4 or 5 is refused. B3 is the bar that reads it.

## Cost forecast

Sources: the issue (about 15.3 s per query, about 9.6 s of it the check) and
`evals/query_sufficiency/README.md` (median 1.12 s on `qwen3:8b`). The full check on the
26B model is the long pole.

| Step | Cells | Low | Typical | High |
|---|---|---|---|---|
| `contexts` (embedder, 14 documents, 20 questions) | 0 chat | 0.03 h | 0.05 h | 0.10 h |
| `checks`: 100 x (full 6 to 14 s, reduced 3 to 8 s) | 200 calls | 0.25 h | 0.35 h | 0.55 h |
| `synth`: 100 x (4 to 12 s) | 100 calls | 0.12 h | 0.15 h | 0.35 h |
| model loads (about 2 min each, two) | | 0.03 h | 0.07 h | 0.07 h |
| **Total, primary configuration** | | **0.4 h** | **0.6 h** | **1.1 h** |
| Optional configuration B (checks only, `synth.json` is reused) | 200 calls | 0.2 h | 0.3 h | 0.5 h |

If stop 2 or 3 fires, only `contexts` and `checks` ran: about 0.4 h at most.

**Hard cap: 2 GPU hours** across both configurations; after that the run stops and the
verdict is read on what exists (missing cells are INCONCLUSIVE). Ollama serializes, so
this is one job at a time. The GPU is reserved by the owner: ask before the first model call.

## Exact commands

One Ollama job at a time. Record `git rev-parse HEAD` and `ollama list` first. `OUT`
defaults to `~/Dev/Projects/openkos-eval-runs/conditional-sufficiency` (outside the
repository); use `--out` to keep configuration B apart.

```sh
M=evals/conditional_sufficiency/measure_conditional_sufficiency.py

# model-free, anywhere
OLLAMA_HOST=127.0.0.1:1 uv run python $M --self-test

uv run python $M contexts                       # embedder only
uv run python $M checks --check-model gemma4:26b-a4b
# read the phase-1 stop it prints; continue only if it is None
uv run python $M synth --synth-model qwen3:8b

# model-free from here
OLLAMA_HOST=127.0.0.1:1 uv run python $M score
OLLAMA_HOST=127.0.0.1:1 uv run python $M decide
```

`score` prints every metric and applies no bar; `decide` applies them. The identity stamp
(harness commit, model digest, hash of the prompts sent) is stored in `checks.json` and
`synth.json`.

## Outputs

`RESULTS.md` in this directory: the commit, model digests, cells run and failed, the table
of every metric per arm, each bar per treatment, the verdicts, the p50 and p95 the issue
asks for, and the cost in seconds against the forecast. The verdict updates #1740 and
nothing else.

## Disclosed before scoring

- No model has been run under these arms. The self-test checks the gating predicate, the
  replay, the scoring, the bars, the reduced-context builder, and the call order against a
  stub that never touches the network.
- **Latency is replayed, not observed end to end.** Seconds come from warm, phase-separated
  calls. In production the check model and the answer model alternate per query, and on a
  host that cannot hold both (about 24 GiB plus about 5 GiB) every query also pays a model
  swap that this reading omits. Skipping a check would then save more than reported, never
  less; `reduced` and the gated arms are compared on the same footing, but the absolute
  figures are an underestimate of the issue's 15.3 s.
- **The gate is evaluated on the reply that was produced anyway.** A conditional
  arm that refuses after synthesis wastes the synthesis; on adjacent queries it is slower
  than the baseline by design. B4 and B5 read grounded cells only, and the all-cells
  figures are reported so that cost is visible.
- **Thresholds are untuned.** `LOW_RANK_TOP_K` and `REDUCED_BLOCKS` were not chosen from
  data. `#760` found no relevance floor on retrieval signals; the gate here is not a floor
  on refusal (a shut gate only skips a check) but it inherits that doubt.
- The survivors are the 3 questions named by #760 on a 10-document version of the corpus
  (README, continuity note). On the 14-document corpus another question could survive and
  one of the 3 might not. The bars use the listed 3, fixed; NO-BASELINE covers the case
  where the baseline misses one.
- `qwen3:8b` answers; one corpus, one machine, 20 questions, 5 runs. Spanish only; the
  English-bundle variant (#1560) is not measured. The AMI corpus of the issue is not used,
  so the saving on long meeting contexts is not measured; a longer context makes the check
  costlier, so the saving there should be larger, not smaller.
- `USED:` compliance is a property of the answer model; a model that rarely emits the line
  turns `cond_unattributed` into the baseline plus the synthesis.

## The owner confirms

Confirmed by the owner on 2026-11-09, every item below as proposed, with two answers
recorded: configuration B **runs** (item 6), and the AMI setting is **conditional** (item 7):
a `SUPPORTED` verdict here enables no default change and no new flag until a confirmation
on the AMI setting has been registered and read. If every treatment is `REJECTED` here, no
AMI registration follows. The GPU is free for the run once this file has merged (item 8).

1. The question and the three treatments: `cond_unattributed` (the literal proposal,
   expected REJECTED), `cond_lowrank`, `reduced`; and that the first is worth running
   knowing it is expected to fail B1.
2. The thresholds, as untuned judgment calls: `LOW_RANK_TOP_K = 2` (of 5) and
   `REDUCED_BLOCKS = 3` (of 5).
3. The replay design: one stored (full check, reduced check, synthesis) triple per
   (question, run), arms computed over it, warm and phase-separated, with latency
   therefore modeled rather than observed end to end.
4. The definition of "abstained" (check refusal or an explicitly empty `USED:`) and of
   "answer correctness non-inferior" as no extra withheld grounded answer, with no judge,
   because delivered answers are identical across arms.
5. The bars: B1 3 of 3 survivors in all runs; B2 no drop of the other adjacent; B3 no rise
   in false refusals and no drop in the delivered rate; B4 a saving of at least 15% on
   grounded cells with a bootstrap upper bound of at most 0.95; B5 grounded p95 not above
   the baseline; the INCONCLUSIVE floor (15% of cells), `NO-BASELINE` and `NO-EXPOSURE`
   at 1.0 s as stops that end the run before synthesis.
6. The data and models: the #760 corpus and its 20 questions, 5 runs, check on
   `gemma4:26b-a4b`, answer on `qwen3:8b`, window 12288; and whether optional
   configuration B (check on `qwen3:8b`) should also run, as a separate verdict.
7. Whether the AMI setting of the issue should be a second registration, since this one
   cannot say what the saving is on real meeting contexts.
8. GPU time: the forecast (0.4 to 1.1 h, typical 0.6 h, plus 0.2 to 0.5 h for B), the 2 h
   hard cap, and when the GPU is free (it is reserved by the owner).
