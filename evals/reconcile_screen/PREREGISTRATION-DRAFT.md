# Pre-registration (DRAFT): the merged-body reconciliation model screen (#1563)

**Status: DRAFT. NOT REGISTERED. Blocked on the repository owner's blind labelling of
the claims.** Nothing below binds until a final version of this file, with the numbers
marked `TO RECOMPUTE` replaced, is merged to `main` before any scored model call
(#1556: pre-register, and merge the pre-registration, before any run). No model has been
called in preparing this draft; every number in it was computed by
`run_reconcile_screen.py forecast` or `--self-test`, which contact none. A measurement: it
adopts nothing and changes no production code, default, prompt or task key. Part of #1556;
refs #1563, #1557.

## Before this can be registered

1. **The owner labels the 121 claims blind** with the sheet from `run_reconcile_screen.py
   blind-sheet`, and `adjudicate` compares it with the first author's labels. Every
   disagreement is resolved by the owner and recorded in `fixture.json` as the volatility
   fixture records it (`adjudication` with `first`, `second`, `resolved`, `reason`; the first
   label stays as `alt_label`). A claim that moves between one-sided and shared needs a
   `once` literal that occurs exactly once in each note, or is retired with a reason.
   `ADJUDICATION.md` is then written, as the volatility screen's was.
2. **The floors, the J bar and the class balance are recomputed** from the resolved labels
   (`forecast`; the values below are the first author's).
3. **The questions in "Decisions the owner owes" are answered**, because each changes what
   the screen means.
4. **Stage 0** (below) replaces the assumed latencies and records the model digests
   (`ollama list`). Neither was queried for this draft, because the server is reserved for
   another job.

## What the labels are, until the blind labelling is recorded (read before any number)

**One author's.** The first author chose the claims and labelled which note states each,
flagging 2 as arguable. The volatility screen's second adjudication found its first author
had anchored on the registry default and moved that floor from 0.778 to 0.481. The
equivalent hazard here is **over-calling `both`**: a claim the first author reads as stated
in both notes is one the model must state exactly once, and a reader who sees only one note
stating it turns it into a one-sided claim and removes the dedupe test from it. Of the 33
`both` claims, 2 are flagged; the rest are shared facts with a literal both notes carry.

**The floors (the first author's labels, strict, no model).**

| baseline | claims kept | mean case score |
| --- | --- | --- |
| `sentence_union` (the reference for J) | 93 / 121 | **0.770** |
| `stack` (what production keeps on a refusal) | 88 / 121 | 0.729 |
| `longer_only` | 81 / 121 | 0.672 |
| `absorbed_only` | 79 / 121 | 0.654 |
| `survivor_only` | 75 / 121 | 0.617 |

Class balance: 42 only in NOTE A, 46 only in NOTE B, 33 in both. The strongest baseline
gets 28 claims wrong; 14 of 15 cases are merged perfectly by no baseline; no claim is wrong
under every baseline (0), because each kind is kept by one trivial predictor.

**What the J bar is.** The reference is wrong in 28 claims and scores below 1.0 in 14
cases. With the harness's own bootstrap over cases (10,000 resamples, seed 42):

- a model that matches the reference on every case and is better by a constant **+0.05**
  on every case clears J (95% lower bound 0.04); +0.10 gives 0.08;
- a model that matches the reference everywhere except winning *k* cases outright (to 1.0)
  clears J from *k* = 4 (win 3 does not; `test_the_j_bar_on_the_real_reference_is_pinned` pins
  both). It tolerates **no** outright loss (a case to 0.0) at any *k* from 4 to 14, because one
  such loss is a delta of -1.0 against gains of at most 0.44 a case. Partial losses are
  different: a model reads on its gains and losses per case, not on its mean alone.

Stated before data so a J pass is not over-read: **a pass shows a model keeps more of what
the notes say, and says shared facts once, better than a sentence-level union with no model
does. It does not show the merged document reads well**, which this screen does not score.

**Label uncertainty.** The report gives strict and lenient accuracy for every model, and
`strict (1st)`, the same answers scored against the first author's labels, as a sensitivity
analysis. **The decision uses strict accuracy on the resolved labels.** Lenient and the
first-author sensitivity are reported and never decide. A model whose verdict differs
between the two is recorded as label-dependent, not selected on the better one.

## Question

Is a model call for **merged-body reconciliation justified**: does any local model produce a
merged body that keeps more of the two notes' claims, states shared ones once and invents
nothing, than a no-model sentence union does, on a fixture whose labels have been checked by
a second person? Secondarily: does the task need a different model than the global
`qwen3:8b`, and does a better merge come with more invented figures?

## Items, arms, runs

- **Items:** the 15 cases in `fixture.json` as committed at the registration commit (12 from
  repository documents, 3 from the `good-life-demo` bundle; 121 claims).
- **Arm:** `default`, the shipped prompt, one chat call per case and run through production's
  `reconcile_merged_body`. There is no second arm.
- **Runs:** n = 5 seeds (1 to 5) per (model, case), the `OllamaClient` seed. Five shows
  stability, not power (the fixture, not the run count, limits power; `evals/edge_typing/`
  takes n = 15 as its floor after a ranking reversed at n = 3).
- **Models:** baseline `qwen3:8b` (the global default, which is what `merge` uses today).
  Candidates, the other #1556 screens' list: `gemma4:26b-a4b`, `qwen3:14b`, `qwen3.6:27b`,
  `qwen3.6:35b-a3b`, plus `gemma2:27b` (installed). Judge-only roles are not a separate
  candidate class.
- **Scoring of failures:** a backend error, a timeout or a refused reply scores as the
  stacked body, the outcome production gives the user in that case.
- Model digests: `TO RECORD` at registration (the harness stamps the full digest in-band on
  every run, and a run whose stamped digest differs from the table is void).

## Metrics

Computed by `reconcile_core.summarize` / `run_reconcile_screen.py score`:

1. **Strict accuracy** (primary, deciding): the mean over cases of each case's mean over runs
   of the share of its claims kept (anchors survive; a shared claim exactly once).
2. **Lenient accuracy** (reported): also accepts a flagged claim in any number.
3. **First-author sensitivity** (reported): the same bodies against the first author's kinds.
4. **Retention** (claims whose anchors survive, over bodies the model produced), by kind;
   **dedupe rate** (shared claims kept that are stated once); **fabrication rate** (bodies
   with at least one digit-bearing word or code span in neither note nor the title); failure
   rate by kind (`backend`, `timeout`, `refused`); median and p95 latency; stability (cases
   whose score is identical across runs).
5. The floors above, recomputed from the resolved labels.

## Decision rule

The deciding statistic is a paired difference of per-case mean strict scores, against the
resolved labels, with a 95% percentile bootstrap over cases (10,000 resamples, seed 42).

- **J (justified):** a model clears J iff its paired gain over the **reference baseline** has a
  95% lower bound above 0. The reference is the model-free predictor with the highest strict
  score on the resolved labels, fixed from the labels alone before any run (`sentence_union`
  on the first author's). If no model clears J the verdict is "a model call is not justified
  on this fixture", and the product stays as it is: `merge` keeps asking the global model and
  keeps the stack on a refusal.
- **Vetoes** (any one drops a candidate): V1 failure rate (backend, timeout or refused) <=
  baseline + 0.02 and <= 0.10 absolute; V2 median call latency <= 2.0x the baseline's; V3
  fabrication rate <= baseline + 0.10.
- **Selection:** a candidate replaces `qwen3:8b` for this task only if its paired gain over the
  baseline has a point estimate above 0 and a 95% lower bound above 0, no veto trips, **and** it
  clears J. Otherwise the baseline stays and the result is recorded as it is, with no retry
  under tuned bars (#1269 rule 4).
- **Confirmation:** a selected model is rerun with seeds 11 to 15 against the baseline under
  the same rule; "confirmed" means selection holds again.
- **What a pass licenses.** Not a config default and not a `reconcile` task key (the key set is
  closed by #508's rule that a default must be justified on a fixture). A pass licenses one
  confirmation run on notes that ingest actually produced, because this fixture's notes are
  repository documents. Adoption is a separate decision this screen does not make.

## Forecast and budget (Ollama wall-clock; ASSUMED, not measured)

Calls: 15 cases x 1 arm x 5 runs = **75 per model; 450 for six models.**

Latencies are **assumptions**: twice #1560's answer-call medians (`qwen3:8b` 4.75 s,
`gemma4:26b-a4b` 2.83 s, `qwen3:14b` 6.57 s, `qwen3.6:35b-a3b` 3.22 s), because a
reconciliation reply is a whole rewritten document of a few hundred words, not a short
answer. `qwen3.6:27b` and `gemma2:27b` borrow `qwen3:14b`'s. One load per model, 60 s.

| model | calls x assumed s + load | forecast |
| --- | --- | --- |
| `qwen3:8b` | 75 x 9.50 + 60 | 12.9 min |
| `gemma4:26b-a4b` | 75 x 5.66 + 60 | 8.1 min |
| `qwen3:14b` | 75 x 13.14 + 60 | 17.4 min |
| `qwen3.6:27b` | 75 x 13.14 + 60 | 17.4 min |
| `qwen3.6:35b-a3b` | 75 x 6.44 + 60 | 9.1 min |
| `gemma2:27b` | 75 x 13.14 + 60 | 17.4 min |
| **screen total** | | **about 1.37 h** |
| confirmation (5 seeds, baseline + one candidate, assuming `qwen3:14b`) | 2 x 75 calls | about 0.5 h |

**Budget: 2.5 h of Ollama wall-clock** from the first scored call. **Stage 0 before the
screen:** three timed calls per model on a small case and one on the largest, which replace the
assumed latencies in this table and set the per-call timeout (4x the baseline's median, floor
60 s, as in #1557; the harness default is 240 s). If the measured total would exceed the budget
the confirmation drops to 3 seeds; if the screen itself would exceed it, the slowest candidate
is dropped before any scored call, not after. One Ollama job at a time, models one at a time,
server defaults, and this work the only user of the server. The harness never contacts a model
in `--self-test`, `forecast`, `blind-sheet` or `adjudicate`.

## Commands

```sh
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/reconcile_screen/run_reconcile_screen.py forecast --runs 5
uv run python -u evals/reconcile_screen/run_reconcile_screen.py run --model qwen3:8b --runs 5
uv run python evals/reconcile_screen/run_reconcile_screen.py score
```

## Decisions the owner owes (this draft does not make them)

1. **Is a literal anchor the right test of "kept"?** A model that keeps a claim in other words
   is scored as having lost it. Recommended: yes. The production prompt says "keep concrete
   details; do not summarize away substance", the anchors are the concrete part (identifiers,
   figures, quoted terms), and a rewrite that changes them is a change a reader of the merged
   document would have to re-check anyway. The cost is a bias toward models that copy.
2. **Is "exactly once" the right standard for a shared claim?** Recommended: yes, it is the
   prompt's own wording. If the owner thinks a restatement is harmless, lenient accuracy is the
   number to read and the dedupe rate stops mattering; this draft keeps strict as the decision.
3. **Should the decision be pooled or stratified?** The registered statistic is pooled over all
   15 cases. Twelve are repository documents and three are the demo bundle; three cases cannot
   carry a veto. Recommended: pooled decides, the two strata are reported, no stratum rule.
4. **Is `sentence_union` the right reference, rather than the stack?** The stack is what ships
   on a refusal, but a no-model union is what one would build instead of calling a model.
   Recommended: the strongest baseline, which is the harder bar and the honest one (0.770
   against 0.729).
5. **Should `gemma2:27b` be a candidate?** It is installed and the only model recommended for
   another task on this list. Recommended: yes, as listed; it has an 8k context window, which
   these notes (at most about 1,400 characters each) fit well inside.

## Known limits (carried from the README)

One author's labels until the blind labelling; anchors are literals, so a paraphrase is scored
as a loss; prose quality and coherence are not scored; the notes are repository documents, not
ingest-produced concept bodies, and are shorter than a real merge; a shared claim counts one
literal; 2 of 121 claims are flagged arguable; fabrication sees only digit-bearing words and
code spans; five seeds; English only.
