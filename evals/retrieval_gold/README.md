# `retrieval_gold`: a retrieval gold set and a model-free harness

**Question.** How good is retrieval itself, apart from the chat model? Nothing in
`evals/` computed recall@k, MRR or nDCG against judged relevant documents; retrieval
was measured only through answers and citations (`query_citation`,
`query_sufficiency`, `query_attribution`), through paraphrase stability
(`retrieval_stability`) and through the display limit (`query_limit`). This harness
gives a retrieval technique a number that has no chat model in it
([#1609](https://github.com/jasonssdev/openkos/issues/1609)).

This directory holds the **harness and a DRAFT question set**. There are no relevance
judgments yet, so there is nothing to score and no baseline. The judgments come from
the blind sheet below.

## What it measures

The harness builds the bundle's derived stores through the production `reindex`, then
reads each channel through the same functions `answer()` calls. No production code
changed.

| Channel | Production seam | Notes |
| --- | --- | --- |
| `fts` | `answer._fts_search(index, answer._fts_query_terms(q), limit=10)` | Function words dropped (#648). |
| `dense` | `answer._dense_search(q, embedder, store, pool_limit=10)` | Embeds the full question. A degraded dense channel stops the run instead of scoring as zero. |
| `fused` | `fusion.select_top(fusion.fuse(fts, dense), k)` | The shipped order, computed once per `k` because the Source-share cap in `select_top` depends on `k`. |

`fts` and `dense` are read as one top-10 list and cut at `k`; they have no production
cut of their own. The deprecated and confidential filters `answer()` applies are applied
first. `--self-test` checks that the `fused` channel equals what `answer()` cites, at
every `k`.

Metrics, per channel, per question language, and per question kind:

- `recall@k` for k = 5, 8, 10. A document is relevant at grade 1 or 2.
- `mrr`, the reciprocal rank of the first relevant document in the top 10 (MRR@10).
- `ndcg@k` with graded relevance 0/1/2 and a linear gain.

`compare` runs a seeded paired bootstrap over questions for any two scored runs. The
exact definitions are in the module docstring of `gold_core.py`.

## The bundle and the questions

The bundle is committed material only, so a fresh clone rebuilds the same 35
documents: the 20 English notes of `evals/quiet_engine/corpus/sources/` (synthetic, and
stored as `Source` documents), the Spanish meetings, concepts and decisions of
`evals/query_grounding/grounding_corpus.py`, and the Spanish meeting
`examples/extraction-corpus/sources/medium-10-reunion-plataforma.md`. See
`gold_bundle.py` for why the English twin of the Spanish corpus is left out.

`questions.draft.json` has 36 questions: 22 English and 14 Spanish; single-hop,
multi-hop and enumeration. Each records its `language` and `kind`. It carries no
judgments, and the draft status means the questions may still change; a change
invalidates judgments written against the old set (the files carry a hash of it).

## What it cannot show

- **Pool bias.** Judging every document for every question is not feasible, so a
  judge grades only the *pool*: the union of the top 10 of each channel. A document
  that was never in a pool is unjudged and **counts as not relevant**. The consequence
  is one-sided. A technique that retrieves a relevant document no pooled channel
  retrieved is scored as if it had retrieved noise, so new techniques are penalized
  against the channels that built the pool. `score` prints `judged@10`, the share of
  each channel's top 10 that has a judgment. Read a number only next to it, and
  re-pool (a new sheet, judged on the new candidates only) when a candidate's
  `judged@10` is low.
- **The `fts-only` pool.** Until embeddings can run, the pool is the lexical top 10
  alone. Judgments written from it say nothing about documents only the dense channel
  finds, so they favour `fts` over `dense` and `fused`. The sheet header and the
  judgments file both name the pool mode; do not report a dense or fused number
  against an `fts-only` pool as a baseline.
- **A 35-document bundle.** A top 10 is 29% of the bundle, so recall@10 is easy and
  ranks mostly by who is first. The set discriminates order, not reach, until a larger
  bundle replaces it.
- **Two languages, one domain per language.** English questions run over a hardware
  project and Spanish ones over meetings; cross-language retrieval is not measured.
- **No answer quality, no abstention.** Every question is answerable from the bundle.
- **Not a pre-registration.** The baseline and every candidate technique need their own
  pre-registration (arms, metric, bar, `n`) before any run. This directory has none.

## Commands

Run everything from the repository root. Paths for sheets, keys, runs and judgments must
be **outside** the repository; the harness refuses a path inside it.

```bash
# No model contacted. Also run by `uv run python evals/run_self_tests.py`.
uv run python evals/retrieval_gold/run_retrieval_gold.py --self-test

# Blind judgment sheet from the lexical pool (no model, works today).
uv run python evals/retrieval_gold/run_retrieval_gold.py judge-sheet \
    --out ~/openkos-eval-runs/retrieval-gold/sheet-fts.md
# The key (opaque candidate code -> document) is written next to it as
# `sheet-fts.md.key.json`. It holds no retrieval path; keep it away from the
# adjudicator anyway.

# Retrieve every question (needs Ollama serving bge-m3; zero chat calls).
uv run python evals/retrieval_gold/run_retrieval_gold.py run \
    --out ~/openkos-eval-runs/retrieval-gold/run-baseline.json
# Lexical only, no model: add --fts-only.

# Full pool: top 10 of fts, dense and fused, from a run with embeddings.
uv run python evals/retrieval_gold/run_retrieval_gold.py judge-sheet --pool full \
    --run ~/openkos-eval-runs/retrieval-gold/run-baseline.json \
    --out ~/openkos-eval-runs/retrieval-gold/sheet-full.md

# A second adjudicator's sheet on a seeded sample of the questions.
uv run python evals/retrieval_gold/run_retrieval_gold.py judge-sheet --pool full \
    --run ~/openkos-eval-runs/retrieval-gold/run-baseline.json --sample 12 \
    --seed 4242 --out ~/openkos-eval-runs/retrieval-gold/sheet-second.md

# Filled sheet -> judgments; with --second, Cohen's kappa (plain and weighted).
uv run python evals/retrieval_gold/run_retrieval_gold.py adjudicate \
    --sheet ~/openkos-eval-runs/retrieval-gold/sheet-full.md \
    --second ~/openkos-eval-runs/retrieval-gold/sheet-second.md \
    --out ~/openkos-eval-runs/retrieval-gold/judgments.json

# Score a run, then compare two scored runs (seeded paired bootstrap).
uv run python evals/retrieval_gold/run_retrieval_gold.py score \
    --run ~/openkos-eval-runs/retrieval-gold/run-baseline.json \
    --judgments ~/openkos-eval-runs/retrieval-gold/judgments.json \
    --out ~/openkos-eval-runs/retrieval-gold/scores-baseline.json
uv run python evals/retrieval_gold/run_retrieval_gold.py compare \
    --base ~/openkos-eval-runs/retrieval-gold/scores-baseline.json \
    --cand ~/openkos-eval-runs/retrieval-gold/scores-candidate.json \
    --channel fused --metric ndcg@10
```

`adjudicate` rejects a sheet with an unlabelled, unknown or out-of-range entry and
writes nothing. `score` rejects a judgments file with an unknown document id, a missing
question, a label outside {0, 1, 2}, a question with no relevant document, or a stale
question set, and scores nothing. Disagreements between two adjudicators are listed to
be resolved by hand; the command never merges them.

## Files

| File | Role |
| --- | --- |
| `run_retrieval_gold.py` | Retrieval through the production seams, the commands, `--self-test`. |
| `gold_core.py` | Metrics, bootstrap, kappa, the question and judgment contracts, the sheet. Pure. |
| `gold_bundle.py` | The bundle, built from committed material. |
| `questions.draft.json` | The draft question set; no judgments. |
