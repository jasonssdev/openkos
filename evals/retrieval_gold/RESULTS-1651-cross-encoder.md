# A cross-encoder reranker: the registered comparison

Result of the comparison registered in
[`PREREGISTRATION-1651-cross-encoder.md`](PREREGISTRATION-1651-cross-encoder.md)
([#1651](https://github.com/jasonssdev/openkos/issues/1651), umbrella
[#1652](https://github.com/jasonssdev/openkos/issues/1652)), run with its registered
model, server flags, document cut, pools and passes. Two harness defects found on the
first contact with a real server were fixed without touching any registered quantity;
they are listed under Deviations. Every figure was read from the printed `evaluate`
output, which was also written to `xenc-result-v1.json`.

## Verdict: INCONCLUSIVE

By the registered order of verdicts: G4 holds (no void); P fails and G3 fails, so the
verdict is `inconclusive`, not `not-licensed`. R30 is not licensed in either reading:
the gain is negative, not merely small. This is a result about **this model, in this
quantization, with this 2,000-character cut and ubatch, on this set**, and nothing more
(see "What this does and does not show").

## Inputs and identity

| Item | Value |
| --- | --- |
| Model file | `bge-reranker-v2-m3-Q8_0.gguf` (from `gpustack/bge-reranker-v2-m3-GGUF`), 635,676,416 bytes |
| Model sha256 | `a43c7c9b11a4c1517e5bf95151960e1621d1b72f7a493364b01e386cf1aaa1d3` (verified before use; stored in every calls file) |
| Server | `llama-server` version 0.7.0 (build 11669, commit 620d2225c), AppleClang 21, Darwin arm64 |
| Flags | `-m <model> --reranking --host 127.0.0.1 --port 8081 -c 8192 -np 4 -b 2048 -ub 2048` (all flags exist in `--help`) |
| `/props` | `total_slots` 4, `n_ctx` 2048 per slot, `model_ftype` Q8_0 |
| Harness | base commit `b0a2a73a` plus the two fixes below (the working tree was dirty at run time; the fixes are in the commit that adds this file) |
| Bundle and questions | `bundle-v1`, `questions.v1.json` (56: 35 English, 21 Spanish; vocabulary-gap stratum n = 17), committed `judgments-v1.json` |
| `wide-run` | produced with the registered pointwise command (`gold_rerank.py wide-run`, bge-m3 via Ollama, depth 50) |
| Runs | 3 passes over pool 50, `--deadline-hours 2`; all complete |
| Determinism | cells identical 1.0000 (bar 0.995); top 8 identical 1.0000 (bar 0.98) |

The machine had no other model resident during the passes: the bge-m3 model that the
`wide-run` step left in Ollama was unloaded before the server started.

## Arms

nDCG@5 and recall are means over the 56 questions; `judged@k` is the share of the top k
that has a judgment; `unjud@8` is the count of unjudged documents in the top 8.

| Arm | nDCG@5 | nDCG@5 (gap) | recall@5 | recall@8 | judged@5 | judged@8 | unjud@8 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A0 shipped | 0.6806 | 0.5950 | 0.5681 | 0.7492 | 1.000 | 1.000 | 0 |
| A1 wide control | 0.6515 | 0.5051 | 0.5265 | 0.6543 | 0.879 | 0.815 | 83 |
| R30 (primary) | 0.6474 | 0.5491 | 0.5040 | 0.6114 | 0.796 | 0.748 | 113 |
| R50 (exploratory) | 0.6423 | 0.5334 | 0.4991 | 0.5968 | 0.754 | 0.676 | 145 |

## Registered conditions

One-sided 95% lower bounds from `gold_core.paired_bootstrap` (10,000 resamples,
seed 1609).

| # | Required | Observed | Met |
| --- | --- | --- | --- |
| P | nDCG@5 R30 - A0 at least +0.07, lower bound above 0 | -0.0331, lower bound -0.0759 | no |
| C | A1 - A0 under half of R30's gain | A1 - A0 = -0.0291 (R30's gain is negative) | holds only vacuously (see note) |
| G1 | recall@8 R30 - A0 at least -0.02 | -0.1378 (lower -0.1939) | no |
| G2 | vocabulary-gap nDCG@5 R30 - A0 at least -0.03 | -0.0459 (lower -0.1334), n = 17 | no |
| G3 | `judged@5` of R30 at least 0.90 | 0.796 | no |
| G4 | at most 2% of pool-30 pairs unparsed | 0.0000 (0 of 2,800 pairs, all passes) | yes |
| L | pool-30 p95 added latency at most 2.0 s | 2.24 s | no |
| S | Spanish nDCG@5 R30 - A0 at least -0.03 | -0.0518 (lower -0.1352), n = 21 | no |

Note on C: the harness prints "ok", but R30's gain is negative, so the condition has no
content here. It decides nothing: P already fails.

Further contrasts (mean, one-sided 95% lower bound): R30 - A1 nDCG@5 -0.0040
[-0.0492]; R50 - A0 nDCG@5 -0.0383 [-0.0809]; R50 - A1 nDCG@5 -0.0092 [-0.0529];
recall@5 R30 - A0 -0.0641 [-0.1082]; recall@8 R50 - A0 -0.1524 [-0.2079]. The English
nDCG@5 contrast, derived from the printed all-question and Spanish means
(35 x en + 21 x es = 56 x all), is about -0.022; the harness does not print it.

## Latency

Wall-clock time of the `/v1/rerank` request over loopback, shared equally among the
candidates, over every (pass, query): 168 samples.

| Pool | p50 | p95 | mean |
| --- | --- | --- | --- |
| 30 (decisive) | 1.76 s | 2.24 s | 1.74 s |
| 50 | 2.93 s | 3.73 s | 2.90 s |

The whole run (three passes of 56 requests each, plus the untimed warm-up) took about
three minutes per pass, inside the forecast of minutes. L fails at p95 by 0.24 s on this
machine; it passes at p50. Because the cost is accounted as linear in documents, the
pool-30 figure is 30/50 of the pool-50 request, as registered.

## Truncation (counted, not decisive)

| Item | Count |
| --- | --- |
| Pairs scored (per pass, pool 50) | 2,800 |
| Pairs whose body was cut at 2,000 characters | 1,766 (63%) |
| Distinct documents cut | 142 |
| Pairs unparsed | 0 |
| Requests refused for size | 0 |

The same counts appear in all three calls files. Nearly two thirds of the pairs reach the
reranker with a body cut short, so an answer past character 2,000 is invisible to it
while the shipped display can still surface the document. The registration states this
risk and does not make the count decisive; it cannot be separated here from the model's
own quality, because no longer cut was registered.

## What drove the verdict

- **The gain is absent, not small.** R30 sits 0.033 below the shipped order and, within
  noise, level with its own un-reranked wide control (R30 - A1 -0.0040). Reranking the
  wide pool did not recover what widening it lost.
- **Recall at 8 falls by 0.14.** Reranking moves documents the shipped order shows at
  ranks 6 to 8 out of the top 8, in favour of documents the judgments do not cover.
- **The pool is under-judged for this arm.** `judged@5` is 0.796 (bar 0.90): 113
  unjudged documents sit in the R30 top 8. The pooling caveat of the pointwise
  registration applies unchanged: unjudged documents count as not relevant, which
  penalizes any arm that promotes documents the baseline never showed. That is exactly
  why G3 routes a miss like this to `inconclusive` and not `not-licensed`. The size of a
  re-pool is the unjudged count above; only the owner authorizes one.
- **Spanish does not drive it.** S fails (-0.0518), but so does the all-question mean;
  English is also negative (about -0.022). The harm is not confined to the multilingual
  case.
- **Latency is a second, independent failure.** Even a result that cleared P would have
  failed L at p95 on this machine.

## What this does and does not show

It shows that `bge-reranker-v2-m3` Q8_0, served by `llama-server` 0.7.0 with the
registered flags, scoring the first 30 documents of the fused order truncated to 2,000
characters, does not raise nDCG@5 on this set and does not meet the 2.0 s p95 bar on
this machine. It does **not** show that cross-encoders do not help, that a longer cut or
another reranker would not, that the latency would fail on other hardware, or that the
pointwise arm is the better path. Because the verdict is `inconclusive` and not
`not-licensed`, the open question the registration names is the under-judged top 5; a
re-pool is the owner's decision.

Under the registered consequence of this outcome: no OpenSpec change for a `Reranker`
seam is licensed, no default changes, nothing under `src/` changes and no dependency is
added. [ADR-0083](../../docs/adr/0083-a-reranker-may-be-served-by-an-opt-in-llama-server-as-a-second-local-runtime.md)
stays Proposed.

## Deviations

None of these changes the model, the server flags, the document cut, the pools, the
passes, the margin or any bar.

1. **`score-pool` stamp (harness fix).** `harness_stamp.build_stamp` refuses an empty
   prompt set and asks the local Ollama for a model digest; the first run of `score-pool`
   against a real server therefore aborted after the warm-up, before any scoring. The
   stamp is now built directly: the model file's sha256 as the digest and the document
   template as its single identified prompt (`rerank-document`). No pass had produced
   output before the fix; the three passes reported here were all run after it.
2. **`evaluate --workspace` (harness fix).** `gold_crossenc.py evaluate` loaded the v1
   questions without a corpus and refused them (`questions.v1.json is written for bundle
   'retrieval-gold-v1', not 'retrieval-gold-v0'`). An optional `--workspace` argument was
   added and passed, so the registered `evaluate` command gains one flag. The same
   omission exists in `gold_rerank.py evaluate` and was not changed.
3. **Model file path.** The model file lived at
   `~/openkos-eval-runs/models/bge-reranker-v2-m3-Q8_0.gguf` and not at the
   `~/models/...` path in the run sequence. The sha256 is the identity; the path is not.
4. **Harness commit.** The harness stamp records `b0a2a73a` with `dirty: true`, because
   the two fixes above were uncommitted when the passes ran.
5. **Aborted attempt.** The loop of three passes was started once before deviation 1
   was fixed; each pass exited with an error after the warm-up and wrote no calls file.
   No other discarded run exists.

## Reproduce

```bash
W=~/openkos-eval-runs/retrieval-gold
H="uv run python evals/retrieval_gold/gold_crossenc.py"
llama-server -m <bge-reranker-v2-m3-Q8_0.gguf> --reranking --host 127.0.0.1 \
    --port 8081 -c 8192 -np 4 -b 2048 -ub 2048
for i in 1 2 3; do
  $H score-pool --workspace $W/bundle-v1 --wide-run $W/wide-run-v1.json \
      --model-file <bge-reranker-v2-m3-Q8_0.gguf> --pool 50 --run-index $i \
      --deadline-hours 2 --out $W/xenc-calls-$i.json
done
$H evaluate --workspace $W/bundle-v1 --wide-run $W/wide-run-v1.json \
    --calls $W/xenc-calls-1.json $W/xenc-calls-2.json $W/xenc-calls-3.json \
    --out $W/xenc-result-v1.json
```
