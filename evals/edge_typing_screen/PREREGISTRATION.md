# Pre-registration: the edge-typing model screen (#1563)

**Status: REGISTERED when this file merges to `main`, which happens before any scored
model call (#1556: pre-register, and merge the pre-registration, before any run).** The labels
are adjudicated (see "Third adjudication"), the owner's questions are decided (see "Decisions
the owner made"), the J table is recomputed and Stage 0 is done. Everything below binds from
the merge; a change after it is a new registration, not an edit to this one. No scored call has
been made: Stage 0 called only calibration inputs that are not fixture items. A measurement:
it adopts nothing and changes no production code, default, prompt or task key. Part of #1556;
refs #1563, #1557.

## Before this could be registered

1. **Done, with two deviations (below).** The 49 items were labelled blind a second
   time with the sheet from `run_edge_typing_screen.py blind-sheet`, by a Claude model
   rather than the owner, and the 11 disagreements were resolved by a third model
   (Claude Opus) on the owner's delegation, recorded in `fixture.json` as the
   volatility fixture records them (`adjudication` with `first`, `second`, `resolved`,
   `reason`; the losing label stays as `alt_label`), and listed in
   [`ADJUDICATION.md`](ADJUDICATION.md).
2. **Done** for the floors and the class balance (`forecast`, below) and for the J
   tolerance table (`j-table`, below), both on the resolved labels; `--self-test`
   pins that four wins and no loss clear J and three do not.
3. **Done.** The three questions are decided (owner, 2026-10-27; see "Decisions the
   owner made").
4. **Done.** Stage 0 (below) measured the latencies and recorded the model digests; the
   forecast is recomputed from them and is within the budget.

## Second labelling: recorded, with a deviation (read first)

**The owner did not label this sheet.** The owner delegated the blind labelling, and a
Claude model labelled the sheet blind (it read only the blind sheet, none of
`fixture.json`, the README, this draft or any other label). Agreement below is therefore
measured against a **Claude labeller, not the owner**, so it is a statement about two
readings of the same rubric, not about the owner's judgment. The owner's own review of
every disagreement, and every resolution, was not performed before registration (the
owner delegated it, and it is not one of the preconditions above); the owner can still
override a resolution, but only as a recorded amendment before any scored call. The labels were imported with
`run_edge_typing_screen.py import-sheet` into [`second_labels.json`](second_labels.json) (labels keyed by
fixture id, plus the labeller and seed). The filled sheet itself is not committed, per
the README.

**Result** (`adjudicate`): 38 of 49 agree (0.776), Cohen's kappa 0.696, 11 disagreements.
In all 11 the Claude label is the first author's own flagged alternative. Five are the
reversed probes `e21`, `e22`, `e23`, `e27`, `e29` (first `references`, Claude
`related_to`); `g01` and `g02` (first `related_to`, Claude `references`); and four ADR items,
`r01`, `r02`, `r03` (first `depends_on`) and `r04` (first `caused_by`), where Claude
answered `references`. The `references`-versus-`related_to` boundary, the hazard this draft
names, is where the two labellers split.

**What this did not do.** It resolved nothing; the third adjudication below did.

## Third adjudication: recorded, with a deviation (read first)

**Deviation: the disagreements were adjudicated by a third model (Claude Opus) on the
owner's delegation, blind to annotator identity**, not by the owner. For each of the 11
items the adjudicator read only the two documents, the production rule verbatim and the
two candidate labels in sorted order, without knowing which annotator gave which and
without reading either annotator's notes or reasoning, any stored model run, result or
score; its verdicts were written down before the sources were revealed. Each resolution
is the item's `adjudication` record in `fixture.json` (`first`, `second`, `resolved`,
`reason`), and [`ADJUDICATION.md`](ADJUDICATION.md) lists them. 8 resolved to the first
author's label (`e21`, `e22`, `e23`, `e27`, `e29`: `references`; `r01`, `r03`:
`depends_on`; `r04`: `caused_by`), 3 to the second (`g01`, `g02`: `references`; `r02`:
`references`). The losing label stays as `alt_label`, so lenient scoring still accepts
it. No registered margin or decision rule changed. The owner may override any resolution
at registration.

