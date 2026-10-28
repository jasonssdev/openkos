# Does the dense channel favour long documents? Results of #1648

The registered diagnostic of
[`PREREGISTRATION-1648.md`](PREREGISTRATION-1648.md), run once on the v1 development
fixture ([#1648](https://github.com/jasonssdev/openkos/issues/1648),
[#1652](https://github.com/jasonssdev/openkos/issues/1652)). No model was contacted by
anything below: the diagnostic reads the stored baseline run, the judgments and the
chunk counts of the built bundle.

**Verdict: not supported on this fixture.** The registered rule to proceed to Step 2
fails on all three conditions. No treatment (T1, T2, T3) was run and no bar was retuned,
as registered.

## Inputs

| Item | Value |
| --- | --- |
| Bundle | `bundle-v1`, built by production `ingest` (427 documents, 527 chunk rows at the shipped 12,000-character window) |
| Questions | `questions.v1.json`, 56 (35 English, 21 Spanish), hash `22baff7b...0271d60d` |
| Baseline run | `run-v1.json`, stamp `20261028T041504Z`, harness commit `a26f60d3` (clean), embedder `bge-m3` (digest `79076464...6bab`), shipped defaults, zero chat calls |
| Judgments | `judgments-v1.json` (955 labels), adjudicated as in [`RESULTS-v1-baseline.md`](RESULTS-v1-baseline.md) |
| Diagnostic | `prereg_tools.py length-diagnostic` at `origin/main` after #1670, 10,000 resamples, seed 1609, 95% percentile interval |

Floor on `n` (registered): at least 40 questions and 20 long documents. Both are met
(56 questions; 39 documents of two or more chunks), so the diagnostic is confirmatory.

## Chunk counts (measured, replacing the registration's estimate)

| Type | Documents | Chunk rows | Chunks per document |
| --- | ---: | ---: | --- |
| Source | 61 | 150 | 24 x 1, 7 x 2, 9 x 3, 20 x 4, 1 x 5 |
| (no type) | 2 | 13 | 1 x 6, 1 x 7 |
| every derived type (Concept 189, Decision 37, Event 26, Organization 9, Person 60, Place 1, Procedure 26, Project 16) | 364 | 364 | all 1 |

So all 39 long documents are Sources (37) or untyped files (2): the registered
confound "every long document is a Source" holds exactly. At the T3 window of 4,000
characters the same bundle is 858 chunk rows (Source 389). The registration's estimate
of about 152 Source rows was close to the measured 150.

## Step 1: the diagnostic

Decision label `dense@5` (280 non-relevant slots, 56 questions).

| Statistic | Value | Registered bar | Result |
| --- | --- | --- | --- |
| D2 enrichment of long non-relevant documents | 2.01 [0.91, 3.19] (observed 20, expected 10.0) | lower bound above 1.25 | **fail** (lower bound 0.91) |
| D3 displacement gain, recall@5 | 0.607 actual, 0.622 without non-relevant long documents: gain +0.015 | at least 0.03 | **fail** |
| D1 rho within `Source` only | -0.268 [-0.596, +0.191] | upper bound below 0 | **fail** (upper bound +0.191) |

`proceed = False`. The "type effect" branch (D2 and D3 pass, D1 within Source fails) does
not apply either, because D2 and D3 themselves fail.

### The other registered statistics

| Label | Slots | D2 enrichment | D1 rho, non-relevant | D1 rho, within Source | D3 recall gain |
| --- | ---: | --- | --- | --- | --- |
| `dense@5` | 280 | 2.01 [0.91, 3.19] | -0.093 [-0.252, +0.075] | -0.268 [-0.596, +0.191] | +0.015 |
| `dense@10` | 560 | 1.98 [1.24, 2.79] | +0.013 [-0.109, +0.152] | -0.120 [-0.317, +0.134] | +0.000 |
| `fused@5` | 280 | 1.26 [0.64, 1.90] | -0.346 [-0.501, -0.149] | -0.361 [-0.718, +0.058] | not computed (registered) |
| `fused@8` | 448 | 1.90 [1.30, 2.53] | -0.145 [-0.306, +0.027] | -0.092 [-0.335, +0.169] | not computed (registered) |

D4, type share of slots beside the share of gold documents, `dense@5`: Concept 0.34 /
0.25, Decision 0.09 / 0.09, Event 0.06 / 0.06, Organization 0.01 / 0.03, Person 0.04 /
0.01, Place 0.01 / 0.01, Procedure 0.07 / 0.05, Project 0.02 / 0.02, **Source 0.35 /
0.49**. Sources hold fewer retrieved slots than their share of the gold documents.

D5, relevant share of `dense@5` slots by chunk bucket: one chunk 138 of 229 (0.60), two
chunks 15 of 19 (0.79), three or more 16 of 32 (0.50).

## Reading

- The point estimate of D2 is about 2, so long non-relevant documents do appear more often
  than their base rate. But the `dense@5` interval is wide (0.91 to 3.19, 20 observed
  against 10.0 expected, from 56 questions) and its lower bound sits under both 1.25 and
  1.00. The enrichment is not distinguishable from no effect at the registered strictness.
- What a remedy could buy is small: deleting every non-relevant long document from the
  dense top 10 raises recall@5 by 0.015, and recall@10 by 0.000. The registered bar was
  0.03.
- Inside `Source`, longer documents rank slightly earlier (rho -0.27 at `dense@5`), but the
  interval crosses zero.
- The `dense@10` and `fused@8` enrichment intervals (1.24 to 2.79; 1.30 to 2.53) do exclude
  1.00. They are not the decision label and carry no multiplicity correction; they say that
  deeper in the list long non-relevant documents are over-represented, while the displacement
  they cause costs no recall inside the pool.
- The `fused@5` rho of -0.346 (more chunks, earlier) is the largest correlation in the
  table, and it is on a label the registration does not decide on.

## What this does not show

- It does not show that there is no length bias. It shows that on 56 questions, 39 long
  documents and a judged pool built from the baseline channels, the registered evidence
  does not reach the bars. The `dense@5` enrichment interval is wide enough to hold both no
  effect and a large one.
- Pool bias applies as registered: unjudged documents count as not relevant, and the D3
  deletion backfills perfectly from the same pool.
- Relevance is three blind Claude judgments (see the baseline results), not human judgment.

## Deviations and operational notes

- **No treatment arm was run**, per the rule. `run --chunk-distances` for T1 and T2 and the
  T3 chunk-target override (built in #1670) were not exercised.
- `length-diagnostic` defaults `--questions` to `questions.draft.json`; the registered data
  is the v1 set, so `--questions evals/retrieval_gold/questions.v1.json` was passed (the
  default fails closed on the run's question-set hash).
- The stored baseline run carries its harness commit (`a26f60d3`, clean) and the shipped
  defaults, which is all the registration asks of it; it was not re-run.
- Embeddings were bge-m3 only. The GPU was not needed for this analysis.

## Reproduce

```bash
W=~/openkos-eval-runs/retrieval-gold
uv run python evals/retrieval_gold/prereg_tools.py chunk-counts \
    --bundle $W/bundle-v1/bundle --out $W/r1648/chunk-counts.json
uv run python evals/retrieval_gold/prereg_tools.py length-diagnostic \
    --questions evals/retrieval_gold/questions.v1.json \
    --run $W/run-v1.json --judgments $W/judgments-v1.json \
    --chunk-counts $W/r1648/chunk-counts.json --out $W/r1648/length-diagnostic.json
```
