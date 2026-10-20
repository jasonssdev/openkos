# Results: the English-question arm of the Ask screen (#1581)

**Verdict: no language penalty is detectable for either model, and the #1560
sufficiency gain of `gemma4:26b-a4b` is not shown on English questions.** The
rule is in [`PREREGISTRATION-1581.md`](PREREGISTRATION-1581.md), committed
before any English-arm call; the harness was not changed after that commit.
Nothing here changes a default, a prompt or a task key.

## Run

- 2026-10-20, 10:03 to 10:30 EST, about 27 min of Ollama wall-clock against a
  forecast of 29 min and the 3 h cap; Ollama 0.32.9, `OLLAMA_NUM_PARALLEL` at
  the default; the models one at a time.
- Contexts: `bge-m3` only, 32 items in each of `en-en` and `en-built`, none
  without context. `built_bundle_sha256` is `59b21054aed1...`, unchanged from
  #1560; 7 of the 12 anchored English questions on `en-built` have no document
  gold in their context, the same 7 as on `es-built`.
- Generations: seeds 1 and 2, both calls, `num_ctx` 12288, `num_predict` 8192,
  thinking off, timeout 60 s, digests as registered in #1560. 256 cache misses
  per model (128 rows, two calls), 0 failures or timeouts.
- Stacked on #1591: the English arm ran under the production `answer/system`
  of that branch (the #1583 language-neutral refusal); the Spanish sides are
  #1583's treated-arm answers and #1560's control sufficiency verdicts.
- Raw generations and projections are outside the repository under
  `~/Dev/Projects/openkos-eval-runs/ask-screen/`
  (`compare-language-runs2.md`); no generated answer is recorded here.

## Measured: absolute means (n of 32 questions; the answer role is the 12 anchored)

| model | role | es-en | en-en | es-built | en-built |
| --- | --- | ---: | ---: | ---: | ---: |
| `qwen3:8b` | sufficiency (32 of 32) | 0.891 | 0.953 | 0.719 | 0.734 |
| | answer (12 of 32) | 0.958 | 0.958 | 0.625 | 0.583 |
| | citation (32 of 32) | 0.938 | 0.938 | 0.844 | 0.812 |
| `gemma4:26b-a4b` | sufficiency (32 of 32) | 0.969 | 0.953 | 0.812 | 0.812 |
| | answer (12 of 32) | 1.000 | 1.000 | 0.500 | 0.458 |
| | citation (32 of 32) | 1.000 | 1.000 | 0.797 | 0.781 |

## Q1: the language penalty (Spanish question minus English question, paired per question)

Mean over the two seeds, 95% percentile bootstrap over questions (10,000
resamples, seed 42). Negative means a Spanish question costs.

| model | role | es-en minus en-en | es-built minus en-built |
| --- | --- | --- | --- |
| `qwen3:8b` | sufficiency | -0.062 [-0.172, +0.016] | -0.016 [-0.047, +0.000] |
| | answer | +0.000 [+0.000, +0.000] | +0.042 [-0.125, +0.250] |
| | citation | +0.000 [-0.047, +0.047] | +0.031 [-0.062, +0.141] |
| `gemma4:26b-a4b` | sufficiency | +0.016 [+0.000, +0.047] | +0.000 [+0.000, +0.000] |
| | answer | +0.000 [+0.000, +0.000] | +0.042 [+0.000, +0.125] |
| | citation | +0.000 [+0.000, +0.000] | +0.016 [-0.062, +0.094] |

Every interval includes 0 or touches it from the positive side; no penalty is
entirely below 0. The largest point estimate is the baseline's sufficiency over
the English mirror, -0.062 (it refuses answerable questions a little more often
when asked in Spanish), with an interval of about 0.19 wide. This agrees with
#1560's `es-en` minus `es-es` finding, now against the English question as well.

## Q2: does the #1560 sufficiency choice hold in English? (candidate minus baseline, English questions)

| role | set | delta [95% CI] |
| --- | --- | --- |
| sufficiency | `en-en` (32 of 32) | +0.000 [-0.094, +0.094] |
| sufficiency | `en-built` (32 of 32) | +0.078 [-0.016, +0.203] |
| sufficiency | pooled (64 of 64) | +0.039 [-0.031, +0.109] |
| answer | pooled (12 of 12 anchored per set, 24 of 24) | -0.042 [-0.146, +0.042] |
| citation | pooled (64 of 64) | +0.016 [-0.070, +0.102] |

The registered reading applies: the pooled sufficiency lower bound is at or
below 0, so **the gain is not shown in English**, and the upper bound is not
below 0, so there is no English regression either. The difference from the
Spanish questions (+0.078 round 2, +0.096 confirmation, both with lower bounds
above 0) comes from the baseline, which does better on `en-en` (0.953) than on
`es-en` (0.891), while `gemma4:26b-a4b` is flat across languages (0.953 to
0.969). The sufficiency check #1560 selected is no worse than the baseline in
English; the margin it showed was a Spanish-question margin for `qwen3:8b`.

## Q3: does the shipped refusal follow an English question?

Share of answered items whose dominant language is the question's:

| model | set | adjacent | grounded |
| --- | --- | ---: | ---: |
| `qwen3:8b` | `en-en` / `en-built` | 1.000 / 1.000 | 1.000 / 1.000 |
| `gemma4:26b-a4b` | `en-en` / `en-built` | 1.000 / 1.000 | 1.000 / 0.977 |

Against Spanish questions under the same prompt: `qwen3:8b` 0.900 adjacent on
both `es-en` and `es-built`, `gemma4:26b-a4b` 1.000. Counts are 20 adjacent
and 44 grounded answers per model and set (10 and 22 questions over two
seeds). The pre-registered floor of 0.80 is met by both models on both sets:
the language-neutral wording follows an English question. The one gemma answer
not in English (1 of 44 grounded on `en-built`) is not a refusal.

Failures: 0 of 128 answer calls and 0 of 128 sufficiency calls per model on the
English sets; `USED:` compliance 1.000 for both.

## What this does not show

- 32 questions per set, ten of them adjacent and the same ten in both bundles;
  the answer role rests on 12 anchored questions. A penalty smaller than
  roughly 0.1 on a role is below what these intervals can see.
- The translation is one author's; the English twins keep every label and gold
  token, and `check_english_questions` confirms no token leaks into a question,
  but a different phrasing could move a retrieval rank.
- Two small synthetic bundles; `en-built` is weak for every model because the
  compiled bundle drops 7 of the 12 anchored evidence phrases.
- `qwen3:14b` and `qwen3.6:35b-a3b` were not run, as registered, so nothing is
  said about them in English.
- The Spanish sides are the generations #1560 and #1583 cached (same seeds,
  same digests, byte-identical answer prompt); they were not regenerated.

## Next

- #1581 is answered: English-question sets exist, are measured for the
  baseline and the #1560 sufficiency choice, and show no language penalty.
- No default changes. If #1560's sufficiency swap is adopted, the case for it
  rests on Spanish questions; in English it is neutral against `qwen3:8b`.
