# Pre-registration: a cross-encoder reranker served by an opt-in llama-server (#1651)

**Status: Draft. Registered when this file merges to main; no model run before the
merge.** A change after the merge is a new registration, not an edit. Part of #1652.
Nothing in this file has been run: no reranker model was downloaded or contacted, and
no number from it exists. It follows [`PREREGISTRATION-1651.md`](PREREGISTRATION-1651.md)
(the pointwise `qwen3:8b` arm) and changes only the scorer; everything it does not
restate is that file's. The owner decided on 2026-10-28 that an optional, explicitly
opt-in `llama-server` may serve a reranker as a second local runtime
([ADR-0083](../../docs/adr/0083-a-reranker-may-be-served-by-an-opt-in-llama-server-as-a-second-local-runtime.md),
Proposed); adopting reranking still depends on this measurement.

## The question

The pointwise arm asks whether a chat model can rerank, at the price of one call per
candidate (about 75 s per query forecast). A cross-encoder is the purpose-built
alternative: one small model that reads the question and a document together and
returns a calibrated relevance score, with the whole pool scored in one request.

Does reranking the top 30 (and, exploratory, the top 50) of the fused order with
`bge-reranker-v2-m3` raise nDCG@5 on the v1 gold set by the same margin as the
pointwise registration, **at an added latency a `query` could carry, without harming
the Spanish questions**?

## Data

Unchanged from the pointwise registration: the v1 gold bundle built by the production
`ingest` (#1609), `questions.v1.json` (56 questions, hash `22baff7b...0271d60d`; 35
English, 21 Spanish) and the committed `judgments-v1.json`. Nothing from
`evals/decision_extraction/`, no held-out or reserve series, and never the 14
downstream questions of #1652. A held-out confirmation is a separate registration.

## Arms

The same four arms over the same `wide-run` file, so the retrieval is shared and only
the order handed to the shipped display rule (`fusion.select_top`) differs.

| Arm | Order handed to `select_top` |
| --- | --- |
| **A0** shipped | The fused order the production `answer()` builds from depth-10 channel lists |
| **A1** wide control | The fused order over depth-50 channel lists, not reranked |
| **R30** (primary) | The first 30 of the A1 order, reranked by the cross-encoder score |
| **R50** (exploratory) | The first 50 of the A1 order, reranked by the cross-encoder score |

In this registration `R30` and `R50` mean the cross-encoder; the pointwise arm's calls
files are never mixed in. Order: score descending, ties broken by the fused rank. A
request that fails leaves every candidate of that query unparsed, and unparsed
candidates sort after every scored one in fused rank (the pointwise rule).

### The reranker, fixed now

- **Model:** `bge-reranker-v2-m3` (Apache-2.0, 0.6B, multilingual), the **Q8_0 GGUF**
  (`bge-reranker-v2-m3-Q8_0.gguf`, from the `gpustack/bge-reranker-v2-m3-GGUF`
  repository). **The sha256 of the file is computed by `score-pool` and stored in the
  identity of every calls file**; it is not known before the download, so it is
  recorded at run time and quoted in the results file. A different file is a different
  registration.
- **Server:** the installed `llama-server` **version 0.7.0 (build 11669)**, started
  by the operator, on loopback, with exactly these flags:

```bash
llama-server -m bge-reranker-v2-m3-Q8_0.gguf --reranking \
    --host 127.0.0.1 --port 8081 -c 8192 -np 4 -b 2048 -ub 2048
```

  `--reranking` (alias `--rerank`) enables `/v1/rerank`; pooling is the model's own
  (`rank`). No sampling flag applies. The `--version` line and the server's `/props`
  reply are stored with the results. A different version or flag set is a deviation to
  report, not a silent change.
- **Endpoint:** `POST /v1/rerank` with `{"model", "query", "documents", "top_n"}`; the
  reply holds `results` with an `index` and a `relevance_score` per document, **best
  first, so the index is the only key** and the order of the reply carries no meaning.
  This is the Jina shape, **not** an OpenAI-compatible endpoint. The harness uses the
  standard library (`urllib`) only; nothing is added to the dependencies.
