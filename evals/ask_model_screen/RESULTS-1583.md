# Results: a language-neutral refusal in `answer/system` (#1583)

**Verdict: ADOPT.** Every registered check holds for both gating models. The
rule and the single changed sentence are in
[`PREREGISTRATION-1583.md`](PREREGISTRATION-1583.md), committed before any
treated-arm call. The harness was not changed after that commit.

## Run

- 2026-10-20, 07:22 to 08:07 EST (about 45 min of Ollama wall-clock against
  the forecast of 48 min and the 3 h cap), Apple Silicon, Ollama 0.32.9,
  `OLLAMA_NUM_PARALLEL` at the default. Models one at a time.
- Answer call only, seeds 1 and 2, 96 items each, `num_ctx` 12288,
  `num_predict` 8192, thinking off, timeout 60 s. Model digests are those of
  the #1560 registration. 192 cache misses per model, 0 failures or timeouts.
- Control: the generations #1560 cached for the production prompt (seeds 1
  and 2); the treated arm differs only in the one sentence.
- Raw generations and projections are outside the repository under
  `~/Dev/Projects/openkos-eval-runs/ask-screen/` (`compare-prompt-refusal-lang-runs2.md`).

## Measured

| model | arm | adjacent in Spanish | adjacent with the English phrase | grounded in Spanish | answer recall | citation | `USED:` compliance | answer failures |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `qwen3:8b` | control | 0.883 | 0.117 | 0.924 | 0.833 | 0.865 | 1.000 | 0.000 |
| `qwen3:8b` | treated | 0.933 | 0.067 | 0.977 | 0.833 | 0.885 | 1.000 | 0.000 |
| `gemma4:26b-a4b` | control | 0.250 | 0.750 | 1.000 | 0.833 | 0.906 | 0.974 | 0.000 |
| `gemma4:26b-a4b` | treated | 0.983 | 0.017 | 0.985 | 0.833 | 0.927 | 0.990 | 0.000 |
| `qwen3:14b` (not gating) | control | 0.467 | 0.533 | 0.886 | 0.736 | 0.880 | 1.000 | 0.000 |
| `qwen3:14b` (not gating) | treated | 0.750 | 0.250 | 0.909 | 0.750 | 0.865 | 0.979 | 0.000 |

Paired item-level change in adjacent Spanish (95% bootstrap):
`qwen3:8b` +0.050 [-0.017, +0.117]; `gemma4:26b-a4b` **+0.733 [+0.600,
+0.850]**; `qwen3:14b` +0.283 [+0.183, +0.383].

## Rule

| check | result |
| --- | --- |
| E1 `gemma4:26b-a4b` adjacent Spanish >= 0.80 and lower bound > 0 | 0.983, lower bound +0.600: pass |
| N1 adjacent Spanish change >= -0.05 | `qwen3:8b` +0.050, `gemma4:26b-a4b` +0.733: pass |
| N2 grounded Spanish change >= -0.05 | +0.053, -0.015: pass |
| N3 answer recall change >= -0.05 | 0.000, 0.000: pass |
| N4 citation change >= -0.05 | +0.020, +0.021: pass |
| N5 failures and compliance | 0.000 failures; compliance 0.000, +0.016: pass |

## Reading it, and what it does not show

- The default does not regress on any measured quantity, and its Spanish
  refusals rise slightly (not significantly: the interval includes 0).
- `qwen3:14b` improves too (0.467 to 0.750) without reaching the floor the
  effect model met; it carried no decision. Its citation change is -0.015 and
  its compliance -0.021, inside the margin it would have had to meet.
- The language measure is the repository's function-word voter; a very short
  refusal it cannot vote on counts as not Spanish, so the treated figures are,
  if anything, understated.
- 60 adjacent answers per model over two seeds, over the same ten adjacent
  questions in three bundles; the checks are non-inferiority at a 0.05 margin,
  not equivalence. English questions are not measured (#1581), and
  `qwen3.6:35b-a3b` was not run.
- This does not by itself make any model selectable for the answer role: that
  needs #1560's remaining vetoes re-measured with the new prompt, a separate
  question this result does not answer.