## What the labels are (read before any number)

**Adjudicated, one author's first.** The first author labelled all 49 items by the production prompt's
own rule and flagged an alternative on 27. The volatility screen's second
adjudication found its first author had anchored on the registry default in all
eight disagreements and moved that floor from 0.778 to 0.481; the equivalent hazard
here is `references`, the rubric-literal answer to any explicit link, which is the
label of 21 items (12 of them the good-life links) and the strongest baseline.

**The floors (the resolved labels; `forecast`).** The first author's values are in
brackets.

| floor | value |
| --- | --- |
| `always references`, strict | **24 / 49 = 0.490** (the reference for rule J) [21 / 49 = 0.429] |
| `type signature`, strict | 10 / 49 = 0.204 [12 / 49 = 0.245] |
| `always related_to`, strict | 7 / 49 = 0.143 [9 / 49 = 0.184] |
| commonest label, strict | 24 / 49 = 0.490 (the same predictor) [21 / 49] |
| lenient, in that order | 27 / 49, 29 / 49, 26 / 49 (unchanged; uninformative: 27 items carry an alternative) |
| items right under no baseline | 15 / 49 [16 / 49] |

Class balance (resolved): `caused_by` 3, `depends_on` 4, `member_of` 4, `part_of` 3,
`produced_by` 4, `references` 24, `related_to` 7 (first author's: `depends_on` 5,
`references` 21, `related_to` 9).

**What the J bar is.** The reference predictor is wrong on 25 items (28 on the first
author's labels). The tolerance table below is `run_edge_typing_screen.py j-table`
(no model) on the resolved labels. With the
harness's own bootstrap (10,000 resamples, seed 42), a model that matches
`always references` everywhere except that it wins *k* of the 25 items the reference
gets wrong clears J with at most this many losses among the 24 it gets right
(`edge_screen_core.j_tolerance`: the first *k* wrong and the first *m* right items in
id order; a loss is an item to 0.0). The same definition on the first author's
labels reproduces this draft's earlier first-author table exactly (win 4 lose 0, win
8 lose 1, win 12 lose 4, win 28 lose 15), so the definition is the one the earlier table used.

| wins | lose at most (id order) | range over 10 seeded shuffles of which items |
| --- | --- | --- |
| 3 | none (cannot clear J) | -1 to -1 |
| 4 | 0 | 0 to 0 |
| 5 | 0 | 0 to 0 |
| 6 | 0 | 0 to 0 |
| 7 | 1 | 1 to 1 |
| 8 | 1 | 1 to 1 |
| 9 | 2 | 2 to 2 |
| 10 | 2 | 2 to 2 |
| 12 | 4 | 3 to 4 |
| 14 | 5 | 5 to 5 |
| 16 | 6 | 6 to 6 |
| 18 | 7 | 7 to 8 |
| 20 | 9 | 9 to 9 |
| 22 | 10 | 10 to 10 |
| 24 | 12 | 11 to 12 |
| 25 (every miss) | 12 | 12 to 13 |

