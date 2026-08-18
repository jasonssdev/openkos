# Does self-attribution make the citation list mean anything? (#753)

`qwen3:8b`, 14 documents, 60 answers over 3 run(s), `limit=5`.

## Baseline — the behavior being replaced

Read free off `evals/query_title/results/` (170 stored answers from the pre-#753 code path, on `query_title`'s SIX-document corpus, not this one):

- distinct citation COUNTS across all 170: **1**
- distinct citation SETS across all 170: **4**

A single distinct count means the list never varied with the answer -- it was `min(limit, corpus)` renamed.

## Treatment

| class | n | reported | absent | unparsed | compliance | kept share (mean) | kept share (median) | cited nothing |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `grounded` | 30 | 30 | 0 | 0 | 100% | 0.487 | 0.400 | 0 |
| `adjacent` | 30 | 30 | 0 | 0 | 100% | 0.140 | 0.000 | 22 |

Separation (grounded - adjacent, mean kept share): **+0.347**

## Verdict

POSITIVE -- compliance 100%, and adjacent answers keep 0.347 less of their context than grounded ones.

`kept share` is citations kept divided by context blocks SENT. The pre-#753 value is 1.000 by construction, for every question of either class.

## What this does not measure

Whether the blocks the model NAMES are the ones it actually drew on. That is an entailment judgement and nothing here computes one. The labelled classes proxy it only at the resolution of "should this answer lean on the bundle at all".
