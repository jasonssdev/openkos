# Pre-registration: a chat model as a pointwise reranker over a widened pool (#1651)

**Status: Draft. Registered when this file merges to main; no model run before the
merge.** A change after the merge is a new registration, not an edit. Part of #1652.
Nothing in this file has been run: no chat model was contacted. The owner-confirm
items below were confirmed at their recommended defaults on 2026-10-28 under the
owner's standing delegation.

## The question

Fusion of the lexical and dense ranks can leave the right document just below the
display cut. A reranker over a wider pool is the standard fix. #1651's feasibility
note found that Ollama has no rerank endpoint, that `llama-server` serves `/rerank`
(a second local server), and that a chat model used as a pointwise reranker already
fits the `LLMBackend.chat` seam at the price of one call per candidate.

The owner approved measuring the **cheapest path first**: `qwen3:8b`, the shipped
model, scoring each candidate on its own. It needs no dependency, no server and no
change under `src/`. If it shows a large gain, a later registration may try
`llama-server` with `bge-reranker-v2-m3` as an opt-in. If it shows none, that is a
bounded finding: it says the cheapest path does not pay, and it does not say a
purpose-trained cross-encoder would not.

Does reranking the top 30 (and, exploratory, the top 50) of the fused order with a
pointwise `qwen3:8b` score raise nDCG@5 on the v1 gold set by a margin that would
justify the next step?

## Data

