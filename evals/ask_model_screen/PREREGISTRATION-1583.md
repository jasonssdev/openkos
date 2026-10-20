# Pre-registration: a language-neutral refusal in `answer/system` (#1583)

**Status: REGISTERED when this file is committed, before any scored model call
of the treated arm.** A change after that commit is a new registration, not an
edit. Follows [`PREREGISTRATION.md`](PREREGISTRATION.md) (#1560, whose
[`RESULTS.md`](RESULTS.md) found the problem) and reuses its harness,
contexts, cache and metrics. Refs #1560, #1556.

## Question and hypothesis

`answer/system.md` gives the model an English example refusal: *an honest "the
compiled bundle does not cover this" is the correct answer when the context is
insufficient*. In #1560 round 1, `gemma4:26b-a4b` and `qwen3:14b` answered 21
and 16 of the 30 unanswerable Spanish questions with that sentence in English,
verbatim; `qwen3:8b` paraphrased it in Spanish (27 of 30 in Spanish).

**H1.** Rewording the sentence so that it asks for the refusal *in the
question's language*, and no longer quotes a sentence to copy, makes the larger
models refuse in Spanish, **without** changing what `qwen3:8b` does.

## The change under test (the treated arm)

Exactly one sentence of `answer/system.md`; every other byte, including the
attribution paragraph, is identical (`candidate-answer-system-1583.md`, a copy
of the production file with this one replacement):

- control: `say so plainly rather than guessing; an honest "the compiled bundle
  does not cover this" is the correct answer when the context is insufficient.`
- treated: `say so plainly rather than guessing: state, in the language of the
  question, that the compiled bundle does not cover it. That honest refusal is
  the correct answer when the context is insufficient, and it is written in the
  question's language, never copied from these instructions.`

The treated file lives in `evals/` until the verdict. Production
(`src/openkos/prompts/answer/system.md`) changes only if the rule below says
adopt, and then to a byte-identical copy.

## Design

- **Items.** The 96 items of #1560 (32 Spanish questions over `es-es`,
  `es-en`, `es-built`; 30 adjacent, 66 grounded, 36 anchored), the stored
  `contexts.json`. Only the system message differs between arms; the user
  messages are byte-identical.
- **Calls.** The synthesis (answer) call only. The sufficiency call uses a
  different system prompt and is unaffected (`--calls answer`).
- **Control.** The generations #1560 already cached for the production prompt:
  seeds 1 and 2 of each model. Nothing is regenerated for the control.
- **Treated.** The same models, seeds 1 and 2, `num_ctx` 12288,
  `num_predict` 8192, thinking off, model-default sampling, timeout 60 s (the
  #1560 stage-0 value for every model), digests as registered in #1560. The
  cache key includes the exact messages, so the treated arm is a new key.
- **Models.** `qwen3:8b` (baseline, gating), `gemma4:26b-a4b` (effect model,
  gating), `qwen3:14b` (reported, not gating). `qwen3.6:35b-a3b` has a control
  for seed 1 only and is **not run**: it showed the problem least (3 of 19
  non-Spanish answers quote the phrase), and a one-seed arm cannot be paired
  with the others' n = 2.
- **n = 2 seeds** per model (96 items each, 192 rows per arm and model).

## Metrics

Per item and run; a failed call scores 0, never skipped. "Language" is the
repository's `_dominant_language` over the prose before the `USED:` line,
function-word voting, Spanish or English or neither; a reply it cannot vote on
(a very short refusal) is **not** counted as Spanish, which is conservative
for the treated arm.

| id | metric |
| --- | --- |
| adjacent Spanish | share of adjacent (unanswerable) answers whose dominant language is Spanish: **the primary metric** |
| English phrase | share of adjacent answers containing `does not cover`, case-insensitive (secondary; the verbatim quote the issue counted) |
| grounded Spanish | the same share on grounded answers |
| answer recall | the #1560 answer-role score: share of derived gold tokens stated, anchored items |
| citation | the #1560 citation-role score: grounded, a reported `USED:` naming a gold document; adjacent, `USED: none` (this is the refusal-correctness metric: a refusal that still cites is wrong) |
| compliance, failures | `USED:` line reported; answer-call failure rate |

## Pass / fail rule (adopt only if every check holds)

Margin `m = 0.05`. "Change" is treated minus control, pooled over the n = 2
runs. The gating models are `qwen3:8b` and `gemma4:26b-a4b`.

| id | applies to | rule |
| --- | --- | --- |
| E1 | `gemma4:26b-a4b` | adjacent Spanish **>= 0.80** (control 0.30) and the paired item-level 95% bootstrap lower bound of its change is **above 0** (10,000 resamples, seed 42, items paired across arms) |
| N1 | both | adjacent Spanish change >= -m (the baseline's 0.90 must not fall) |
| N2 | both | grounded Spanish change >= -m |
| N3 | both | answer recall change >= -m |
| N4 | both | citation change >= -m |
| N5 | both | answer failures <= control + 0.02; `USED:` compliance change >= -m |

**Adopt** iff E1 and every N-check hold for both gating models. Then, and only
then, `answer/system.md` takes the treated sentence in a separate commit with
its prompt-hash pin updated. **Otherwise nothing is adopted**: the result is
recorded as it is, and the rule is not retuned or the sentence reworded and
rerun under this registration (#1269 rule 4); a different wording is a new
registration. `qwen3:14b` is reported beside them and cannot decide.

Limits stated now. Thirty adjacent answers per seed (60 per model over two
seeds) is small, and the three sets share the same ten adjacent questions, so
the intervals are wide; E1 is an effect-size floor, and N1 to N5 are
point-estimate non-inferiority checks, not proofs of equivalence. English
questions are not measured (#1581). The two bundles are small synthetic
corpora.

## Forecast and budget

Answer call only, from #1560's measured medians: `qwen3:8b` 4.75 s, `gemma4:26b-a4b`
2.83 s, `qwen3:14b` 6.57 s per item, 96 items per run, plus about 1 minute per
model load.

| step | forecast |
| --- | --- |
| `qwen3:8b`, 2 runs | 15 min |
| `gemma4:26b-a4b`, 2 runs | 10 min |
| `qwen3:14b`, 2 runs | 22 min |
| **total Ollama wall-clock** | **about 0.8 h (48 min), well under the 3 h cap** |

One Ollama job at a time; `OLLAMA_NUM_PARALLEL` at the server default.

## Commands

```
uv run python -u evals/ask_model_screen/run_ask_model_screen.py --stage generate \
    --model <m> --runs 2 --calls answer --timeout 60 \
    --arm refusal-lang --system-prompt-file evals/ask_model_screen/candidate-answer-system-1583.md
uv run python -u evals/ask_model_screen/run_ask_model_screen.py --stage compare-prompt \
    --candidates qwen3:8b,gemma4:26b-a4b,qwen3:14b --arm refusal-lang --runs 2
```

Generations, projections and the comparison report stay outside the
repository under `~/Dev/Projects/openkos-eval-runs/ask-screen/`; the results
file records numbers, never a generated answer.
