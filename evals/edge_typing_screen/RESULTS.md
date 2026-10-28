# Results: the edge-typing model screen (#1563)

Registered in `PREREGISTRATION.md` (PR #1645), before any scored call. Run with
`run_edge_typing_screen.py run --model <m> --runs 3 --timeout 60` for each of six models
in registered order, then `score`, at main commit `271e3b89`, on 2026-10-27 (seeds 1, 2, 3;
49 items; 147 calls per model). Stored outputs:
`~/Dev/Projects/openkos-eval-runs/edge-typing-screen/`. Part of #1556; refs #1563.

## Verdict

**The `edge_typing` key is not justified on this fixture: no model clears J.** Every model's
95% lower bound over `always references` (0.490) is below 0. No candidate is selected; the
baseline `qwen3:8b` stays. Recorded as it is, with no retry under tuned bars.

## Gates

Deciding statistic: paired gain of per-item mean strict score, resolved labels, 95% percentile
bootstrap over items (10,000 resamples, seed 42).

| Model | Strict | J: gain over `always references` | J lower bound | J result | Gain over `qwen3:8b` | Lower bound | Selection result |
|---|---|---|---|---|---|---|---|
| `qwen3:8b` (baseline) | 0.653 | +0.163 | -0.061 | not cleared | n/a | n/a | n/a |
| `gemma4:26b-a4b` | 0.653 | +0.163 | -0.054 | not cleared | 0.000 | -0.102 | not selected |
| `qwen3:14b` | 0.599 | +0.109 | -0.136 | not cleared | -0.054 | -0.122 | not selected |
| `qwen3.6:27b` | 0.667 | +0.177 | -0.034 | not cleared | +0.014 | -0.088 | not selected |
| `qwen3.6:35b-a3b` | 0.565 | +0.075 | -0.163 | not cleared | -0.088 | -0.170 | not selected |
| `gemma2:27b` | 0.524 | +0.034 | -0.218 | not cleared | -0.129 | -0.231 | not selected |

Margin to J is the lower bound itself (the bar is 0): the nearest miss is `qwen3.6:27b` at
0.034 short. Floors on the resolved labels: `always references` 0.490, `always related_to`
0.143, `type signature` 0.204.

### Vetoes (registered thresholds, against the baseline)

| Model | V1 failure rate (limit 0.027) | V2 median s (limit 3.10) | V3 trap-hit (limit 0.285) | Tripped |
|---|---|---|---|---|
| `qwen3:8b` | 0.007 | 1.55 | 0.185 | n/a |
| `gemma4:26b-a4b` | 0.000 pass | 2.21 pass | 0.630 FAIL | V3 |
| `qwen3:14b` | 0.068 FAIL | 2.74 pass | 0.333 FAIL | V1, V3 |
| `qwen3.6:27b` | 0.007 pass | 13.09 FAIL | 0.481 FAIL | V2, V3 |
| `qwen3.6:35b-a3b` | 0.034 FAIL | 2.60 pass | 0.370 FAIL | V1, V3 |
| `gemma2:27b` | 0.000 pass | 9.17 FAIL | 0.370 FAIL | V2, V3 |

Every candidate trips V3 (the direction hazard): the registered limit is baseline + 0.10 =
0.285, and the baseline's trap-hit rate of 0.185 is the lowest.

### The stratum guard (the scorer does not apply it)

Selection also needs a non-negative paired gain on the 20 non-constructed items (14 good-life,
6 ADR). From the per-stratum strict output, weighted 14:6 (inputs are rounded to three
decimals, so these are accurate to about 0.001):

| Model | good-life | adr | non-constructed (20) | Gain vs `qwen3:8b` | Guard |
|---|---|---|---|---|---|
| `qwen3:8b` | 0.452 | 0.278 | 0.400 | n/a | n/a |
| `gemma4:26b-a4b` | 0.548 | 0.500 | 0.534 | +0.134 | holds |
| `qwen3:14b` | 0.238 | 0.333 | 0.267 | -0.133 | fails |
| `qwen3.6:27b` | 0.524 | 0.500 | 0.517 | +0.117 | holds |
| `qwen3.6:35b-a3b` | 0.333 | 0.111 | 0.266 | -0.133 | fails |
| `gemma2:27b` | 0.143 | 0.278 | 0.184 | -0.216 | fails |

The guard is moot for the outcome: no candidate reaches it, since none clears J or the
selection lower bound. Constructed stratum: 0.828, 0.736, 0.828, 0.770, 0.770, 0.759 in the
table's order (`qwen3:8b` first).

## Timeouts and failures per model (60 s timeout; 147 calls each)

| Model | Failure rate | timeout | invalid_reply | out_of_vocab | Median s | p95 s |
|---|---|---|---|---|---|---|
| `qwen3:8b` | 0.007 | 0 | 0 | 1 | 1.55 | 3.95 |
| `gemma4:26b-a4b` | 0.000 | 0 | 0 | 0 | 2.21 | 5.66 |
| `qwen3:14b` | 0.068 | 0 | 0 | 10 | 2.74 | 6.93 |
| `qwen3.6:27b` | 0.007 | 1 (e22, seed 2) | 0 | 0 | 13.09 | 29.61 |
| `qwen3.6:35b-a3b` | 0.034 | 1 (e27, seed 3) | 3 | 1 | 2.60 | 10.50 |
| `gemma2:27b` | 0.000 | 0 | 0 | 0 | 9.17 | 29.25 |

`qwen3.6:27b` timed out once, not on the ADR-sized items the registration feared. A timeout
scores 0 and V1 counts it.

## Other reported numbers

Strict (1st) is the sensitivity against the first author's labels; specific precision is over
structural answers. The decision used the resolved labels only; no verdict is label-dependent
(no model clears J on either label set).

| Model | Lenient | Strict (1st) | Recovered | Specific precision | Stability |
|---|---|---|---|---|---|
| `qwen3:8b` | 0.810 | 0.694 | 0.148 | 0.697 (66) | 0.673 |
| `gemma4:26b-a4b` | 0.748 | 0.673 | 0.148 | 0.597 (77) | 0.816 |
| `qwen3:14b` | 0.748 | 0.653 | 0.185 | 0.716 (67) | 0.735 |
| `qwen3.6:27b` | 0.789 | 0.687 | 0.222 | 0.608 (74) | 0.776 |
| `qwen3.6:35b-a3b` | 0.735 | 0.619 | 0.148 | 0.582 (79) | 0.510 |
| `gemma2:27b` | 0.748 | 0.578 | 0.296 | 0.562 (89) | 0.796 |

Every model answers `member_of`, `part_of` and `produced_by` correctly on the items that
carry them and does worst on `references` recall (0.167 to 0.514), the dominant label.

## Time

Measured wall-clock for the screen: 5,713 s = 1.59 h, against a forecast of 1.48 h and a
budget of 2.5 h. Within budget; no model was dropped. Per model: `qwen3:8b` 300 s,
`gemma4:26b-a4b` 386 s, `qwen3:14b` 528 s, `qwen3.6:27b` 2,232 s, `qwen3.6:35b-a3b` 659 s,
`gemma2:27b` 1,608 s.

## Deviations

None from the registered procedure: models, seeds (1 to 3), timeout (60 s), decision rule and
vetoes as registered; digests matched the registration table (`ollama list`). The stratum
guard was computed by hand from the printed per-stratum output, as the registration says.
The scorer prints "draft rule" on its decision header; the rule it applies is the registered one.

## What this licenses

Nothing is adopted and no task key, default or prompt changes. A "not justified" verdict
supports leaving `edge_typing` on the global model. Had a model been selected, it would have
licensed only a confirmation run (seeds 11 to 15) and a later ADR decision.