The v1 gold bundle built by the production `ingest` (#1609), `questions.v1.json`
(56 questions, hash `22baff7b...0271d60d`) and the committed `judgments-v1.json`
(955 labels). Nothing from `evals/decision_extraction/`, no held-out or reserve
series, and never the 14 downstream questions of #1652, which are evidence for the
problem and not a test set. A held-out confirmation is a separate registration.

## Arms

All arms read one `wide-run` file, so they share the retrieval and differ only in
the order they hand to the shipped display rule (`fusion.select_top`, the Source cap).

| Arm | Order handed to `select_top` |
| --- | --- |
| **A0** shipped | The fused order the production `answer()` builds from depth-10 channel lists |
| **A1** wide control | The fused order over depth-50 channel lists, not reranked |
| **R30** (primary) | The first 30 of the A1 order, reranked by the model score |
| **R50** (exploratory) | The first 50 of the A1 order, reranked by the model score |

A1 exists because widening the lists changes the RRF order by itself. Without it, a
reranker would be credited for what the wider lists did.

### The reranker, fixed now

- **Model:** `qwen3:8b` through the production `OllamaClient` (`think: False`, the
  production setting), `temperature` 0.0, `seed` 1651, `max_generation_tokens` 32,
  `context_window` 4096. The model digest and the harness commit are stored with
  every calls file.
- **One chat call per candidate.** The score depends only on the question and the
  candidate, so it is computed once per (question, document) and R30 reads the first
  30 of the 50 scored. No batching, no listwise prompt, no retry.
- **Input:** the document title and its stored body cut to **2,000 characters**
  (fixed now, not tuned), and the question. No judgment, no other candidate.
- **Prompt, verbatim.** System message:

```text
You judge whether a document helps answer a question asked of a personal knowledge base. Rate how well the document answers the question with an integer from 0 to 10: 0 means unrelated, 3 means the same topic but it does not contain the answer, 6 means it contains part of the answer, 10 means it states the answer directly. Judge only the document text given. Reply with a JSON object with exactly one key and nothing else: {"score": N}
```

  User message:

```text
Question: {question}

Document title: {title}

Document:
{body, first 2000 characters}
```

- **Parsing, fail-closed.** `llm/parsing.extract_json_object`, then exactly one key
  `score` holding an integer from 0 to 10. A string, a float, a bool, a second key, an
  out-of-range value, a backend error or no object is an **unparsed** call: it is
  never repaired and never retried, and the candidate sorts after every parsed one.
- **Order:** score descending, ties broken by the fused rank. Integer scores tie
  often; this keeps the arm deterministic and makes constant scores reproduce A1.
- **Display rule:** the reranked order goes through the same `select_top` the product
  uses. The arms differ in order only.

## Metrics, margins and the decision rule

Primary: **nDCG@5, all 56 questions, R30 minus A0**, paired by question, bootstrap with
the harness (`gold_core.paired_bootstrap`, seed 1609, 10,000 resamples). The bound is
**one-sided 95%**: the 5th percentile of the resampled means, which is the harness
interval at `alpha = 0.10`. A run's per-question value is the mean over its runs.

R30 is **licensed** only if all hold:

| # | Condition |
| --- | --- |
| P | nDCG@5 gain of at least **+0.07**, one-sided 95% lower bound above 0 |
| C | A1's gain over A0 is under half of R30's gain (the wider lists are not the cause) |
| G1 | recall@8, R30 minus A0, no lower than **-0.02** (the display is 8) |
| G2 | vocabulary-gap stratum (n = 17) nDCG@5, R30 minus A0, no lower than **-0.03** |
| G3 | `judged@5` of R30 at least **0.90** (see the pooling caveat); reported always, decisive only when P fails |
| G4 | at most **2%** of the pool-30 calls unparsed; above that the run is **void** |

Also reported: recall@5 and recall@8 on all questions and on the vocabulary-gap
stratum, nDCG@5 for R50 and its paired contrasts against A0 and A1, `judged@5` and
`judged@8` and the count of unjudged documents in the top 8 for every arm, and p50/p95
added latency and total calls. **R50 is exploratory and cannot license anything on its
own.**

Verdicts, in this order: `void` (G4 fails); `licensed` (P, C, G1 and G2 hold);
`inconclusive` (P fails and G3 fails: the miss may be the pool, see below);
`not-licensed` (anything else).

**What a pass licenses, and nothing more:** the later `llama-server` /
`bge-reranker-v2-m3` registration, and drafting the ADR for a `Reranker` seam. **No
default changes, nothing under `src/` changes, no dependency is added.** A
`not-licensed` result closes the cheapest path; the owner may still open the
`llama-server` step on the issue's own grounds. An `inconclusive` result returns to the
owner, who decides whether to pay for a re-pool (below).

Why +0.07: the only contrast the v1 baseline could distinguish from zero at nDCG@5 was
fused over lexical-only, +0.088, and a model call per candidate should not be adopted
for less than a gain of that order. The baseline's paired intervals at nDCG@5 had a
half-width of about 0.045 to 0.05 over 56 questions (two-sided), so an observed +0.07
puts the one-sided lower bound near +0.03: the margin is above the noise with room, and
a gain that only just clears 0 is not what the owner asked for ("a large gain").
Headroom exists: shipped fused nDCG@5 is 0.681.

**One shot.** One prompt, one pool definition, one margin. No second prompt after a
result, no tuned truncation, no extra question set.

## Runs per arm, and why

A0 and A1 are deterministic and one run each. The reranker is the only
stochastic arm, and greedy decoding with a fixed seed is expected to make it nearly
deterministic, though GPU batching can still flip a near-tie. The project's noise
lessons (a five-run arm spanned 0.25 against itself on sampled decoding) set 15 runs as
the floor for a sampled LLM arm, so fewer runs have to be earned:

- **Stage 0, determinism probe:** the first 10 questions, pool 30, scored 3 times
  (900 calls). `gold_rerank.py determinism` reads it. If at least **99.5%** of the
  (question, document) cells score identically in all three **and** at least **98%** of
  the questions have an identical top 8, the regime is **deterministic** and the main
  run is **3 scoring passes** over pool 50 (R30 is the first 30 of each), averaged per
  question. Three rather than one, so determinism is confirmed on the full set.
- **Otherwise:** 15 passes of pool 30 are required. That does not fit the budget below,
  so the owner re-confirms the GPU spend before it starts; R50 then comes from the first
  3 passes only and stays exploratory.

## Forecast and budget

Prefill 299 tokens/s and decode 31.2 tokens/s for `qwen3:8b` (`evals/runtime_screen`,
the figures #1650 priced with). A call is about 120 tokens of system prompt, 25 of
question, 10 of title and up to about 500 to 650 of document (2,000 characters), so
about 500 to 800 prompt tokens and about 8 reply tokens.

| Piece | Basis | Estimate |
| --- | --- | --- |
| One call | prefill 1.7 to 2.7 s, decode 0.3 s, overhead 0.1 to 0.3 s | **2.0 to 3.3 s**, central 2.5 s |
| One pass, pool 50 | 56 x 50 = 2,800 calls | **1.9 to 3.1 h** (central 1.9 h) |
| Stage 0 probe | 10 x 30 x 3 = 900 calls | 0.5 to 0.8 h |
| Main run, deterministic regime | 3 passes = 8,400 calls | **5.8 to 9.2 h** (central 5.8 h) |
| Main run, noisy regime | 15 passes x 1,680 calls = 25,200 calls | 14 to 23 h (central 17.5 h) |
| Embedding for `wide-run` | 56 questions with bge-m3, plus the reindex | minutes |

**Budget: 10 GPU hours for Stage 0 and the main run together.** `score-pool` takes
`--deadline-hours`; reaching it leaves an **incomplete** calls file that `evaluate`
refuses, and the run is reported as incomplete, never extended. A crash resumes from the
last finished question of the same identity. The noisy regime exceeds the budget on
purpose: it needs a fresh decision.

Latency is measured per call and reported as the serial sum for the pool: p50 and p95
over every (run, query), and total calls. The forecast is **about 75 s added per query
at pool 30 (60 to 100 s) and about 125 s at pool 50**. That is not an interactive
latency and is not a pass condition: this arm measures how much quality a reranking
step could add; the latency of the thing that might ship is the later registration's.

## The pooling caveat (reranking can only promote documents already judged)

The judgments were pooled from the top 10 of the three baseline channels. A reranker
over 30 or 50 candidates promotes documents from ranks 11 and below of the wide order,
and most of those were never shown to a judge. **An unjudged document counts as not
relevant in every metric.** Two consequences, both stated before the result:

- The bias is **against** the reranker. A gain that clears P is a lower bound on the
  real gain, so a `licensed` verdict stands even when `judged@5` is low.
- A **miss** over a poorly judged pool proves little. Hence `inconclusive`: P fails and
  `judged@5` is under 0.90. The report prints `judged@5`, `judged@8` and the count of
  unjudged documents in the top 8 for every arm, which is the size of a re-pool. A
  re-pool means blind judgment of those documents by the same procedure as v1; it is a
  separate step the owner authorizes, never part of this run.

A1 carries the same bias, which flatters condition C slightly; it is named so a reader
does not take C as stricter than it is.

## Threats, said now

- **Truncation.** The model sees 2,000 characters. A `Source` embeds the whole raw text,
  so an answer past that point is invisible to the reranker, while the shipped display
  can still surface the document. The Source cap in `select_top` bounds how much that
  costs, and the cut is fixed, not tuned.
- **Ties.** Integer scores tie; ties fall back to the fused rank, so a coarse scorer
  degrades toward A1, not below it.
- **Spanish.** Scoring Spanish questions against Spanish documents has no measurement in
  this repo. The report does not split by language (the set is small); a Spanish-only
  failure would show in the per-question file, not in the verdict.
- **One family judged the set.** Questions were authored and judged by Claude models; a
  Qwen reranker disagreeing with them is read as "the judgments", not as truth
  (`RESULTS-v1-baseline.md`).
- **Small n.** 56 questions, 17 in the stratum. G2 is a no-harm guard, not a claim.
- **Wide lists.** Widening to 50 changes the RRF order, hence A1. The `wide` ranking
  applies the same deprecated and confidential exclusions as the shipped path.

## Harness status

Model-free parts are built under TDD with a stub scorer and a stub embedder, and mutated
(`python evals/retrieval_gold/gold_rerank.py --self-test`): the prompt and its fail-closed
parsing, the reranked order and its ties, the per-`k` display rule, one call per
candidate, per-call timing and the latency report, the resumable `score_pool`, the wide
retrieval (reproduces the shipped fused order at depth 10), the arms, the one-sided
bootstrap, `judged@k`, the decision rule and the determinism probe.

| Piece | State |
| --- | --- |
| `parse_score`, `build_messages`, `rerank`, `arm_rankings`, `latency_report`, `score_pool`, `retrieve_wide`, `evaluate`, `decide`, `determinism` | built, `--self-test` (this PR) |
| `wide-run`, `score-pool` command wiring against Ollama | built, **not exercised against a model** |

## Run sequence

Every output is written outside the repository.

```bash
W=~/openkos-eval-runs/retrieval-gold
H="uv run python evals/retrieval_gold/gold_rerank.py"

# 0. no chat model: the shipped channels plus the depth-50 fused order (bge-m3 embeds the questions)
$H wide-run --workspace $W/bundle-v1 --out $W/wide-run-v1.json

# 1. Stage 0, determinism probe: three passes over the first 10 questions, pool 30 (GPU, about 0.5-0.8 h)
for i in 1 2 3; do
  $H score-pool --workspace $W/bundle-v1 --wide-run $W/wide-run-v1.json \
      --pool 30 --first 10 --run-index $i --out $W/rerank-probe-$i.json
done
$H determinism --calls $W/rerank-probe-1.json $W/rerank-probe-2.json $W/rerank-probe-3.json

# 2a. deterministic regime: three passes over pool 50 (GPU, about 6-9 h; --deadline-hours caps each command)
for i in 1 2 3; do
  $H score-pool --workspace $W/bundle-v1 --wide-run $W/wide-run-v1.json \
      --pool 50 --run-index $i --deadline-hours 3.5 --out $W/rerank-calls-$i.json
done
# 2b. noisy regime: stop; the owner re-confirms the GPU budget for 15 passes of pool 30

# 3. no model: arms, contrasts, latency, the verdict
$H evaluate --wide-run $W/wide-run-v1.json \
    --calls $W/rerank-calls-1.json $W/rerank-calls-2.json $W/rerank-calls-3.json \
    --out $W/rerank-result-v1.json
```

The verdict and the tables go in a results file next to this one
(`RESULTS-1651.md`) with the calls files' model digest and harness commit.

## Owner confirms (confirmed 2026-10-28 under the owner's standing delegation)

All items below are confirmed at the recommended default (the first option).

1. **Reranker:** `qwen3:8b`, the pointwise 0-to-10 prompt above, 2,000-character
   documents. Alternative: a yes/no prompt, or a 0-to-3 scale (coarser, more ties).
2. **Pools:** 30 is primary; 50 is exploratory and cannot license anything alone.
3. **Margin:** +0.07 nDCG@5 on all 56 questions, one-sided 95% lower bound above 0.
   Alternative: +0.05, which a 56-question set cannot separate from noise as reliably.
4. **Runs and budget:** Stage 0 decides between 3 passes and 15; the budget is 10 GPU
   hours; the noisy regime returns to the owner.
5. **Pooling:** unjudged is not relevant; no re-pool inside this registration. A miss
   over a poorly judged pool is `inconclusive` and goes back to the owner.
6. **What a pass licenses:** the `llama-server` / `bge-reranker-v2-m3` registration and
   an ADR draft, nothing else. No default change, no `src/` change.