- **One request per query**, carrying the whole pool of 50. The score of a document
  does not depend on its neighbours, so R30 reads the first 30 of the 50 scored. No
  batching across queries, no retry.
- **Input:** the document title, a blank line, then the stored body cut to **2,000
  characters**: the same cut as the pointwise arm, so the arms differ in the scorer
  and not in what it sees.
- **Parsing, fail-closed.** Every index `0..n-1` exactly once, each `relevance_score`
  a finite number (a bool, a string, a null or a missing field is not one). Anything
  else, an HTTP error (including a document the server refuses for size) or a refused
  connection leaves the **whole query unparsed**. Nothing is repaired or retried.
- **Warm-up.** One request on a throw-away input that is not from the gold set is sent
  before the first query and is not timed, so the model load is not billed to query 1.
  It is also the wiring check: a server that is not in reranking mode refuses it.

## Truncation, stated and counted

A bidirectional reranker must fit the question and one document in `--ubatch-size`,
and the document is cut by the harness before it is sent.

- **Document cut:** 2,000 characters, fixed now and not tuned.
- **`--ubatch-size`:** 2,048 tokens (with `-b 2048`, and `-c 8192 -np 4` giving each
  slot 2,048 tokens). 2,000 characters of English is roughly 500 to 650 tokens, of
  Spanish somewhat more; 2,048 leaves headroom for the question and the title.
- **Counted and reported, not decisive:** the pairs scored, the **pairs whose body was
  cut** and the **distinct documents cut** (`truncation_report`, stored in every calls
  file), and the pairs left unparsed. A request the server refuses for size shows as
  unparsed pairs and is bounded by G4.
- **Why a cut matters here:** an answer past character 2,000 is invisible to the
  reranker while the shipped display can still surface the document, as in the
  pointwise arm. The report prints the share of cut pairs so a miss can be read
  against it.

## Metrics, margins and the decision rule

The primary test is the pointwise registration's, unchanged: **nDCG@5, all 56
questions, R30 minus A0**, paired by question, bootstrap with the harness
(`gold_core.paired_bootstrap`, seed 1609, 10,000 resamples), bound **one-sided 95%**.

R30 is **licensed** only if all hold:

| # | Condition |
| --- | --- |
| P | nDCG@5 gain of at least **+0.07**, one-sided 95% lower bound above 0 |
| C | A1's gain over A0 is under half of R30's gain |
| G1 | recall@8, R30 minus A0, no lower than **-0.02** |
| G2 | vocabulary-gap stratum (n = 17) nDCG@5, R30 minus A0, no lower than **-0.03** |
| G3 | `judged@5` of R30 at least **0.90**; reported always, decisive only when P fails |
| G4 | at most **2%** of the pool-30 pairs unparsed; above that the run is **void** |
| **L** | pool-30 **p95 added latency per query at most 2.0 s** (new) |
| **S** | **Spanish stratum** (n = 21) nDCG@5, R30 minus A0, no lower than **-0.03** (new) |

Verdicts, in this order: `void` (G4 fails); `licensed` (P, C, G1, G2, L and S hold);
`inconclusive` (P fails and G3 fails); `not-licensed` (anything else, including a
result that clears P, C, G1 and G2 but fails L or S).

### The latency bar (L), and why 2.0 s

Added latency is the wall-clock time of the `/v1/rerank` request over loopback
(connection, JSON, the model), measured per request and **shared equally among the
candidates**, so a pool-30 figure is 30/50 of a pool-50 request. That assumes the
cost is linear in documents; it is stated, not tested. p50 and p95 are taken over
every (pass, query), as in the pointwise latency report.

