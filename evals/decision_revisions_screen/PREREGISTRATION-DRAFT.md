# Pre-registration (DRAFT): the decision-revisions model screen (#1563)

**Status: DRAFT. NOT REGISTERED. The labels are adjudicated (see "Third
adjudication"); still blocked on the owner's questions, the stored-run re-score and
Stage 0.** Nothing below binds until a final version of this
file, with the numbers marked `TO RECOMPUTE` replaced, is merged to `main` before
any scored model call (#1556: pre-register, and merge the pre-registration, before
any run). No model has been called in preparing this draft; every number in it was
computed by `run_revisions_screen.py forecast` or `--self-test`, which contact none,
or by re-scoring the stored `qwen3:8b` rows of `evals/decision_revisions/results/`.
A measurement: it adopts nothing and changes no production code, default, prompt or
task key. Part of #1556; refs #1563, #1557.

## Before this can be registered

1. **Done, with two deviations (below).** The 58 pairs were labelled blind a second
   time with the sheet from `run_revisions_screen.py blind-sheet`, by a Claude model
   rather than the owner, and the 3 disagreements (all of which score differently)
   were resolved by a third model (Claude Opus) on the owner's delegation, recorded in
   [`adjudication.json`](adjudication.json) (`first`, `second`, `resolved`, `reason`) and
   listed in [`ADJUDICATION.md`](ADJUDICATION.md).
2. **Done for the floors and the class balance** (`forecast`, below). Still `TO
   RECOMPUTE`: the J tolerance statement (`--self-test` pins on the adjudicated labels
   that four wins clear J and three do not) and every number derived from the stored
   `qwen3:8b` rows (the 0.893 / 0.887 / 0.882 scores, the 7-item headroom and the
   paired-gain replay), which were not re-scored here.
3. **The questions in "Decisions the owner owes" are answered**, because each changes
   what the screen means.
4. **Stage 0** (below) replaces the assumed latencies. It was not run for this
   draft because the server is reserved for another job. The digests below are as
   `ollama list` reported them, a read-only call.

## Second labelling: recorded, with a deviation (read first)

**The owner did not label this sheet.** The owner delegated the blind labelling, and a
Claude model labelled the sheet blind (it read only the blind sheet, none of
`fixture.json`, the README, this draft or any other label). Agreement below is therefore
measured against a **Claude labeller, not the owner**, so it is a statement about two
readings of the same rubric, not about the owner's judgment. The owner's own review of
every disagreement, and every resolution, is still owed. The labels were imported with
`run_revisions_screen.py import-sheet` into [`second_labels.json`](second_labels.json) (labels keyed by
fixture id, plus the labeller and seed). The filled sheet itself is not committed, per
the README.

**Result** (`adjudicate`): 55 of 58 agree (0.948), Cohen's kappa 0.927, 3 disagreements.
All three score differently, all are pairs the drafter flagged contested, and all are among
the five never-right stored-run items: `r14` (first `reverses`, Claude `unrelated`), `r43`
(first `refines`, Claude `reaffirms`) and `r53` (first `unrelated`, Claude `reaffirms`).

**What this did not do.** It resolved nothing; the third adjudication below did.

## Third adjudication: recorded, with a deviation (read first)

**Deviation: the disagreements were adjudicated by a third model (Claude Opus) on the
owner's delegation, blind to annotator identity**, not by the owner. For each pair the
adjudicator read only the two Decisions as the sheet shows them (titles, bodies, dates and
order), the production judge prompt verbatim and the two candidate verdicts in sorted
order, without knowing which annotator gave which and without reading the drafter's notes,
any stored model run, result or score; its verdicts were written down before the sources
were revealed. `r14` resolved to the drafter's `reverses` (the later Decision "moves" the
Wednesday-morning session); `r43` and `r53` resolved to the second label, `reaffirms`
(`r43` says no change was proposed; `r53` restates the same date and place). Records are in
`adjudication.json`. No registered margin or decision rule changed. The owner may override
any resolution at registration.

## What the labels are (read before any number)

