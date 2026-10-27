# Pre-registration (DRAFT): the edge-typing model screen (#1563)

**Status: DRAFT. NOT REGISTERED. Blocked on the repository owner's blind
adjudication of the labels.** Nothing below binds until a final version of this
file, with the numbers marked `TO RECOMPUTE` replaced, is merged to `main` before
any scored model call (#1556: pre-register, and merge the pre-registration, before
any run). No model has been called in preparing this draft; every number in it was
computed by `run_edge_typing_screen.py forecast` or `--self-test`, which contact
none. A measurement: it adopts nothing and changes no production code, default,
prompt or task key. Part of #1556; refs #1563, #1557.

## Before this can be registered

1. **The owner labels the 49 items blind** with the sheet from
   `run_edge_typing_screen.py blind-sheet` and `adjudicate` compares it with the
   first author's labels. Every disagreement is resolved by the owner and recorded
   in `fixture.json` as the volatility fixture records it (`adjudication` with
   `first`, `second`, `resolved`, `reason`; the first label stays as `alt_label`).
   `ADJUDICATION.md` is then written, as the volatility screen's was.
2. **The floors, the J table and the class balance are recomputed** from the
   resolved labels (`forecast`; the values below are the first author's).
3. **The three questions in "Decisions the owner owes" are answered**, because
   each changes what the screen means.
4. **Stage 0** (below) replaces the assumed latencies and records the model
   digests (`ollama list`); neither was queried for this draft, because the
   server is reserved for another job.

## What the labels are, until the adjudication is recorded (read before any number)

**One author's.** The first author labelled all 49 items by the production prompt's
own rule and flagged an alternative on 27. The volatility screen's second
adjudication found its first author had anchored on the registry default in all
eight disagreements and moved that floor from 0.778 to 0.481; the equivalent hazard
here is `references`, the rubric-literal answer to any explicit link, which is the
label of 21 items (12 of them the good-life links) and the strongest baseline.

**The floors (the first author's labels).**

| floor | value |
| --- | --- |
| `always references`, strict | **21 / 49 = 0.429** (the reference for rule J) |
| `type signature`, strict | 12 / 49 = 0.245 |
| `always related_to`, strict | 9 / 49 = 0.184 |
| commonest label, strict | 21 / 49 = 0.429 (the same predictor) |
| lenient, in that order | 27 / 49, 29 / 49, 26 / 49 (uninformative: 27 items carry an alternative) |
| items right under no baseline | 16 / 49 (12 constructed, 4 ADR) |

**What the J bar is.** The reference predictor is wrong on 28 items. With the
harness's own bootstrap (10,000 resamples, seed 42), a model that matches
`always references` everywhere it is right and wins *k* of the 28 clears J with at
most this many losses elsewhere: win 4 lose 0, win 8 lose 1, win 10 lose 2, win 12
lose 4, win 14 lose 5, win 16 lose 6, win 18 lose 7, win 20 lose 9, win 24 lose 12,
win 28 lose 15 (the exact tolerance depends on which items are won and lost; the
`--self-test` pins win 4 clears and win 3 does not). Stated before data so a J pass
is not over-read: **a pass shows a model agrees with these labels better than
answering `references` for every link does; it does not show the model types edges
correctly.** In particular 29 of the 49 items are constructed to the rubric and
state their relation in its words, so a model can clear J on those alone;
accuracy is reported per stratum and does not decide (question 3).

**Label uncertainty.** The report gives strict and lenient accuracy for every
model, and `strict (1st)`, the same answers scored against the first author's
labels, as a sensitivity analysis. **The decision uses strict accuracy on the
resolved labels.** Lenient and the first-author sensitivity are reported and never
decide. A model whose verdict differs between the two is recorded as
label-dependent, not selected on the better one.

## Question

Is the `edge_typing` task key **justified**: does any local model type an
untyped link better than a predictor that needs no model, on a fixture whose labels
follow the production prompt's own rule and have been checked by a second person?
Secondarily: does the key need a different model than the global `qwen3:8b`, and
does a better type come with a worse direction (the older harness found the most
accurate model the most direction-blind)?

## Items, arms, runs

- **Items:** the 49 in `fixture.json` as committed at the registration commit (29
  constructed, 14 good-life, 6 ADR; 9 reversed probes; class balance in the README).
- **Arm:** `default`, the shipped prompt, one chat call per item and run through
  production's `suggest_edge_types`. There is no second arm: the prompt names no
  current type to anchor on, and direction is probed by the reversed items.
- **Runs:** n = 3 seeds (1, 2, 3) per (model, item). Seeds are the `OllamaClient`
  seed. n = 3 shows stability; it is not a power claim (the fixture, not the run
  count, limits power; `evals/edge_typing/` takes n = 15 as its floor after a model
  ranking reversed at n = 3, and `evals/contradictions/` recorded a 0.25 swing
  between two samples of one arm. This screen does not reach 15 and says so).
- **Models:** baseline `qwen3:8b` (the global default). Candidates, the other
  #1556 screens' list: `gemma4:26b-a4b`, `qwen3:14b`, `qwen3.6:27b`,
  `qwen3.6:35b-a3b`. Judge-only models are not candidates. `gemma2:27b`, the
  documented recommendation for this key (#650), is not in the list (question 2).
- Model digests: `TO RECORD` at registration (the harness stamps the full digest
  in-band on every run, and a run whose stamped digest differs from the table is
  void).

## Metrics

Computed by `edge_screen_core.summarize` / `run_edge_typing_screen.py score`:

1. **Strict accuracy** against the (resolved) labels (primary, deciding): the mean
   over items of each item's mean over runs of "answer equals the label". A failed
   call scores 0, never skipped, and so does an answer production direction-
   corrected (it describes the reverse edge).
2. **Lenient accuracy** (reported): also accepts the flagged alternative.
3. **First-author sensitivity** (reported): the same answers against the first
   author's labels.
4. Per-class recall, the gold-by-answered confusion matrix, accuracy per stratum,
   answer share per type, **specific precision** (of the answers that were one of
   the five structural types, the share right), the **trap-hit rate** and the
   `recovered` rate on the reversed probes, failure rate by kind, how often
   production withdrew a type, median and p95 latency, stability across runs.
5. The floors above, recomputed from the resolved labels.

## Decision rule

The deciding statistic is a paired difference of per-item mean strict scores,
against the resolved labels, with a 95% percentile bootstrap over items (10,000
resamples, seed 42).

- **J (justified):** a model clears J iff its paired gain over the **reference
  baseline** has a 95% lower bound above 0. The reference is the model-free
  predictor with the highest strict accuracy on the resolved labels, fixed from the
  labels alone before any run (`always references` on the first author's). If no
  model clears J the verdict is "the key is not justified on this fixture", and no
  recommendation changes; the minimum is still four wins and no loss.
- **Vetoes** (any one drops a candidate from the key): V1 failure rate
  <= baseline + 0.02 and <= 0.05 absolute; V2 median call latency <= 2.0x the
  baseline's; V3 trap-hit rate <= baseline + 0.10 (the direction hazard: the older
  harness measured the most accurate model hitting 17 of 18 traps).
- **Selection:** a candidate replaces `qwen3:8b` for this key only if its paired
  gain over the baseline has a point estimate above 0 and a 95% lower bound above
  0, no veto trips, **and** it clears J. Otherwise the baseline stays and the result
  is recorded as it is, with no retry under tuned bars (#1269 rule 4).
- **Confirmation:** a selected model is rerun with seeds 11-15 against the baseline
  under the same rule; "confirmed" means selection holds again. Adoption (a config
  default, `RECOMMENDED_TASK_MODELS`, or an ADR) is a separate decision this screen
  does not make; a J pass alone would not support one, and a selection would still
  owe the specific-precision and direction numbers a reading.

## Forecast and budget (Ollama wall-clock; ASSUMED, not measured)

Calls: 49 items x 1 arm x 3 runs = **147 per model; 735 for five models.**

Latencies are **assumptions** for this task, not measurements: #1560's answer-call
medians (`qwen3:8b` 4.75 s, `gemma4:26b-a4b` 2.83 s, `qwen3:14b` 6.57 s,
`qwen3.6:35b-a3b` 3.22 s; `qwen3.6:27b` borrows 6.57 s). A reply is a short JSON
object, so they are probably high for the 109 to 219 character documents
(`evals/edge_typing/` measured about 1.3 s per edge on `qwen3:8b`), and probably low
for the six ADR items, whose prompts run to 12,000 characters. One load per model,
60 s.

| model | calls x assumed s + load | forecast |
| --- | --- | --- |
| `qwen3:8b` | 147 x 4.75 + 60 | 12.6 min |
| `gemma4:26b-a4b` | 147 x 2.83 + 60 | 7.9 min |
| `qwen3:14b` | 147 x 6.57 + 60 | 17.1 min |
| `qwen3.6:27b` | 147 x 6.57 + 60 | 17.1 min |
| `qwen3.6:35b-a3b` | 147 x 3.22 + 60 | 8.9 min |
| **screen total** | | **about 1.06 h** |
| confirmation (5 seeds, baseline + one candidate, assuming `qwen3:14b`) | 2 x 245 calls | about 0.80 h |

**Budget: 2.5 h of Ollama wall-clock** from the first scored call; the assumed
worst case above is about 1.9 h. **Stage 0 before the screen:** three timed calls
per model on a small item and one on an ADR item, which replace the assumed
latencies in this table and set the per-call timeout (4x the baseline's median,
floor 60 s, as in #1557). If the measured total would exceed the budget the
confirmation drops to 3 seeds; if the screen itself would exceed it, the slowest
candidate is dropped before any scored call, not after. One Ollama job at a time,
models one at a time, server defaults, and this work the only user of the server.
The harness never contacts a model in `--self-test`, `forecast`, `blind-sheet` or
`adjudicate`.

## Commands

```sh
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/edge_typing_screen/run_edge_typing_screen.py forecast --runs 3
uv run python -u evals/edge_typing_screen/run_edge_typing_screen.py run --model qwen3:8b --runs 3
uv run python evals/edge_typing_screen/run_edge_typing_screen.py score
```

## Decisions the owner owes (this draft does not make them)

1. **Is `references` the right label for an explicit link that states no more?**
   The rubric says yes (SOURCE names TARGET); the suggester says `related_to` most of
   the time (67% of accepted edges on a real bundle before the rubric of #388, 59%
   of emissions on the constructed fixture after it, `evals/edge_typing/`). If the owner's blind labels are mostly `related_to` where the
   first author wrote `references`, the reference baseline changes from
   `always references` to something far weaker, J becomes easier, and the draft's
   warning about a lowered floor applies.
2. **Should `gemma2:27b` be a candidate?** It is the documented per-task
   recommendation and the only model with prior evidence on this task, on the
   constructed stratum; it is not on the volatility screen's list. Adding it needs
   a pulled model and a latency assumption.
3. **Pooled or stratified?** The registered statistic is pooled over all 49 items.
   The constructed stratum (29 items) was written to the rubric and the good-life
   stratum is 14 links among 7 documents, so a pooled pass can come from the
   easiest 29. Stratified accuracy is reported either way; whether a model that
   clears J only on the constructed stratum may be selected is a rule this draft
   leaves open rather than inventing.

## Known limits (carried from the README)

One author's labels until the adjudication; the rubric is the production prompt's,
so the fixture measures agreement with that rule; `references` dominates; 27 items
ambiguous by the first author's flag; `caused_by` and `part_of` have 3 items each;
the good-life items are not independent; direction is probed on nine constructed
items; bodies range from 109 to 6,388 characters; no `Place`, `Entity`, `Insight`,
`Event` or `Procedure` endpoint; English only.