The bar is **absolute, not a ratio**, because the model-free pipeline's own time is
small next to the answer model that follows it, so a ratio to it would reject any
reranker. It is fixed at **2.0 s at p95** for three reasons: (1) it is below the
**2.5 s central cost of a single pointwise chat call** in the pointwise forecast, so
the cross-encoder must beat one call of the arm it replaces, not thirty; (2) the
answer model's own prefill alone is seconds at the measured 299 tokens/s, so a
reranking step of the same order is a visible but tolerable addition, and one an order
of magnitude larger is not; (3) a number fixed before the run cannot be argued up after
a good nDCG. It is measured on the same machine, in one session, with no other model
resident on the GPU. The warm-up is excluded; the first-load time is reported separately.

### The Spanish guard (S)

The set is 35 English and 21 Spanish questions. The pointwise registration did not
split by language; for a model sold as multilingual that is the case that matters, and
a Spanish-only regression would be invisible in an all-questions mean. **S** is a
no-harm guard on the Spanish questions: the paired mean of R30 minus A0 nDCG@5 over the
21 must be no lower than **-0.03**, the floor G2 uses for its stratum. With n = 21 the
guard is a floor on the point estimate, not a claim of a gain or a proof of no loss;
the harness also prints the contrast's one-sided bound. The English contrast is
reported beside it.

### Why the margin is the pointwise one

The +0.07 margin, the one-sided 95% bound and the A1 control keep the two
registrations comparable: the same question, a different scorer. The cross-encoder is
not given an easier bar because it is cheaper.

## Runs and stopping rules

- A0 and A1 are deterministic, one run each. The cross-encoder is scored in **3 passes
  over pool 50** (R30 is the first 30 of each), averaged per question; three passes
  check determinism on the full set and give three latency samples per query.
  `gold_rerank.py determinism` reads the passes: if fewer than **99.5%** of the cells
  match or fewer than **98%** of the questions have an identical top 8, that is
  reported, and the three-pass mean is still the figure used.
- **One shot.** One model file, one document cut, one `--ubatch-size`, one pool
  definition, one margin. No second quantization, cut or flag set after a result.
- Each pass runs under `--deadline-hours 2`. Reaching it leaves an **incomplete** calls
  file that `evaluate` refuses, and the run is reported as incomplete, never extended.
  A crash resumes from the last finished question of the same identity.
- **Stop at once, `void`,** if the warm-up fails, if more than 2% of the pool-30 pairs
  are unparsed, or if the server is restarted with other flags mid-run.
- **Not while Ollama is busy.** Ollama serializes and the two servers contend for the
  same GPU; no pass starts while another registered run is using it, because that
  would corrupt the latency bar.
- Cost is forecast, not measured: 56 queries by 50 documents of up to about 700 tokens
  is about 2 million tokens per pass for a 0.6B model, expected in minutes, not hours.
  If that is wrong by a large factor the deadline above is the stop.

## What each outcome shows

- **`licensed`** licenses a follow-up OpenSpec change for a `Reranker` protocol, an
  opt-in `llama-server` URL key and the fallback to the fused order, and moves ADR-0083
  toward Accepted. **No default changes, nothing under `src/` changes and no
  dependency is added by this registration.** A held-out confirmation is still owed.
- **`not-licensed`** shows only that **this model, in this quantization, with this
  2,000-character cut and ubatch, on this set** does not clear the bar on these
  questions and this machine. It does **not** show that cross-encoders do not help, that
  a longer cut or a different reranker would not, that the latency would fail on other
  hardware, or that the pointwise arm is the better path. If a result clears P and fails
  only on L or S, that is reported as such: the quality gain and its cost are separate
  findings.
- **`inconclusive`** is the pointwise registration's: a miss over a pool whose top 5 is
  under 90% judged; the unjudged count is the size of a re-pool, which only the owner
  authorizes. The pooling caveat of the pointwise file applies unchanged.
- **`void`** says the run did not measure the reranker; nothing is concluded.

## Threats, said now

- **Small n.** 56 questions, 17 in the vocabulary-gap stratum, 21 Spanish. G2 and S are
  no-harm guards, not claims.