**One author's, with one review of the doubtful quarter.** The drafter labelled all
58 pairs and flagged 16 as contested; the owner accepted all 16 as proposed on
2026-09-27. The other 42 pairs were never labelled twice. The volatility screen's
second adjudication found its first author had anchored on the default in all eight
disagreements and moved that floor from 0.778 to 0.481; the hazard here is the
drafter's own reading of the prompt's four definitions, and 4 of the 5 items the
stored `qwen3:8b` run never gets right are contested ones.

**The floors (the adjudicated labels, strict; `forecast`).** The drafter's values are in
brackets. Class balance: REVERSES 14, REFINES 13, REAFFIRMS 8, UNRELATED 23 (the drafter's:
14, 14, 6, 24).

| floor | value |
| --- | --- |
| `lexical overlap (oracle cut)` | **34 / 58 = 0.586** (the reference for rule J; fitted to the labels, so optimistic) [35 / 58 = 0.603] |
| `always unrelated` | 23 / 58 = 0.397 (also the commonest label) [24 / 58 = 0.414] |
| `always refines` | 16 / 58 = 0.276 [17 / 58 = 0.293] |
| `always reverses` | 16 / 58 = 0.276 |
| `always reaffirms` | 8 / 58 = 0.138 [6 / 58 = 0.103] |
| items wrong for the reference | 24 / 58 [23 / 58] |
| items wrong for both `always unrelated` and the lexical cut | 21 / 58 [19 / 58] |
| stored `qwen3:8b`, current judge prompt, three 15-run runs | 0.893, 0.887, 0.882 (not a floor: a measurement of the baseline model on the drafter's labels; `TO RECOMPUTE` on the adjudicated ones) |

**What the J bar is, and why it will not decide.** The reference is wrong on 24
items (23 on the drafter's labels; the stored-run figures in this paragraph are the
drafter's and `TO RECOMPUTE`). With the harness's own bootstrap (10,000 resamples, seed 42, the edge-typing
screen's), a model that matches the reference everywhere it is right and wins *k* of
the 23 clears J with no loss from *k* = 4; the `--self-test` pins win 4 clears and
win 3 does not. **The stored `qwen3:8b` run already scores about 0.88, so every
competent model clears J.** A pass shows a model agrees with these labels better
than the best word-overlap cut does; it does not show the model judges revisions
correctly, and it does not discriminate between models. The deciding comparison is
selection against `qwen3:8b`, where the headroom is about 7 items:

- Replaying the stored per-item scores of the latest `qwen3:8b` run (50 of 58 items
  right in every run; five never right, two right one run in five, one at 0.73), a
  candidate that fixes 3 of the 7 weakest items with no loss has a paired-gain lower
  bound of 0.0 and one that fixes 5 has +0.017. Selection therefore needs a
  candidate that is right on at least 5 of those 7 and loses nothing, or a different
  set of items after the adjudication. The most probable outcome, stated before the
  data, is that nothing is selected.
- The 12 `confirmation` pairs are at ceiling for the baseline (0.99 to 1.00) and the
  46 `original` pairs are the ones the judge prompt was tuned on, for `qwen3:8b`.
  The baseline's score is optimistic for generalisation and a candidate that wins is
  stronger evidence than one that loses.

**Label uncertainty.** The report gives strict accuracy for every model plus two
sensitivity analyses: `strict (1st)`, the same answers scored against the drafter's
labels, and `strict (clean)`, restricted to the 42 pairs the drafter did not flag.
**The decision uses strict accuracy on the resolved labels.** The sensitivities are
reported and never decide. A model whose verdict differs between resolved-strict and
either sensitivity is recorded as label-dependent, not selected on the better one.

## Question

Is the decision-revision judge's task key **justified**: does any local model judge
how a later Decision relates to an earlier one's choice better than a predictor that
needs no model, on a fixture whose labels follow the production prompt's own
definitions and have been checked by a second person? Secondarily: does the key need
a different model than the global `qwen3:8b`, and does a better verdict come with
more verdicts that would hide a Decision still in force?

## Items, arms, runs

- **Items:** the 58 pairs of `evals/decision_revisions/revision_fixture_library.py`
  as committed at the registration commit, with `adjudication.json` applied (46
  `original`, 12 `confirmation`; 49 directed, 9 undirected; class balance in the
  README).
- **Arms:** one, `default`: the shipped judge prompt, handed each pair directly
  (the candidate stage is not measured).
- **Runs:** n = 3 seeds (1, 2, 3) per (model, item). Seeds are the `OllamaClient`
  seed; sampling is otherwise the model's Modelfile default. n = 3 is the minimum that
  shows stability, not a power claim (the fixture limits power, not the run count).
- **Models:** baseline `qwen3:8b` (the global default). Candidates, from the other
  #1556 screens: `gemma4:26b-a4b`, `qwen3:14b`, `qwen3.6:27b`, `qwen3.6:35b-a3b`, and
  `gemma2:27b` (installed; it is `RECOMMENDED_TASK_MODELS` for neighbouring keys).
  Nothing is pulled.

Model digests, as `ollama list` reported them on the registering machine (the short
ID; the harness stamps the full digest in-band on every run and a run whose stamped
digest differs from this table is void):

| tag | digest (short ID) |
| --- | --- |
| `qwen3:8b` | `500a1f067a9f` |
| `gemma4:26b-a4b` | `001e5dafc3c7` |
| `qwen3:14b` | `bdbd181c33f2` |
| `qwen3.6:27b` | `bcbdbd4bcf86` |
| `qwen3.6:35b-a3b` | `a7eb95c53bcf` |
| `gemma2:27b` | `53261bc9c192` |

## Metrics

Computed by `revisions_screen_core.summarize` / `run_revisions_screen.py score`:

1. **Strict accuracy** against the resolved labels (primary, deciding): the mean over
   items of each item's mean over runs of "the verdict is the label", where on an
   undirected pair `reverses` and `refines` are one untyped change (production never
   types it). A failed call, including a malformed reply, scores 0 and is never
   skipped, and is never credited as `unrelated`.
2. `strict (1st)` and `strict (clean)` (sensitivity, not deciding); accuracy per
   split.
3. Per-class recall and the confusion matrix; failure rate by kind; median and p95
   latency; stability across runs.
4. **False-supersede rate:** of the calls on directed pairs that are not a reversal,
   the share answered `reverses`.
5. **Queue recall and queue noise** under production's actionability rule
   (confidence >= 0.7 and both quotes verbatim): reported, never deciding.
6. The floors above.

## Decision rule

The deciding statistic is a paired difference of per-item mean strict scores against
the resolved labels, with a 95% percentile bootstrap over items (10,000 resamples,
seed 42).

- **J (justified):** a model clears J iff its paired gain over the reference (the
  strongest model-free predictor on the resolved labels, recomputed at registration;
  the lexical cut on the drafter's) has a 95% lower bound above 0. If no model
  clears J the verdict is "the key is not justified on this fixture". Because the
  stored baseline already clears it, J is computed for `qwen3:8b` too and reported
  first.
- **Vetoes** (any one drops a candidate from the key): V1 failure rate <= baseline
  + 0.02 and <= 0.05 absolute; V2 median call latency <= 2.0x the baseline's; V3
  false-supersede rate <= baseline + 0.10.
- **Selection:** a candidate replaces `qwen3:8b` for this key only if its paired
  gain over the baseline has a point estimate above 0 and a 95% lower bound above 0,
  no veto trips, **and** it clears J. Otherwise the baseline stays and the result is
  recorded as it is, with no retry under tuned bars (#1269 rule 4). Per-split
  accuracy is reported with the verdict; whether a selected model must also not lose
  on the `original` split is question 3 below.
- **Confirmation:** a selected model is rerun with seeds 11-15 against the baseline
  under the same rule; "confirmed" means selection holds again. Adoption (a config
  default or an ADR) is a separate decision this screen does not make.

## Forecast and budget (Ollama wall-clock; assumed, not measured)

Calls: 58 items x 3 runs = **174 per model; 1,044 for six models.**

No judge latency was stored by `evals/decision_revisions/` (its rows carry none).
The latencies are **assumptions**: twice the volatility screen's measured per-call
median for each of the five models it ran (1.16, 1.51, 2.12, 10.44, 1.86 s over 162
calls each), because a judge reply carries a rationale and two verbatim quotes where
a volatility reply is one short object; `gemma2:27b` borrows 7.3 s from
`evals/edge_typing/README.md` ("about 9 minutes" for 74 edges). One load per model,
60 s.

| model | calls x assumed s + load | forecast |
| --- | --- | --- |
| `qwen3:8b` | 174 x 2.32 + 60 | 7.7 min |
| `gemma4:26b-a4b` | 174 x 3.02 + 60 | 9.8 min |
| `qwen3:14b` | 174 x 4.24 + 60 | 13.3 min |
| `qwen3.6:27b` | 174 x 20.88 + 60 | 61.6 min |
| `qwen3.6:35b-a3b` | 174 x 3.72 + 60 | 11.8 min |
| `gemma2:27b` | 174 x 7.3 + 60 | 22.2 min |
| **screen total** | | **about 2.1 h** |
| confirmation (5 seeds, baseline + one candidate, assuming `qwen3:14b`) | 2 x 290 calls | about 0.6 h |

**Budget: 3.0 h of Ollama wall-clock** from the first scored call; the worst case
above is about 2.7 h. `qwen3.6:27b` is about half of the screen and its latency is the
least certain input. **Stage 0 before the screen:** three timed calls per model,
which replace the assumed latencies in this table and set the per-call timeout (4x
the baseline's median, floor 60 s, as in #1557). If the measured total would exceed
the budget the confirmation drops to 3 seeds; if the screen itself would exceed it,
the slowest candidate is dropped before any scored call, not after. One Ollama job at
a time, models one at a time, server defaults, and this work the only user of the
server. The harness never contacts a model in `--self-test`, `forecast`, `blind-sheet`
or `adjudicate`.

## Commands

```sh
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/decision_revisions_screen/run_revisions_screen.py forecast --runs 3
uv run python -u evals/decision_revisions_screen/run_revisions_screen.py run --model qwen3:8b --runs 3
uv run python evals/decision_revisions_screen/run_revisions_screen.py score
```

## Decisions the owner owes (each with the recommended answer, applied unless overridden at registration)

1. **Is the screen worth running before the adjudication?** Recommended: no. The
   stored run already answers "does the key beat no model" if the labels hold, and
   the labels are the weakest link; run the blind sheet first, then decide whether
   the 7-item headroom justifies about 2 hours of the server.
2. **Score an undirected `reverses`/`refines` as one untyped change?** Recommended:
   yes. It is what production does with the verdict (`relation_for`); scoring the
   distinction would reward a choice nothing consumes.
3. **Must a selected model also not lose to the baseline on the `original` split?**
   Recommended: yes (point estimate of the paired gain on `original` >= 0). The
   `confirmation` pairs are at ceiling for the baseline and cannot carry a veto.
4. **Reference for J: the lexical oracle cut or `always unrelated`?** Recommended: the
   lexical cut. It is the stronger predictor, so a pass means more, and it costs
   nothing since J is expected to pass anyway. It is fitted to the labels, so it is
   recomputed after the adjudication.
5. **Verdict accuracy or queue outcome as the deciding metric?** Recommended: verdict
   accuracy decides; queue recall and noise are reported. The queue rule (confidence
   0.7, quotes) is a separate production knob this screen does not tune.
6. **What does a pass license?** Recommended: one confirmation run on
   ingest-produced Decisions, not a task-key change, as for reconcile.

## Known limits (carried from the README)

One author's labels with one owner review of 16 pairs; the prompt was tuned on this
fixture for `qwen3:8b`; about 7 items of headroom; one domain, English only; short
bodies; the candidate stage and a user's real Decision text are not measured; an
out-of-vocabulary verdict string is read as `unrelated` by production and cannot be
told from a real one; the latencies are assumptions.
