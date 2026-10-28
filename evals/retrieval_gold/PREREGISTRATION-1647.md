# Pre-registration: do query-aware excerpts raise delivered evidence? (#1647)

**Status: registered before any `delivered` number was produced.** No `delivered` command has been run on this question set, with either setting, at the time of writing; the annotation below was written blind to every delivery output. A change to anything below after the run starts needs a new pre-registration and a re-run of both arms. This file is under umbrella [#1652](https://github.com/jasonssdev/openkos/issues/1652); the metric is [#1646](https://github.com/jasonssdev/openkos/issues/1646) (`gold_delivery.py`), the gold set [#1609](https://github.com/jasonssdev/openkos/issues/1609).

## Question

When a retrieved document is longer than its share of the context window, does choosing the excerpt windows by BM25 similarity to the question (`query_aware_excerpts: true`, [#1647](https://github.com/jasonssdev/openkos/issues/1647), shipped default off) put more of the gold passage into the context the model reads than the shipped even coverage does?

Only the choice of windows changes. The window size (`_CONTEXT_CHUNK_TARGET = 800`), the budget, the `fair_shares` split and the elision marker are the same in both arms, and a text that fits its share is byte-identical in both. Retrieval is identical in both arms (one stored run), so `retrieved` is identical and any difference is trimming.

## What is measured, exactly

The arms differ in one argument of the shipped seam, `answer._assemble_context(..., query_aware_excerpts=...)`, driven by `run_retrieval_gold.py delivered` (`--query-aware` for the candidate arm). No model is contacted: no chat call, no embedding call. The run is verified with `OLLAMA_HOST` set to an unreachable address.

- **Corpus.** The built v1 workspace `bundle-v1` (`retrieval-gold-v1`), read through `--workspace`, never written. The ranking is the stored `run-v1.json` (embedder `bge-m3`, harness commit `a26f60d`), channel `fused`, k in {5, 8, 10}.
- **Questions.** `questions.v1.json` (56 questions: 35 English and 21 Spanish; the file is the authority).
- **Window.** The harness defaults, which are also the shipped configuration: context window 12,288 tokens, reply reserve 8,192 tokens (`DEFAULT_CONTEXT_WINDOW`, `DEFAULT_MAX_GENERATION_TOKENS`). One setting; no sweep.
- **Metric (per question, 0 or 1).** `delivered` as defined in `gold_delivery.py`: every annotated gold document has at least one gold span, as a contiguous substring, in its final context block. A question's value at `k` is taken from `fused@k`.

## The gold passages: how they were written

- **Annotation unit.** The documents the adjudicated judgments (`judgments-v1.json`) grade 2. Grade-1 documents are not annotated: they are partial and a span would be a guess. **54 of 56 questions have at least one grade-2 document and are scored; `q01` and `q10` have none and are not scored** (named, not hidden, in the results). 136 grade-2 documents, 180 spans.
- **Blind.** The sheet (`passage-sheet`) carries no retrieval result, rank, channel or context window. The spans were written before `delivered` had been run even once, with either setting.
- **Deviation from a human annotator.** The spans were written by Claude models (eight parallel Claude Sonnet workers, one slice of the sheet each, by their own reading of the documents) on the owner's delegation. The maintainer session ran only the mechanical verbatim check (`passages`). They are therefore **unreviewed model-authored annotations**, not a human judgment of what answers the question. The risk this carries is that a model prefers spans that are lexically close to the question, which could favour a BM25-based arm. Mitigation: the instruction was the shortest span holding the key fact, copied verbatim, with no knowledge of either arm; and the lexical-overlap bias is read against the vocabulary-gap stratum below, where it is weakest.
- **Frozen.** `passages-v1.json`, sha256 `75caf1bd3caa2433369a8d46c5112c7070dde790121018a5848ff013fd1b5546`, produced by `passages` (every span verified verbatim, the question set hash checked). It stays outside the repository, like the judgments: the commands refuse a path inside it.
- **Known limits of the contract.** (a) Several spans of one document are alternatives: the document is delivered if ANY span is in its block. Where a model gave several spans because the answer has several parts (for example a three-item list in `q35`, `q51`, `q54`, `q55`), the metric credits the document when one part arrives. That favours both arms alike and makes `delivered` an upper bound for list questions. (b) A question needs EVERY annotated grade-2 document, so a many-document question is harder to deliver whole. (c) A span that carries only half of a two-part answer is accepted as written.

## Hypothesis and test

**H1.** The query-aware arm delivers more gold evidence than even coverage: the mean paired difference in per-question `delivered` (candidate minus baseline) is greater than 0.

- **Primary metrics.** `delivered@8` (the shipped query limit) and `delivered@5`. `k = 10` is reported, not decided on.
- **Test.** Paired bootstrap over the scored questions with the harness bootstrap (`core.paired_bootstrap`: questions resampled with replacement, 10,000 resamples, seed 1609), **one-sided 95%**: the lower bound is the 5th percentile of the resampled mean difference (the harness bootstrap at two-sided `alpha = 0.10`, its `lo`). Computed by `run_retrieval_gold.py delivered-compare`.
- **Success (all of):** at `k = 8` the lower bound is above 0; at `k = 5` the lower bound is above 0; and at each of those `k` no language stratum (`en`, `es`) has a mean paired difference below -0.03.
- **Exposure rule.** If the baseline loses fewer than 5 scored questions to `trimmed` at `k = 8`, the test could not have moved and the verdict is **NO EXPOSURE**, never shippable and never refuted. The count is printed with the result as `n of N`.
- **Verdicts.** SUCCESS (all of the above). FAILURE (otherwise, with the lower bounds and the language strata stated). NO EXPOSURE (as above).

## Secondary measures (reported, no decision rests on them)

- Mean paired difference and counts by `kind` (single-hop, multi-hop, enumeration) and by language, each as `n of N` baseline and candidate.
- The vocabulary-gap stratum: questions with `vocab_gap: true` against the rest. A gain only on questions whose words appear in the passage, and none on the vocabulary-gap ones, would show the arm rewarding the annotators' lexical bias rather than finding evidence.
- Loss counts by stage per arm (`not_retrieved`, `omitted`, `trimmed`). `not_retrieved` and `omitted` cannot change between arms except through the budget; a change in `omitted` is a regression to name. The number of questions that improve and that regress.
- `any` (at least one gold document delivered) and `retrieved` next to `delivered`.

## Decision rule

- **SUCCESS** licenses an ADR proposing `query_aware_excerpts` default on. This work changes no default; the ADR is a separate change, under the repository's ADR process, and may still decline on the secondary evidence (a regression by kind, no gain in the vocabulary-gap stratum).
- **FAILURE** leaves the default off and the flag as it is; the results say why.
- **NO EXPOSURE** is reported as an unfalsifiable test, with the missing exposure named.

## What this does not show

- Not answer quality: no chat model is in the loop. A delivered passage can still be misread, and a trimmed one can be inferred from another block.
- One window setting, one bundle, development series only; no thesis evaluation or held-out series is read.
- The excerpt scorer is BM25 over 800-character windows (the shipped implementation); the dense variant of #1647 is not under test.
- Model-authored annotations, as above.

## Commands (frozen)

```bash
W=~/openkos-eval-runs/retrieval-gold
# no model; poisoned server so a stray call fails loudly
OLLAMA_HOST=127.0.0.1:9 uv run python evals/retrieval_gold/run_retrieval_gold.py delivered \
    --workspace $W/bundle-v1 --run $W/run-v1.json --passages $W/passages/passages-v1.json \
    --out $W/passages/delivered-off.json
OLLAMA_HOST=127.0.0.1:9 uv run python evals/retrieval_gold/run_retrieval_gold.py delivered \
    --workspace $W/bundle-v1 --run $W/run-v1.json --passages $W/passages/passages-v1.json \
    --query-aware --out $W/passages/delivered-on.json
uv run python evals/retrieval_gold/run_retrieval_gold.py delivered-compare \
    --workspace $W/bundle-v1 --base $W/passages/delivered-off.json --cand $W/passages/delivered-on.json
```
