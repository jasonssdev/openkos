# Results: the answer-role re-screen under the shipped prompt (#1596)

**Verdict: no winner. `qwen3:8b` stays as the answering model; no candidate is
selected for the answer role or for answer+citation, and no confirmation
ran.** The rule is in [`PREREGISTRATION-1596.md`](PREREGISTRATION-1596.md),
committed (`1e5538e3`) before any new model call; the harness was not changed.
Nothing here changes a default, a prompt or a task key.

- `gemma4:26b-a4b` is the only candidate that clears every veto, and it is
  indistinguishable from the baseline on the answer metric: delta **+0.000
  [-0.111, +0.102]** (36 of 36 anchored items). `select` needs a lower bound
  above 0.
- `qwen3:14b` and `qwen3.6:35b-a3b` fail V3 (answers in Spanish: 0.865 and
  0.830 against a floor of 0.912) even under the language-neutral prompt.
- Citation: no lower bound above 0 either (`gemma4:26b-a4b` +0.049 [-0.010,
  +0.111], 96 of 96 items).

This is the outcome the registration expected ("no winner for lack of a lower
bound above 0, not for lack of capability"). It is a failure to show a gain,
not a finding that `gemma4:26b-a4b` is worse.

## Run

- 2026-10-20, about 2,554 s (0.71 h) of Ollama wall-clock against a forecast
  of about 0.7 h and the 4.0 h cap; Apple Silicon, Ollama 0.32.9,
  `OLLAMA_NUM_PARALLEL` at the default, models one at a time, no other user of
  the server. Digests matched the registration.
- Answer call only, seeds 1 to 3, 96 items per run, `num_ctx` 12288,
  `num_predict` 8192, thinking off, timeout 60 s, under the shipped
  `answer/system`.
- Cache: `qwen3:8b`, `gemma4:26b-a4b`, `qwen3:14b` seeds 1 and 2 were **192 of
  192 cache hits** (the #1583 treated-arm generations, byte-identical prompt;
  replayed `--offline` before the registration commit) and seed 3 was 96 of 96
  new calls each. `qwen3.6:35b-a3b` had no valid cache: **288 of 288 new
  calls**. 0 of 288 failures or timeouts for every model.
- No confirmation: nothing was selected, so seeds 11 to 15 were not run.
- Raw generations and the comparison report (`compare-runs3-qwen3-8b.md`) are
  outside the repository under
  `~/Dev/Projects/openkos-eval-runs/ask-screen-1596/`, a copy of #1560's cache
  directory so the earlier projections were not overwritten. No generated
  answer is recorded here.

## Registered rule and each outcome

Delta = candidate minus `qwen3:8b`, paired per item (mean over the 3 seeds),
pooled over the three sets, 95% percentile bootstrap (10,000 resamples, seed
42). Answer baseline mean 0.833 (36 of 36 anchored items); citation baseline
0.885 (96 of 96 items).

| role | model | n items | delta [95% CI] | `es-es` | `es-en` | `es-built` | vetoes | `select` |
| --- | --- | ---: | --- | ---: | ---: | ---: | --- | --- |
| answer | `gemma4:26b-a4b` | 36 of 36 | +0.000 [-0.111, +0.102] | +0.083 | +0.028 | -0.111 | none | lower bound <= 0 |
| | `qwen3:14b` | 36 of 36 | -0.074 [-0.185, +0.028] | +0.000 | +0.000 | -0.222 | V3 | vetoed |
| | `qwen3.6:35b-a3b` | 36 of 36 | -0.009 [-0.093, +0.074] | +0.083 | +0.000 | -0.111 | V3 | vetoed |
| citation | `gemma4:26b-a4b` | 96 of 96 | +0.049 [-0.010, +0.111] | +0.104 | +0.062 | -0.021 | none | lower bound <= 0 |
| | `qwen3:14b` | 96 of 96 | -0.021 [-0.080, +0.035] | +0.073 | -0.073 | -0.062 | X1 | vetoed |
| | `qwen3.6:35b-a3b` | 96 of 96 | +0.021 [-0.035, +0.076] | +0.073 | +0.042 | -0.052 | none | lower bound <= 0 |

`select` names no model for answer and none for citation, so no model is
selected for answer+citation. Veto detail (all three seeds, 288 answers per
model, from the guardrails of the comparison report):

| model | V1 answer failures | V2 median answer s (ratio) | V3 answers in Spanish (floor 0.912) | V4 `USED:` reported (floor 0.950) | X1 `es-en` upper bound | M (reused from #1560) |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| `qwen3:8b` | 0 of 288 | 3.93 (1.00) | 277 of 288 = 0.962 | 288 of 288 = 1.000 | baseline | 6.35 GiB, pass |
| `gemma4:26b-a4b` | 0 of 288, pass | 3.05 (0.78), pass | 283 of 288 = 0.983, **pass** | 286 of 288 = 0.993, pass | answer +0.028, pass | 17.76 GiB, pass |
| `qwen3:14b` | 0 of 288, pass | 5.53 (1.41), pass | 249 of 288 = 0.865, **fail** | 281 of 288 = 0.976, pass | citation es-en delta -0.073, upper bound below 0: **fail (citation)** | 10.38 GiB, pass |
| `qwen3.6:35b-a3b` | 0 of 288, pass | 3.54 (0.90), pass | 239 of 288 = 0.830, **fail** | 286 of 288 = 0.993, pass | answer +0.000, pass | 21.10 GiB, pass |

V1, V2 and M hold for all three. V3 fails the answer role of `qwen3:14b` and
`qwen3.6:35b-a3b`; X1 fails the citation role of `qwen3:14b`. The M column is
the #1560 stage-0 measurement, not re-measured (registered P4).

## Why V3 still fails for two candidates

Share of answers in Spanish, by question label (all three seeds):

| model | adjacent (unanswerable) | grounded |
| --- | ---: | ---: |
| `qwen3:8b` | 84 of 90 = 0.933 | 193 of 198 = 0.975 |
| `gemma4:26b-a4b` | 88 of 90 = 0.978 | 195 of 198 = 0.985 |
| `qwen3:14b` | 68 of 90 = 0.756 | 181 of 198 = 0.914 |
| `qwen3.6:35b-a3b` | 43 of 90 = 0.478 | 196 of 198 = 0.990 |

The language-neutral wording fixed `gemma4:26b-a4b` (#1583: 0.250 to 0.983) but
not `qwen3:14b` (22 of 90 adjacent answers in English, 22 of 90 containing "does
not cover") and not `qwen3.6:35b-a3b`, which #1583 did not run: 40 of 90 adjacent
answers in English, 20 of 90 containing "does not cover", 7 of 90 with no
language vote, while its grounded answers are 196 of 198 in Spanish. Those
two refuse unanswerable Spanish questions in English more often than the
baseline, which is what V3 exists to catch. (The language measure is the
repository's function-word voter; an answer it cannot vote on is not counted
as Spanish.)

## Absolute means (answer: 12 anchored items x 3 seeds per set; citation: 32 items x 3 seeds per set)

| model | answer `es-es` / `es-en` / `es-built` | citation `es-es` / `es-en` / `es-built` |
| --- | --- | --- |
| `qwen3:8b` | 0.917 / 0.972 / 0.611 | 0.885 / 0.938 / 0.833 |
| `gemma4:26b-a4b` | 1.000 / 1.000 / 0.500 | 0.990 / 1.000 / 0.812 |
| `qwen3:14b` | 0.917 / 0.972 / 0.389 | 0.958 / 0.865 / 0.771 |
| `qwen3.6:35b-a3b` | 1.000 / 0.972 / 0.500 | 0.958 / 0.979 / 0.781 |

`gemma4:26b-a4b` ties or beats the baseline on the two Spanish-bundle sets and
is lower on `es-built` (0.500 against 0.611), the compiled bundle where 7 of
the 12 anchored questions have no document gold in the context. That is the
whole of the pooled delta being 0.

## Cross-lingual penalty (`es-en` minus `es-es`)

`qwen3:8b` answer +0.056 [+0.000, +0.167], citation +0.052 [+0.010, +0.104];
`gemma4:26b-a4b` +0.000 [+0.000, +0.000], +0.010 [+0.000, +0.031];
`qwen3:14b` +0.056 [-0.083, +0.250], -0.094 [-0.177, -0.031];
`qwen3.6:35b-a3b` -0.028 [-0.083, +0.000], +0.021 [+0.000, +0.052]. Only
`qwen3:14b`'s citation penalty is entirely below 0.

## Deviations from the registration

None in what was run: the models, seeds, call, timeout, cache use and
commands are those registered, and the harness is unchanged. Two disclosures:

- The comparison report also prints a "kept / dropped" halving line per role
  (for example "kept: `gemma4:26b-a4b`"). Halving was dropped by registration
  (P2) and plays no part; only `select` and the vetoes decide.
- The report's sufficiency table is empty by construction (the sufficiency
  call was not made, P3).

## What this does not show

- It does not show that `gemma4:26b-a4b` is as good an answerer as
  `qwen3:8b`: with 36 anchored items the interval is about +-0.11 wide, so a
  loss or gain of that size is not excluded. The registered rule asks for a
  proven gain, and there is none.
- Thirty-six anchored items per run (12 per set), small synthetic bundles, a
  metric that checks named facts and not prose; `es-built` weak for every
  model.
- English questions were not rerun (P6). #1581 covers `qwen3:8b` and
  `gemma4:26b-a4b` only; `qwen3:14b` and `qwen3.6:35b-a3b` have no English
  measurement, and neither is selected.
- `qwen3.6:27b` was not rerun (P5): V2 failure at #1560 stage 0, not affected
  by a prompt change.

## What adoption would need

Nothing is adoptable from this result: the rule selected no model, so this
branch proposes no ADR and changes no default. For the record, `gemma4:26b-a4b`
would be the only possible answerer (all vetoes hold, 0.78x the baseline's
latency, already resident for sufficiency under ADR-0076, which removes the
check-to-synthesis model swap that ADR-0076 left unmeasured on the 32 GB
floor). Moving synthesis to it would need a **new registration**, not a
reinterpretation of this one: a non-inferiority question with a stated margin
and enough anchored items to resolve it (this screen's 36 cannot), then an ADR
in the style of ADR-0076 (status Proposed), a decision on a task key for
synthesis versus changing the global `model:`, and the end-to-end latency and
memory checks of ADR-0076's adoption bar. None of that is claimed here.

## Next

- Thesis (jasonssdev/mcd-afg#143): on this screen nothing displaces
  `qwen3:8b` as the answering model, now also under the language-neutral
  prompt.
- Optional follow-up, only if a one-model layout is worth pursuing: a
  non-inferiority registration for `gemma4:26b-a4b` as answerer with a larger
  anchored item set.
