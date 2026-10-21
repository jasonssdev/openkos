# DRAFT pre-registration: the volatility-typing model screen (#1563)

**Status: DRAFT. This is NOT a registration.** Nothing here is registered until
a later commit replaces this file with a `PREREGISTRATION.md` that says so, made
before the first scored model call. Until then every number, bar and model below
may change. No model has been run through the harness. Part of #1556; refs
#1563, #1557. A measurement: it adopts nothing and changes no production code,
default, prompt or task key.

**Blocking before registration:** a second adjudicator labels all 27 items,
blind to the first author's labels and to the registry defaults, and the
disagreements are resolved and recorded (see `README.md`, "What the fixture
cannot show"). A registration over one author's labels would repeat the
adjudication-debt mistake of #1557's Spanish fixtures.

## Question

Is the `volatility_typing` task key **justified**, that is: does any local model
type a concept type's volatility better than simply using the type's registry
default, on a fixture whose labels follow ADR-0007's rubric? And, secondarily,
does the task key need a different model than the global `qwen3:8b`?

## Items, arms, runs

- **Items:** the 27 in `fixture.json` (9 static, 12 slow, 6 volatile; 16 with a
  flagged alternative), as committed at the registration commit.
- **Arms:** `default` (the shipped prompt) and `wrong-anchor` (the shown tier is
  neither the label nor its alternative). Both on every model.
- **Runs:** n = 3 seeds (1, 2, 3) per (model, arm, item). Seeds are the
  `OllamaClient` seed. Replies are short and most variance is the model's, so
  n = 3 is the minimum that shows stability; it is not a power claim (the
  fixture, not the run count, limits power).
- **Models:** baseline `qwen3:8b` (the global default). Candidates, taken from
  the other #1556 screens (concept, window, Ask): `gemma4:26b-a4b`, `qwen3:14b`,
  `qwen3.6:27b`, `qwen3.6:35b-a3b`. The digest each tag resolves to is recorded in
  the registration. Judge-only models are not candidates.

## Metrics

Computed by `screen_core.summarize` / `run_volatility_screen.py score`:

1. **Strict accuracy** on the `default` arm (primary): the mean over items of
   each item's mean over runs of "answer equals the primary label". A failed call
   scores 0, never skipped.
2. **Lenient accuracy** (reported, not deciding): also accepts the flagged
   alternative.
3. Per-class recall and the confusion matrix; failure rate by kind; median and
   p95 latency; anchor-follow rate (`wrong-anchor` arm); agreement with the
   registry default; stability across runs.
4. The floors: the registry predictor (0.778 strict, 1.000 lenient on the
   committed labels) and the commonest label (0.444).

## Decision rule (as drafted)

The deciding statistic is a paired difference of per-item mean strict scores on
the `default` arm, with a 95% percentile bootstrap over items (10,000
resamples, seed 42).

- **J (justified):** a model clears J iff its paired gain over the registry
  predictor has a 95% lower bound above 0. If no model clears J the verdict is
  "the key is not justified on this fixture", and the registry default stays the
  better-evidenced choice. Because the registry is wrong on only six items, J
  needs the model to win at least four of them and lose none of the rest
  (harness-checked); this is a deliberately hard bar, stated now so a null is not
  later read as a model failure.
- **Vetoes** (any one drops a candidate from the key): V1 failure rate
  <= baseline + 0.02 and <= 0.05 absolute; V2 median call latency <= 2.0x the
  baseline's; V3 anchor-follow rate <= baseline + 0.10.
- **Selection:** a candidate replaces `qwen3:8b` for this key only if its paired
  gain over the baseline has a point estimate above 0 and a 95% lower bound above
  0, no veto trips, **and** it clears J. Otherwise the baseline stays and the
  result is recorded as it is, with no retry under tuned bars (#1269 rule 4).
- **Confirmation:** a selected model is rerun with seeds 11-15 against the
  baseline under the same rule; "confirmed" means selection holds again.
  Adoption (a config default or an ADR) is a separate decision this screen does
  not make, and needs the second adjudicator's labels first.

## Forecast and budget (Ollama wall-clock; assumed, not measured)

Calls: 27 items x 2 arms x 3 runs = **162 per model; 810 for five models.**

Latencies are **assumptions**, not measurements for this task: #1560's
answer-call medians (`qwen3:8b` 4.75 s, `gemma4:26b-a4b` 2.83 s, `qwen3:14b`
6.57 s, `qwen3.6:35b-a3b` 3.22 s; `qwen3.6:27b` borrows 6.57 s). A volatility
reply is a short JSON object, so these are probably high. One load per model, 60
s.

| model | calls x assumed s + load | forecast |
| --- | --- | --- |
| `qwen3:8b` | 162 x 4.75 + 60 | 13.8 min |
| `gemma4:26b-a4b` | 162 x 2.83 + 60 | 8.6 min |
| `qwen3:14b` | 162 x 6.57 + 60 | 18.7 min |
| `qwen3.6:27b` | 162 x 6.57 + 60 | 18.7 min |
| `qwen3.6:35b-a3b` | 162 x 3.22 + 60 | 9.7 min |
| **screen total** | | **about 1.2 h** |
| confirmation (5 seeds, baseline + one candidate, assuming `qwen3:14b`) | 2 x 270 calls | about 0.9 h |

**Budget: 2.5 h of Ollama wall-clock** from the first scored call; the worst case
above is about 2.1 h. **Stage 0 before the screen:** three timed calls per model,
which replace the assumed latencies in this table and set the per-call timeout
(4x the baseline's median, floor 60 s, as in #1557). If the measured total would
exceed the budget the confirmation drops to 3 seeds; if the screen itself would
exceed it, the slowest candidate is dropped before any scored call, not after.
One Ollama job at a time, models one at a time, server defaults, and this work the
only user of the server. The harness never contacts a model in `--self-test` or
`forecast`.

## Commands

```sh
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/volatility_typing_screen/run_volatility_screen.py forecast --runs 3
uv run python -u evals/volatility_typing_screen/run_volatility_screen.py run --model qwen3:8b --runs 3
uv run python evals/volatility_typing_screen/run_volatility_screen.py score
```

## Known limits (carried from the README)

One author's labels; the rubric is the registry's own, so the fixture only
discriminates on six items; Place, Entity and Insight are uncovered; bodies are
short; seven items are Spanish and are scored with the rest (language is
recorded per item; a language split is not part of the rule).