Which items are won and lost moves the cell by at most one (the last column); the
table is a guide to the size of the bar, not a decision rule, which is the bootstrap on
the real per-item scores. `--self-test` pins win 4 clears and win 3 does not.
Stated before data so a J pass
is not over-read: **a pass shows a model agrees with these labels better than
answering `references` for every link does; it does not show the model types edges
correctly.** In particular 29 of the 49 items are constructed to the rubric and
state their relation in its words, so a model can clear J on those alone;
accuracy is reported per stratum, and selection also needs a non-negative gain on the
20 other items (the owner's decision 3).

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
  `qwen3.6:35b-a3b`, plus `gemma2:27b`, the documented recommendation for this key
  (#650), added by the owner's decision 2. Judge-only models are not candidates.
- Model digests: the table under "Forecast and budget" (recorded by Stage 0; the
  harness also stamps the full digest in-band on every run, and a run whose stamped
  digest differs from the table is void).

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
  0, no veto trips, it clears J, **and** its paired gain on the 20 non-constructed
  items (good-life and ADR) has a point estimate >= 0 (the owner's decision 3; the
  point estimate of a paired mean difference is the difference of the two models'
  accuracies on those items, which `score` reports per stratum; `score`'s verdict
  line does not apply this guard itself, so it is read from those numbers).
  Otherwise the baseline stays and the result
  is recorded as it is, with no retry under tuned bars (#1269 rule 4).
- **Confirmation:** a selected model is rerun with seeds 11-15 against the baseline
  under the same rule; "confirmed" means selection holds again. Adoption (a config
  default, `RECOMMENDED_TASK_MODELS`, or an ADR) is a separate decision this screen
  does not make; a J pass alone would not support one, and a selection would still
  owe the specific-precision and direction numbers a reading.

## Forecast and budget (Ollama wall-clock; MEASURED by Stage 0)

Calls: 49 items x 1 arm x 3 runs = **147 per model; 882 for six models.**

**Stage 0 (run 2026-10-27 with `run_edge_typing_screen.py stage0`; no scored item was
called).** Per model, in order: one warm-up call (this is the model load, and is in no
statistic below), three timed calls on a small calibration pair, and one timed call on a
large one, all seed 1, timeout 240 s, server defaults, one model at a time and the model
unloaded before the next. The calibration pairs are in no way fixture items: the small one is
two concept documents (`stage0-small`, 229 characters; the smallest scored pair is 237), the
large one is ADR-0025 and ADR-0044, which are not in the fixture (`stage0-large`, 10,380
characters; the largest scored pair is 12,337). `--self-test` pins that neither shares an id
with a fixture item. No call failed. n = 4 timed calls per model, so the p95 (nearest rank)
is the large call; read it as the cost of an ADR-sized pair, not as a tail estimate.

| model | digest (sha256, as `/api/tags` reports it) | warm-up (load) s | small median s (n=3) | ADR-size s (n=1) | median s (n=4) | p95 s (n=4) | forecast min |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `qwen3:8b` | `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41` | 6.7 | 0.98 | 8.90 | 0.98 | 8.90 | 5.7 |
| `gemma4:26b-a4b` | `001e5dafc3c77684c2307ebc6ab8e336e10c9b18eca52acf547d72fc83c3ca8c` | 12.8 | 1.07 | 6.35 | 1.08 | 6.35 | 4.9 |
| `qwen3:14b` | `bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8` | 12.2 | 1.82 | 14.24 | 1.82 | 14.24 | 9.6 |
| `qwen3.6:27b` | `bcbdbd4bcf86f2c4a56cd1698fb31f9434f7dd99752f71b636c750aaa67a49a5` | 35.3 | 13.38 | 46.33 | 13.39 | 46.33 | 46.5 |
| `qwen3.6:35b-a3b` | `a7eb95c53bcf96b4bdd008d0fab4a5dac88047d9c1a7a9ab88ed453423fbd87c` | 22.8 | 1.13 | 5.88 | 1.14 | 5.88 | 5.0 |
| `gemma2:27b` | `53261bc9c192c1cb5fcc898dd3aa15da093f5ab6f08e17e48cf838bb1c58abfe` | 23.4 | 2.30 | 29.45 | 2.31 | 29.45 | 16.9 |

**How the forecast is built.** Latency is not constant across the 49 items (237 to 12,337
characters), so the median of the four timed calls would understate the ADR items and the
large call alone would overstate the rest. Each item's latency is interpolated linearly in
its pair's character count between the small and the large measurement (never below the
small one, extrapolated past 10,380 characters), summed over the 49 items, times 3 runs, plus
the model's measured warm-up in place of the assumed 60 s load. Two points are a thin basis
for a line; this is an estimate, the scored runs will report their own medians, and the
forecast carries no claim beyond the budget check below.

| model | mean s per call (interpolated) | forecast |
| --- | --- | --- |
| `qwen3:8b` | 2.27 | 5.7 min |
| `gemma4:26b-a4b` | 1.93 | 4.9 min |
| `qwen3:14b` | 3.84 | 9.6 min |
| `qwen3.6:27b` | 18.74 | 46.5 min |
| `qwen3.6:35b-a3b` | 1.91 | 5.0 min |
| `gemma2:27b` | 6.72 | 16.9 min |
| **screen total** | | **1.48 h** |
| confirmation (5 seeds, baseline + `qwen3:14b`, the draft's assumption) | 2 x 245 calls | 0.42 h |
| confirmation (5 seeds, baseline + `qwen3.6:27b`, the slowest candidate) | 2 x 245 calls | 1.43 h |
| confirmation (3 seeds, baseline + `qwen3.6:27b`) | 2 x 147 calls | 0.86 h |

**Budget: 2.5 h of Ollama wall-clock** from the first scored call. The measured screen
total, **1.48 h, is within it**, so no candidate is dropped before the screen. With the
confirmation, the total is 1.89 h when the selected candidate is `qwen3:14b`, within
budget; if the selected candidate were `qwen3.6:27b`, five seeds would make it
2.91 h, over budget, so the registered rule applies and the confirmation uses 3
seeds (2.34 h, within budget). The slowest candidate on these numbers is
`qwen3.6:27b`, at 18.7 s per call against `qwen3:8b`'s 2.3.

**Per-call timeout.** Four times the baseline's median (0.98 s), floor 60 s,
as in #1557: **60 s**, passed to `run` as `--timeout 60` (the harness default is 120 s).
Read this before the screen runs: `qwen3.6:27b` took 46.3 s on the 10,380
character pair and the largest scored pair is 12,337, so that model may time out on the ADR
items; a timeout is a failure and scores 0, and V1 counts it. The rule is the registered one
and is not changed here.

**Observed, not a decision.** On the Stage 0 medians, V2 (median call latency <= 2.0x the
baseline's, 1.97 s) would pass `gemma4:26b-a4b` (1.08),
`qwen3:14b` (1.82) and `qwen3.6:35b-a3b` (1.14), and
would not pass `gemma2:27b` (2.31) or `qwen3.6:27b` (13.39). V2 is
applied on the scored runs' medians, not on these four calls; every candidate stays in the
screen.

One Ollama job at a time, models one at a time, server defaults, and this work the only user
of the server. The harness never contacts a model in `--self-test`, `forecast`, `blind-sheet`
or `adjudicate`; `stage0` and `run` do.

## Commands

```sh
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/edge_typing_screen/run_edge_typing_screen.py forecast --runs 3
uv run python -u evals/edge_typing_screen/run_edge_typing_screen.py stage0   # Stage 0, already run; calls only calibration inputs
uv run python -u evals/edge_typing_screen/run_edge_typing_screen.py run --model qwen3:8b --runs 3 --timeout 60
uv run python evals/edge_typing_screen/run_edge_typing_screen.py score
```

## Decisions the owner made (2026-10-27)

Each was an open question in this draft; the owner decided all three on 2026-10-27, as
recommended.

1. **`references` is the right label for an explicit link that states no more.**
   Decided by the owner, 2026-10-27. The rule defines `references` by SOURCE naming
   TARGET and reserves `related_to` for when none of the listed types holds; the
   adjudication applied that reading, so the reference baseline stays `always
   references` (0.490 on the resolved labels).
2. **`gemma2:27b` is a candidate.** Decided by the owner, 2026-10-27. It is the
   documented per-task recommendation (#650) and the only model with prior evidence on
   this task, so a screen without it could neither keep nor drop that recommendation.
   It is in the harness's candidate list, with an assumed latency borrowed from
   `qwen3:14b` until Stage 0 measured it (installed; its measured latency and digest are in
   the table under "Forecast and budget").
3. **Pooled decides, with a stratum guard.** Decided by the owner, 2026-10-27. The
   registered statistic stays pooled over all 49 items and every stratum is reported;
   in addition a selected candidate's paired gain over the baseline must have a point
   estimate >= 0 on the 20 items outside the constructed stratum (14 good-life, 6 ADR),
   so a pass cannot come only from the 29 items written to the rubric.

## Known limits (carried from the README)

One author's labels until the adjudication; the rubric is the production prompt's,
so the fixture measures agreement with that rule; `references` dominates; 27 items
ambiguous by the first author's flag; `caused_by` and `part_of` have 3 items each;
the good-life items are not independent; direction is probed on nine constructed
items; bodies range from 109 to 6,388 characters; no `Place`, `Entity`, `Insight`,
`Event` or `Procedure` endpoint; English only.
