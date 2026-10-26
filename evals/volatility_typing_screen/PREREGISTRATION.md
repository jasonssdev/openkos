# Pre-registration: the volatility-typing model screen (#1563)

**Status: REGISTERED at the commit that merges this file to `main`, before any
scored model call.** From that commit the arms, items, models, metrics, bars,
vetoes, selection rule and budget below are fixed; changing one after a scored
call needs a new registration, and a result is then recorded as it is, never
re-run under tuned bars (#1269 rule 4). No model has been run through the
harness. Part of #1556; refs #1563, #1557. A measurement: it adopts nothing and
changes no production code, default, prompt or task key.

The labels were adjudicated by a second person before registration; the record,
the agreement and the effect on the floors are in
[`ADJUDICATION.md`](ADJUDICATION.md). Read it with the three sections below,
which say before the data what the new floors do and do not mean.

## What the adjudication changed (read before any number)

**The floors.** Against the resolved labels (recomputed from `fixture.json`):

| floor | value |
| --- | --- |
| registry predictor, strict | **13 / 27 = 0.481** (it was 21 / 27 = 0.778 on the first author's labels) |
| registry predictor, lenient | 27 / 27 = 1.000 (uninformative: the registry matches one of the two adjudicators on every item) |
| commonest label, strict | **11 / 27 = 0.407** (slow 11, static 9, volatile 7) |
| sensitivity: registry predictor against the first author's labels | 21 / 27 = 0.778 |

**The J bar is now far easier, and this is stated before data so a J pass is
not over-read.** The registry predictor is wrong on **14** items, not 6. With the
harness's own bootstrap (10,000 resamples, seed 42), a model that matches the
registry everywhere it is right and wins k of the 14 clears J with at most this
many losses elsewhere: win 4 lose 0, win 7 lose 1, win 9 lose 2, win 11 lose 3,
win 13 lose 4, win 14 lose 5. Eight of those 14 wins exist only because the
second adjudicator disagreed with the first author on an item where the first
author's label equalled the registry. A J pass therefore shows that a model
agrees with the **resolved** labels better than the registry does; it does not
show that the model types volatility correctly, and it is not evidence for
adoption by itself.

**The anchoring finding.** In all eight disagreements the first author's label
equalled the registry default of the item's type. Two readings fit: (1) the first
author anchored on the registry, and the blind second adjudicator, who never saw
the type, did not; (2) the registry default is wrong for some types (for example
`Procedure`, default `volatile`, where the owner labelled both Procedure items
`slow`). **This screen does not decide between them.** Resolving every
disagreement to the second adjudicator's label is the owner's decision, not a
finding that reading (2) is true; if reading (1) holds, part of the lowered
registry floor is the first author's anchoring made visible and not a registry
defect.

**Label uncertainty.** Cohen's kappa between the two adjudicators is **0.544**
(19 / 27 raw). The labels are a measurement of two people's reading of ADR-0007's
rubric, not a truth. Consequently:

- the report gives **both** strict and lenient accuracy for every model;
- the report also gives a **sensitivity analysis**: strict accuracy of the same
  `default`-arm answers scored against the **first author's** labels
  (`strict (1st)` in `score`), and the registry floor on them (0.778);
- the **decision still uses strict accuracy on the resolved labels** (below).
  Lenient and the first-author sensitivity are reported and never decide. A
  model whose verdict differs between resolved-strict and the sensitivity is
  recorded as label-dependent in the result, not selected on the better one.

## Question

Is the `volatility_typing` task key **justified**, that is: does any local model
type a concept type's volatility better than simply using the type's registry
default, on a fixture whose labels follow ADR-0007's rubric? And, secondarily,
does the task key need a different model than the global `qwen3:8b`?

## Items, arms, runs

- **Items:** the 27 in `fixture.json` (9 static, 11 slow, 7 volatile after
  resolution; 17 with a flagged alternative), as committed at the registration
  commit.
- **Arms:** `default` (the shipped prompt) and `wrong-anchor` (the shown tier is
  neither the label nor its alternative). Both on every model. The anchor is
  computed from the resolved label and the first author's label (kept as the
  alternative) where there is one, so an anchor follower is wrong on both.
- **Runs:** n = 3 seeds (1, 2, 3) per (model, arm, item). Seeds are the
  `OllamaClient` seed. Replies are short and most variance is the model's, so
  n = 3 is the minimum that shows stability; it is not a power claim (the
  fixture, not the run count, limits power).
- **Models:** baseline `qwen3:8b` (the global default). Candidates, taken from
  the other #1556 screens (concept, window, Ask): `gemma4:26b-a4b`, `qwen3:14b`,
  `qwen3.6:27b`, `qwen3.6:35b-a3b`. Judge-only models are not candidates.

Model digests, as `ollama list` reported them on the registering machine (the
short ID; the harness stamps the full digest in-band on every run and a run whose
stamped digest differs from this table is void):

| tag | digest (short ID) |
| --- | --- |
| `qwen3:8b` | `500a1f067a9f` |
| `gemma4:26b-a4b` | `001e5dafc3c7` |
| `qwen3:14b` | `bdbd181c33f2` |
| `qwen3.6:27b` | `bcbdbd4bcf86` |
| `qwen3.6:35b-a3b` | `a7eb95c53bcf` |

## Metrics

Computed by `screen_core.summarize` / `run_volatility_screen.py score`:

1. **Strict accuracy** on the `default` arm against the resolved labels
   (primary, deciding): the mean over items of each item's mean over runs of
   "answer equals the resolved label". A failed call scores 0, never skipped.
2. **Lenient accuracy** (reported, not deciding): also accepts the flagged
   alternative.
3. **First-author sensitivity** (reported, not deciding): strict accuracy of the
   same answers against the first author's labels.
4. Per-class recall and the confusion matrix; failure rate by kind; median and
   p95 latency; anchor-follow rate (`wrong-anchor` arm); agreement with the
   registry default; stability across runs.
5. The floors: the registry predictor (0.481 strict, 1.000 lenient on the
   resolved labels; 0.778 on the first author's) and the commonest label
   (0.407).

## Decision rule

The deciding statistic is a paired difference of per-item mean strict scores on
the `default` arm, against the resolved labels, with a 95% percentile bootstrap
over items (10,000 resamples, seed 42).

- **J (justified):** a model clears J iff its paired gain over the registry
  predictor has a 95% lower bound above 0. If no model clears J the verdict is
  "the key is not justified on this fixture", and the registry default stays the
  better-evidenced choice. The loss tolerances are in the table above; the
  minimum is still four wins and no loss.
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
  not make; given the anchoring finding above it would also need the owner to
  settle which of the two readings holds for the types involved.

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

Two adjudicators, kappa 0.544; the rubric is the registry's own, so the fixture
discriminates on the 14 items where the registry is wrong, 8 of them by
resolution of a disagreement; Place, Entity and Insight are uncovered; bodies are
short; seven items are Spanish and are scored with the rest (language is
recorded per item; a language split is not part of the rule).
