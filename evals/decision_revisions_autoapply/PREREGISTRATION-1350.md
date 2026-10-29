# Pre-registration: unattended application of directed revision findings (#1350)

**Status: REGISTERED on merge, before any verdict is written.** Nothing here builds the mode. It fixes the question, the arms, the bars and the stopping rules, so that [#1350](https://github.com/jasonssdev/openkos/issues/1350) is decided by a measurement and not by intuition, under the method of [#1054](https://github.com/jasonssdev/openkos/issues/1054) and [ADR-0034](../../docs/adr/0034-identity-auto-merge-only-for-a-measured-class.md). No model was called in preparing it. Values marked **measured** come from a committed file; values marked **estimate** are not measurements. From the merge on the rule is frozen: a different rule is a separate pre-registration that re-runs every arm.

The model-free half is already encoded and self-tested in [`run_autoapply_gate.py`](run_autoapply_gate.py) (`--self-test`, discovered by `evals/run_self_tests.py`; one targeted mutation killed three checks).

## Question

If directed `revisions` findings were written **without a person**, how often would the write be the wrong edge?

The mode under test applies a finding only when all of these hold: the pair is **directed** (both dates resolved and distinct, ADR-0025); the verdict is `reverses` or `refines`; production calls it actionable (`is_actionable_revision`: confidence >= 0.7 and both quotes verified); and its confidence reaches a threshold `t*` fixed by the measurement. A `reverses` writes `<later> supersedes <earlier>`, which hides the earlier Decision from `query`. A `refines` writes `<later> revises <earlier>`. `reaffirms`, `unrelated` and undirected findings are never applied, so they enter no number below.

An applied call is the **wrong edge** when the verdict is not the pair's label: a `supersedes` over a pair that is not a reversal, or a `revises` over a pair that is not a refinement.

## What is already known

- **#1054 (`qwen3:8b`): FAIL.** The identity judge stated 0.95 on right and wrong verdicts alike, so no threshold separated them (**measured**, `evals/auto_merge/results/`). The issue states the same kill clause for this mode: if confidence turns out constant, the measurement fails and the mode is not built.
- **The revision judge is the same shape.** In the three stored 15-run `qwen3:8b` runs under the current judge prompt (`d8238af6410ac31d`; `evals/decision_revisions/results/runs-20260928T173355Z`, `...204537Z`, `...20260929T011322Z`), every actionable `reverses`/`refines` call states a confidence of 0.9, 0.95 or 1.0 (**measured**). Production's own floor is 0.7, so it filters nothing.
- **The precision is not near a floor for unattended writes.** On directed pairs, the actionable calls are the wrong edge in 25 of 322 (precision 0.922), 25 of 323 and 31 of 327 (**measured**, `run_autoapply_gate.py decide`, adjudicated labels of `evals/decision_revisions_screen/adjudication.json`). The wrong calls sit at confidence 0.9, 0.95 and 1.0, **including at the maximum, 1.0** (8 of the 25 in the calibration run, 21 of 56 in the confirmation runs).
- **The misses are systematic, not noise.** Three pairs (`r04`, `r13`, `r27`) carry the calibration run's wrong calls, and `r27` is wrong in 14 or 15 of 15 trials in every current-prompt run (`r13` in 7, 8 and 12 of 15). Repeating a call, or reading its confidence, does not cure a pair the judge reads wrongly every time.
- **Disclosure.** These numbers were seen while drafting this file. They are descriptive. The rule below is the #1054 / #1298 rule, inherited unchanged, not fitted to them, and the precision floor was chosen from the cost of a wrong write (see "Bars"), not from this data. The expected outcome of Stage 0 is stated here before the verdict file exists: **FAIL, no separator.**

## Arms and stages

The question is answered in two stages. Stage 1 runs only if Stage 0 passes, because Stage 1 spends GPU hours and Stage 0 is free.

### Stage 0: model-free, on stored runs (this decides the confidence question now)

| arm | data | role |
|---|---|---|
| calibration | `runs-20260928T173355Z-qwen3-8b.json` (870 trials) | chooses `t*` |
| confirmation | `runs-20260928T204537Z-qwen3-8b.json` + `runs-20260929T011322Z-qwen3-8b.json` (1,740 trials) | judges the bars with `t*` fixed |

The population is the 49 **directed** pairs of the 58-pair library fixture (hand-written, adjudicated labels, `evals/decision_revisions_screen/`). It is not AMI. Stage 0 therefore decides one narrow, necessary condition: *does the judge's stated confidence separate right directed edges from wrong ones, for this model and prompt?* A FAIL here does not prove the mode would fail on AMI; it removes the only threshold the mode has, and the issue's own clause says the mode is then not built. A PASS here licenses nothing but Stage 1.

Weakness, disclosed: calibration and confirmation are different runs of the **same pairs**, not different pairs. The "chosen on one arm, judged on another" separation of ADR-0034 holds across runs only. This makes Stage 0 easier to pass than Stage 1, so it can only be a gate, never a verdict.

### Stage 1: real multi-meeting decisions, AMI development series only (conditional)

Runs only for a (model, judge prompt) that passed Stage 0. Today none exists: `qwen3:8b` is the shipped judge and the only model with stored runs on this prompt.

- **Series.** `scoring.DEV_SERIES` (ES2015, IS1004, TS3009). No evaluation or held-out series is read at any step (thesis ADR-0005). Calibration arm: ES2015 and IS1004. Confirmation arm: TS3009. `t*` is chosen on two series and judged on a third.
- **Decisions.** The adjudicated hand-annotated decisions of each series (`evals/transcript_decisions/`), compiled as dated Decision objects, so extraction error is not in the number. This gate measures the *application* of a finding, not the extraction of a decision.
- **Pairs.** Whatever production's candidate stage (`plan_revision_candidates`) nominates over those Decisions, so candidate generation is in the measurement. Undirected pairs (same-day meetings until #1349 orders them) are counted and reported but never applied, so they decide nothing.
- **Labels.** Two blind labellers per pair with the production judge prompt's four definitions, the dates and the pair as the judge sees it, and nothing else (the `blind-sheet` protocol of `evals/decision_revisions_screen/`). **Disagreements are resolved by the owner, not by a model.** Labels are frozen before the first treatment run.
- **Runs.** 15 per arm, production client settings, no pinned seed, one resident model.
- **Power precondition.** The labelled directed set must hold at least 8 true REVERSES and 8 true REFINES pairs and at least 12 directed negatives per edge (so that R1 and R3 are reachable at 15 runs). If a series set cannot meet this, Stage 1 is **INSUFFICIENT**, which is a FAIL for shipping and is recorded as such. It is never read as a pass, and the bars are never lowered to fit the data.

## Decision rule (frozen on merge)

`run_autoapply_gate.decide` runs on the arms above, with no edits.

- **R0 validity:** the arms use the same model and judge prompt, share no run file (Stage 1: no series), and each pair is run equally often. Otherwise **INVALID**: re-run, not a result.
- **S1 separator (the #1054 rule):** `B` is the highest confidence among wrong-edge calls in calibration. `t*` is the lowest right-edge confidence strictly above `B`. No such confidence: **FAIL (no separator)**, and the mode is not built. No wrong call: `t*` is the lowest right confidence and is labelled non-binding.
- **R1 exposure:** per edge, at least **180** confirmation trials on directed pairs the edge would be wrong for (zero wrong then bounds the error under 1.7%, rule of three).
- **R2 false supersedes:** **0** wrong `reverses` applied at `confidence >= t*` in confirmation. A wrong `supersedes` hides a Decision that is still in force, the costliest error this mode can make.
- **R3 `revises` precision:** at least **60** applied `refines` calls, and the one-sided 95% Wilson lower bound on their precision is **>= 0.95**. Both verdicts must pass; precision on REVERSES alone is not enough.
- **R4 retention:** at least **0.50** of confirmation trials on directed true REVERSES/REFINES pairs apply the right edge at `t*`. Without it the mode is safe and useless.
- **R5 persistence:** no pair has a wrong edge applied in more than **1** confirmation trial. A single slip is noise; a pair read wrongly repeatedly is systematic, and an unattended run would write it every time.

**Verdict.** PASS if and only if S1 yields `t*` and R0 to R5 all hold. INVALID if R0 fails. Otherwise FAIL, naming every failed bar. Stage 1 is decided by the same function on AMI trials.

## Why these numbers

- **A zero tolerance on `supersedes` (R2) and a 0.95 lower bound on `revises` (R3)** follow the cost of the write, not the model's accuracy. A person reviewing one finding at a time catches a wrong edge before it lands. Unattended, a wrong `supersedes` removes a live Decision from `query` until someone notices, and "Human curates, engine maintains" is only kept by the commit, the log and `unrelate` after the fact. The ADR-0034 / #1298 precedent for the same trade is zero false actions on a measured negative class. `revises` does not hide the earlier Decision, so it gets the statistical bound instead of zero: 0.95 is the lowest lower bound under which a 30-decision series expects fewer than two wrong edges, and at 60 right calls out of 60 the bound is 0.957, so the floor is reachable only by a near-perfect judge.
- **R4 0.50** is the #1298 retention bar, unchanged.
- **R5** exists because repeated trials of one pair are not independent: a Wilson bound on trials alone overstates n when the same pair fails every time.

## Cost forecast

- **Stage 0:** 0 model calls and no GPU; `run_autoapply_gate.py decide` reads three committed files and finishes in seconds (**measured** by running it).
- **Stage 1 (if reached):** judge calls only, plus a one-off embedding pass for the candidate stage (`bge-m3`, small). With the screen's assumed 2.32 s median per `qwen3:8b` judge call (**estimate**, `ASSUMED_MEDIAN_LATENCY_S` in `run_revisions_screen.py`), for an assumed 150 nominated pairs across the three series: 150 x 15 x 2.32 s is about 87 minutes, plus a 60 s model load, one resident model. A Stage 0 timing of three calls replaces the assumed latency before Stage 1 is scheduled. The pair count is unknown until the candidate stage runs on the gold decisions; the forecast is rerun then and the run is not started if it exceeds 3 hours.

## Stopping rules

- Stage 0 FAIL: stop. Write the verdict file, comment the outcome on #1350, do not run Stage 1, do not build the mode.
- Stage 0 PASS: Stage 1 may start only after the labelled set meets the power precondition and the forecast is within budget.
- A protocol change after the first Stage 1 treatment run invalidates that run. A pilot (one run per arm, read by no bar) may precede the counted runs.
- No second attempt with a changed threshold, prompt or model reuses these arms as if fresh: the result carries the model digest and prompt hash, and any change to the judge prompt, its quote check, its confidence floor or the default model invalidates a PASS and requires re-running the harness (ADR-0034).
- A different confidence signal (for example agreement over repeated calls) is a different mode with a different cost. It qualifies only through its own pre-registration.

## What a PASS licenses

An OpenSpec change and an ADR, and nothing else. The ADR follows ADR-0034's form: directed revision findings apply without per-item consent only for an opt-in class that passed this measurement. The change must keep: off by default and opt-in per run; `t*`, the model tag and digest and the judge prompt hash as code constants, with a verdict from any other model ineligible; the same write path as `reconcile --from-findings` (identical edges and `status` export); one git commit and one `log.md` entry per run listing each applied edge with verdict and confidence; every edge individually reversible with `unrelate`; never undirected, `reaffirms` or `unrelated` findings; no confidential concept sent off-device. A marker on applied edges (`applied_by: auto`) is required, so that they can be audited and bulk-reverted. No production code is written before that change and ADR merge.

## What a FAIL means

The mode is not built. The outcome is recorded in a verdict file under `results/` here and in a comment on #1350, and `openkos revisions` plus `reconcile --from-findings` keep their per-item consent. ADR-0034's closing rule applies by analogy: a different signal can qualify only through its own pre-registered measurement.

## What a result does not establish

Stage 0 labels are one drafter's, with a blind second labeller and a third-model adjudication, not the owner's. The library fixture is one domain, one voice, English only, tuned for `qwen3:8b`. Stage 1 labels are only as good as two blind readings of short Decisions; a PASS shows agreement with those labels on three development series, not that the mode is correct on a user's bundle. The result covers one judge model and one prompt hash. This section is carried into every verdict file.

## Owner confirms

The task ran autonomously. Each choice below took the strictest reasonable option and may be overridden before the merge.

1. **Stage 0 is a gate on stored library runs** before any AMI spend. The issue asked for AMI series only; Stage 1 is AMI-only, and Stage 0 decides only the confidence question.
2. **Both verdicts must pass.** The issue left "REVERSES alone or both" open; REVERSES alone is rejected because `revises` is written too.
3. **R2 is zero, R3 is a 0.95 Wilson lower bound with at least 60 applied calls, R5 caps a pair at one wrong trial.**
4. **`t*` is chosen on one arm and judged on another** (different series in Stage 1; different runs only in Stage 0, disclosed).
5. **Gold decisions, not extraction output, feed Stage 1**, so the number is the application error only.
6. **Disagreements between blind labellers go to the owner, not to a model**, stricter than the delegated adjudication the library fixture used.
7. **An unpowered Stage 1 is INSUFFICIENT and counts as a fail**, never a pass.
8. **The `applied_by: auto` marker is required if the mode is ever built** (the issue left it open).
9. **The expected Stage 0 outcome (FAIL) is stated before the verdict file is written**, and the numbers seen while drafting are disclosed above.
