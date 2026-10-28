# Query-aware excerpts: the registered comparison

Result of the comparison registered in
[`PREREGISTRATION-1647.md`](PREREGISTRATION-1647.md)
([#1647](https://github.com/jasonssdev/openkos/issues/1647), umbrella
[#1652](https://github.com/jasonssdev/openkos/issues/1652)), run exactly with its
frozen commands. No chat model and no embedding call is in any number below: the
runs used a poisoned `OLLAMA_HOST`. Every figure here was re-read from the two
output files (`delivered-off.json`, `delivered-on.json`) and agrees with the printed
`delivered-compare` output.

## Inputs

| Item | Value |
| --- | --- |
| Bundle | `bundle-v1` (`retrieval-gold-v1`), read-only |
| Ranking | stored `run-v1.json`, channel `fused`, k in {5, 8, 10}, identical in both arms |
| Questions | `questions.v1.json`, hash `22baff7b...0271d60d`; 54 scored, `q01` and `q10` unscored (no grade-2 document) |
| Passages | `passages-v1.json`, sha256 `75caf1bd3caa2433369a8d46c5112c7070dde790121018a5848ff013fd1b5546`; 136 grade-2 documents, 180 spans |
| Window | context 12,288 tokens, reply reserve 8,192 tokens (shipped) |
| Arms | `delivered-off.json` (`query_aware: false`), `delivered-on.json` (`query_aware: true`) |

## Registered result: delivered, paired

`delivered` = every annotated grade-2 document has a gold span in its final context
block. One-sided 95% lower bound from the harness paired bootstrap (10,000
resamples, seed 1609).

| k | Baseline | Query-aware | Mean diff | Lower bound | Improved | Regressed |
| --- | --- | --- | --- | --- | --- | --- |
| 5 (primary) | 13 of 54 | 25 of 54 | +0.2222 | +0.1296 | 12 | 0 |
| 8 (primary) | 6 of 54 | 18 of 54 | +0.2222 | +0.1111 | 14 | 2 (`q24`, `q29`) |
| 10 (reported) | 2 of 54 | 16 of 54 | +0.2593 | +0.1481 | 15 | 1 (`q29`) |

`retrieved` is identical in both arms at every k (30, 37, 41 of 54), as designed:
any difference is trimming. `any` (at least one gold document delivered) moves
33 to 41 at k=5, 30 to 38 at k=8 and 26 to 35 at k=10 (of 54).

## Registered conditions

| Condition | Required | Observed | Met |
| --- | --- | --- | --- |
| Lower bound at k=8 above 0 | > 0 | +0.1111 | yes |
| Lower bound at k=5 above 0 | > 0 | +0.1296 | yes |
| No language stratum below -0.03, k=8 | >= -0.03 | en +0.2121, es +0.2381 | yes |
| No language stratum below -0.03, k=5 | >= -0.03 | en +0.2121, es +0.2381 | yes |
| Exposure: baseline `trimmed` losses at k=8 | >= 5 | 31 of 54 | yes (exposure exists) |

## Verdict: SUCCESS

All registered conditions hold, and the test was exposed (31 of 54 baseline
questions lost to trimming at k=8, against the floor of 5), so it could have
failed. By the registered decision rule this licenses an ADR proposing
`query_aware_excerpts` default on. It changes no default by itself, and the ADR may
still decline on the secondary evidence below; none of it argues for declining.

## Strata (secondary, no decision rests on them)

Baseline to query-aware, `n of N`, mean paired difference.

| Stratum | N | k=5 | k=8 | k=10 |
| --- | --- | --- | --- | --- |
| en | 33 | 12 to 19 (+0.2121) | 6 to 13 (+0.2121) | 2 to 12 (+0.3030) |
| es | 21 | 1 to 6 (+0.2381) | 0 to 5 (+0.2381) | 0 to 4 (+0.1905) |
| single-hop | 25 | 5 to 14 (+0.3600) | 2 to 12 (+0.4000) | 0 to 11 (+0.4400) |
| multi-hop | 14 | 1 to 4 (+0.2143) | 1 to 2 (+0.0714) | 0 to 1 (+0.0714) |
| enumeration | 15 | 7 to 7 (+0.0000) | 3 to 4 (+0.0667) | 2 to 4 (+0.1333) |
| vocab_gap | 15 | 4 to 7 (+0.2000) | 3 to 5 (+0.1333) | 1 to 5 (+0.2667) |
| no vocab_gap | 39 | 9 to 18 (+0.2308) | 3 to 13 (+0.2564) | 1 to 11 (+0.2564) |

The gain is not confined to questions whose words appear in the passage: the
vocabulary-gap stratum gains at every k (+0.133 to +0.267), on small counts. No
kind regresses; enumeration is flat at k=5 and multi-hop gains little at k=8 and
k=10, where most of those questions are not fully retrieved.

## Loss decomposition

Questions not delivered, by the stage that lost them (of 54).

| k | Arm | `not_retrieved` | `omitted` | `trimmed` |
| --- | --- | --- | --- | --- |
| 5 | baseline | 24 | 0 | 17 |
| 5 | query-aware | 24 | 0 | 5 |
| 8 | baseline | 17 | 0 | 31 |
| 8 | query-aware | 17 | 0 | 19 |
| 10 | baseline | 13 | 0 | 39 |
| 10 | query-aware | 13 | 0 | 25 |

`not_retrieved` is unchanged and `omitted` stays 0, as it must: the arm only moves
what is lost to trimming. At k=8 it still loses 19 questions to trimming.

## Side finding (an observation, not a registered result)

With even-coverage trimming, `delivered` falls as the limit rises: 13 of 54 at k=5,
6 at k=8, 2 at k=10 (baseline), because more blocks share the same budget and each
excerpt shrinks. The query-aware arm shows the same shape at a higher level (25, 18,
16). It matters for [ADR-0062](../../docs/adr/0062-query-limit-defaults-to-8-measured-against-5.md),
which chose a default limit of 8 on retrieval recall: at that limit the baseline
delivers the gold passage for fewer questions than at 5. This comparison was not
designed to test the limit, so it supports no recommendation on it.

## Deviations and limits

- **The passages were written by models.** Eight parallel Claude Sonnet workers, one
  slice of the sheet each, on the owner's delegation; no human reviewed them. A model
  may prefer spans lexically close to the question, which could favour a BM25 arm.
  The vocabulary-gap stratum, where that bias is weakest, still gains.
- **`q01` and `q10` are unscored**: they have no grade-2 document, so 54 of 56
  questions are scored.
- **List-style questions are credited for any one part.** Several spans of one
  document are alternatives, so `q35`, `q51`, `q54` and `q55` count a document
  delivered when one item of the list arrives. This favours both arms alike and
  makes `delivered` an upper bound for them.
- Not answer quality: no chat model is in the loop. One window setting, one bundle,
  development series only. The scorer is the shipped BM25 over 800-character windows.
- Two questions regress at k=8 (`q24`, `q29`); they were not investigated.

## Reproduce

The frozen commands in the pre-registration, with no model call:

```bash
W=~/openkos-eval-runs/retrieval-gold
uv run python evals/retrieval_gold/run_retrieval_gold.py delivered-compare \
    --workspace $W/bundle-v1 --base $W/passages/delivered-off.json \
    --cand $W/passages/delivered-on.json
```