- **One family judged the set.** Questions were authored and judged by Claude models; a
  disagreeing reranker is read as "the judgments", not as truth.
- **Pooling.** Unjudged documents count as not relevant, which is biased against any
  arm that promotes documents the baseline never showed. A1 carries the same bias.
- **The latency is one machine.** It is a bar on this hardware, with the equal-share
  accounting above.
- **Tokenizer drift.** The cut is in characters and the limit in tokens; a Spanish or
  code-heavy document can be longer in tokens than its character count suggests. The
  refused requests are counted for exactly that reason.
- **Two registrations, one set.** The pointwise and cross-encoder arms read the same 56
  questions; neither is a confirmation of the other.

## Harness status

Model-free parts are built under TDD against a loopback stub server and mutated
(`python evals/retrieval_gold/gold_crossenc.py --self-test`): the request shape, the
mapping of results back to documents by index (results arrive best-first), the
fail-closed parsing (HTTP errors, duplicate or missing or out-of-range indexes, bool and
string scores, a refused connection), the equal-share timing, the one-request-per-query
`score_pool`, the truncation report and the L and S conditions. The shared machinery is
`gold_rerank.py`'s, which now also accepts a batch scorer, float scores and a Spanish
subset.

| Piece | State |
| --- | --- |
| `make_rerank_scorer`, `truncation_report`, `decide_crossenc`, `score-pool`, `evaluate` | built, `--self-test` (this PR) |
| a real `llama-server`, the model file, the wide-run file | **not exercised**: no model was downloaded or contacted |

## Run sequence

Every output is written outside the repository. The `wide-run` file is the pointwise
registration's.

```bash
W=~/openkos-eval-runs/retrieval-gold
H="uv run python evals/retrieval_gold/gold_crossenc.py"

# 0. start the server with the flags above, then record: llama-server --version; curl :8081/props
# 1. three passes over pool 50 (the warm-up runs inside score-pool)
for i in 1 2 3; do
  $H score-pool --workspace $W/bundle-v1 --wide-run $W/wide-run-v1.json \
      --model-file ~/models/bge-reranker-v2-m3-Q8_0.gguf \
      --pool 50 --run-index $i --deadline-hours 2 --out $W/xenc-calls-$i.json
done
uv run python evals/retrieval_gold/gold_rerank.py determinism \
    --calls $W/xenc-calls-1.json $W/xenc-calls-2.json $W/xenc-calls-3.json

# 2. no model: arms, contrasts, latency, the verdict with L and S
$H evaluate --wide-run $W/wide-run-v1.json \
    --calls $W/xenc-calls-1.json $W/xenc-calls-2.json $W/xenc-calls-3.json \
    --out $W/xenc-result-v1.json
```

The verdict and the tables go in `RESULTS-1651-cross-encoder.md` next to this file,
with the model file's sha256, the `llama-server` version and `/props`, the harness
commit and the truncation counts.

## Owner confirms

The owner's 2026-10-28 decision covers the opt-in `llama-server` runtime. The items
below were the registrar's proposals, fixed here before any result. **The owner
confirmed all of them unchanged on 2026-10-28, before this file merged and before
any result existed**, together with the server settings (`-np 4 -c 8192 -ub 2048`)
and the run budget (3 passes, 2 hours per pass):

1. **Model and quantization:** `bge-reranker-v2-m3`, Q8_0.
2. **Latency bar:** 2.0 s at p95, pool 30, absolute.
3. **Spanish guard:** -0.03 nDCG@5 on the 21 Spanish questions (G2's floor).
4. **Document cut and `--ubatch-size`:** 2,000 characters and 2,048 tokens.
5. **Margin and pools:** the pointwise registration's (+0.07; 30 primary, 50 exploratory).
6. **What a pass licenses:** an OpenSpec change for the `Reranker` seam and the opt-in
   URL key, and the held-out confirmation; nothing else.
