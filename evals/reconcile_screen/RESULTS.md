# Results: the merged-body reconciliation model screen (#1563)

Registered in `PREREGISTRATION.md` (PR #1645), before any scored call. Run with
`run_reconcile_screen.py run --model <m> --runs 5 --timeout 60` for each of six models in
registered order, then `score`, at main commit `271e3b89`, on 2026-10-27 (seeds 1 to 5; 15
cases, 121 claims; 75 calls per model). Stored outputs:
`~/Dev/Projects/openkos-eval-runs/reconcile-screen/`. Part of #1556; refs #1563.

## Verdict

**A model call is justified: `gemma4:26b-a4b` clears J and is SELECTED over `qwen3:8b`.** It is
the only model to clear J, and the only one selected. The confirmation run (seeds 11 to 15)
has not been run. This adopts nothing and changes no default.

## Gates

Deciding statistic: paired gain of per-case mean strict score, resolved labels, 95% percentile
bootstrap over cases (10,000 resamples, seed 42). Reference `sentence_union` 0.777.

| Model | Strict | J: gain over `sentence_union` | J lower bound | J result | Gain over `qwen3:8b` | Lower bound | Vetoes | Result |
|---|---|---|---|---|---|---|---|---|
| `qwen3:8b` (baseline) | 0.795 | +0.018 | -0.049 | not cleared | n/a | n/a | n/a | n/a |
| `gemma4:26b-a4b` | 0.889 | +0.112 | +0.035 | cleared (margin +0.035) | +0.094 | +0.013 | none | **SELECTED** |
| `qwen3:14b` | 0.791 | +0.014 | -0.038 | not cleared | -0.004 | -0.033 | none | not selected |
| `qwen3.6:27b` | 0.765 | -0.012 | -0.085 | not cleared | -0.030 | -0.086 | V1, V2 | not selected |
| `qwen3.6:35b-a3b` | 0.794 | +0.017 | -0.052 | not cleared | -0.001 | -0.075 | none | not selected |
| `gemma2:27b` | 0.762 | -0.015 | -0.097 | not cleared | -0.032 | -0.107 | V2 | not selected |

The selected model's margins are thin: J lower bound +0.035 and selection lower bound +0.013,
on 15 cases. Floors: `sentence_union` 0.777, the stack production keeps on a refusal 0.728.

### Vetoes (registered thresholds, against the baseline)

| Model | V1 failure rate (limit 0.02) | V2 median s (limit 11.60) | V3 fabrication (limit 0.167) | Tripped |
|---|---|---|---|---|
| `qwen3:8b` | 0.000 | 5.80 | 0.067 | n/a |
| `gemma4:26b-a4b` | 0.000 pass | 4.59 pass | 0.013 pass | none |
| `qwen3:14b` | 0.000 pass | 10.01 pass | 0.000 pass | none |
| `qwen3.6:27b` | 0.067 FAIL | 26.15 FAIL | 0.000 pass | V1, V2 |
| `qwen3.6:35b-a3b` | 0.000 pass | 4.40 pass | 0.040 pass | none |
| `gemma2:27b` | 0.000 pass | 14.92 FAIL | 0.027 pass | V2 |

## Timeouts and failures per model (60 s timeout; 75 calls each)

| Model | Failure rate | timeout | backend | refused | Median s | p95 s |
|---|---|---|---|---|---|---|
| `qwen3:8b` | 0.000 | 0 | 0 | 0 | 5.80 | 14.68 |
| `gemma4:26b-a4b` | 0.000 | 0 | 0 | 0 | 4.59 | 11.60 |
| `qwen3:14b` | 0.000 | 0 | 0 | 0 | 10.01 | 22.96 |
| `qwen3.6:27b` | 0.067 | 5 (`docs-policy`, all five seeds) | 0 | 0 | 26.15 | 60.00 |
| `qwen3.6:35b-a3b` | 0.000 | 0 | 0 | 0 | 4.40 | 10.49 |
| `gemma2:27b` | 0.000 | 0 | 0 | 0 | 14.92 | 28.86 |

A timeout scores as the stacked body, as registered.

## Other reported numbers

| Model | Lenient | Strict (1st) | Retention | Dedupe | Stability | Retention a_only / b_only / both |
|---|---|---|---|---|---|---|
| `qwen3:8b` | 0.810 | 0.803 | 0.979 | 0.327 | 0.800 | 0.981 / 0.961 / 1.000 |
| `gemma4:26b-a4b` | 0.902 | 0.897 | 0.939 | 0.805 | 0.200 | 0.938 / 0.922 / 0.964 |
| `qwen3:14b` | 0.802 | 0.797 | 0.985 | 0.285 | 0.667 | 0.995 / 0.965 / 1.000 |
| `qwen3.6:27b` | 0.779 | 0.771 | 0.855 | 0.699 | 0.067 | 0.815 / 0.837 / 0.938 |
| `qwen3.6:35b-a3b` | 0.808 | 0.802 | 0.884 | 0.643 | 0.067 | 0.852 / 0.878 / 0.933 |
| `gemma2:27b` | 0.777 | 0.771 | 0.836 | 0.713 | 0.200 | 0.776 / 0.809 / 0.952 |

Strict, lenient and first-author scores move together for every model, so no verdict is
label-dependent. Where `gemma4:26b-a4b` wins is dedupe (0.805 against the baseline's 0.327):
the baseline retains slightly more claims but rarely states a shared claim only once. Its
stability across seeds is low (0.200), so individual runs vary.

## Time

Measured wall-clock for the screen: 5,523 s = 1.53 h, against a forecast of 1.66 h and a
budget of 2.5 h. Within budget; no model was dropped. Per model: `qwen3:8b` 501 s,
`gemma4:26b-a4b` 400 s, `qwen3:14b` 835 s, `qwen3.6:27b` 2,168 s, `qwen3.6:35b-a3b` 393 s,
`gemma2:27b` 1,226 s. The confirmation (baseline + `gemma4:26b-a4b`, 2 x 75 calls) is not yet
run; at these medians it adds roughly 0.25 h.

## Deviations

None from the registered procedure: models, seeds (1 to 5), timeout (60 s), decision rule and
vetoes as registered; digests matched the registration table (`ollama list`). The registered
confirmation was not run in this change.

## What this licenses

A selection licenses one confirmation run (seeds 11 to 15, same rule) on notes that ingest
actually produced, because this fixture's notes are repository documents, and then a separate
ADR decision. It does not license a config default or a `reconcile` task key; the key set is
closed by #508's rule. The screen does not score whether the merged document reads well.
