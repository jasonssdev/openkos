# Decision fidelity: the registered run

Result of the measurement registered in
[`PREREGISTRATION-1649.md`](PREREGISTRATION-1649.md)
([#1649](https://github.com/jasonssdev/openkos/issues/1649), umbrella
[#1652](https://github.com/jasonssdev/openkos/issues/1652)). It adopts nothing: no
production code, default or prompt changes.

## Verdicts

- **Prompt clause (`A1`): INCONCLUSIVE.** Not adopted; the shipped prompt is unchanged.
- **Commitment guard: NOT RUN.** Stage 0 failed, so by the registration the guard is not run
  and that is the result.

## Inputs and stamp

| Item | Value |
| --- | --- |
| Model | `qwen3:8b`, configuration frozen as #1564 (ceiling 8192, context 12288, `chat_timeout` 600 s) |
| Baseline pin | `decision_pass.py`, its prompt and `llm/ollama.py` unchanged at `f71779a3` |
| Run commit | `07ad015e` |
| Runs | 15 per arm (`A0` baseline, `A1` prompt), interleaved; 180 cells per arm; neither arm `PARTIAL` |
| Scoring | `score_fidelity.py score`, seed 1649, 10,000 resamples |
| Gold | `labels/value_gold.json` (71 anchored items); `labels/line_labels.json` (841 lines) |

## Scorer report

Reproduced from the scorer's `report.md`. Stage-0 gate **FAIL**, unlabelled evidence lines 0.

| arm | runs | recall | value_fidelity | value_hit_rate | uncommitted_rate | labelled | unlabelled | discarded | none | evidence_only | guard_dropped |
|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 15 | 0.292 | 0.658 | 0.200 | 0.298 | 2756 | 0 | 59 | 685 | 21 | 0 |
| prompt | 15 | 0.345 | 0.653 | 0.234 | 0.290 | 2339 | 0 | 59 | 484 | 18 | 0 |
| baseline+guard | 15 | 0.147 | 0.538 | 0.082 | 0.275 | 834 | 0 | 43 | 135 | 0 | 1922 |
| prompt+guard | 15 | 0.124 | 0.573 | 0.074 | 0.225 | 675 | 0 | 19 | 119 | 0 | 1664 |

### Prompt clause: INCONCLUSIVE

- recall (prompt - baseline; non-inferior if lower bound > -0.05): +0.053 [-0.034, +0.137] over 71 clusters
- value_fidelity (superior if lower bound > 0 and diff >= 0.1): -0.035 [-0.179, +0.112] over 36 clusters

Read plainly:

- **M1 (recall).** The lower bound is -0.034, above the -0.05 non-inferiority bound, so recall is
  numerically non-inferior; the point estimate is +0.053 and the interval is wide. M1 is not what
  blocks the verdict.
- **M2 (value fidelity).** The point estimate is **negative** (-0.035). The interval only barely
  reaches +0.10 at its top (hi +0.112), so it cannot be ruled out that the clause helps, but the
  data lean the other way. By the rule (`lo > 0` and `d >= +0.10`) superiority fails by a wide
  margin, and `hi >= +0.10` keeps it out of REJECT. This is INCONCLUSIVE by the rule, **not a
  near-miss in favour** of the clause.

### Commitment guard: NOT RUN

Stage 0 (over `labels/cue_labels.json`, see `labels/stage0.json`): 40 committed and 84
proposal-class lines; sensitivity **0.475** against the required >= 0.85, specificity **0.560**
against >= 0.50. Sensitivity fails, so the guard is not run and no guard verdict exists.

The two `+guard` rows are **descriptive only**; no verdict hangs on them. They show why the guard
is not run: applied offline it dropped 1922 of 2756 baseline Decisions (about 70%) and 1664 of 2339
prompt Decisions (about 71%), and baseline recall fell from 0.292 to 0.147, roughly halved. The
uncommitted rate moved only from 0.298 to 0.275 (baseline), so most of what it removed was
committed.

## Model-free numbers

| Number | `A0` baseline | `A1` prompt |
| --- | --- | --- |
| Cells (failed) | 180 (13) | 180 (16) |
| Failure kinds | 10 `OllamaGenerationCapped`, 3 unparseable reply | 13 `OllamaGenerationCapped`, 3 unparseable reply |
| Windows per run | 99 | 99 |
| Model calls (capped) | 1519 (28) | 1521 (28) |
| Capped-call rate | 1.84% | 1.84% |
| Decisions (mean per run) | 2756 (183.7) | 2339 (155.9) |
| With evidence line | 2756 | 2339 |
| Distinct evidence lines | 602 | 507 |
| Runs with any failed cell | 11 of 15 | 10 of 15 |
| Wall time | 4.83 h | 4.38 h |

Failed cells concentrate on TS3009a (9 and 10) and the ES2015 series. Total model time was about
9.2 h of the registered 15 h budget; the capped-call rate sits at the 1% the forecast assumed plus
a little under one point. Line sheet: 841 distinct lines (268 produced by both arms, 334 only by `A0`,
239 only by `A1`), 33 of them unlocated in their transcript.

The arms differ in volume: the prompt clause stored 15% fewer Decisions (2339 against 2756), which
is part of why recall and the per-line rates are not directly comparable by count.

## Deviations

1. **99 windows per run, not 101.** The registration stated 101; the driver's preflight logged 99 on
   the pinned transcripts. Both arms ran the same 99.
2. **Line labelling.** Two independent Claude readers (A Sonnet, B Opus) each labelled the 841-item
   arm-blind sheet (seed 1649) in three chunks of 281, 281 and 279 items. The 209 disagreements were
   adjudicated by a blind third reader (Opus) who saw both labels unattributed, in random order, and
   could pick any label; they picked neither candidate 4 times. Cohen's kappa on committed versus not
   is **0.596** (raw agreement 0.806); on the four labels kappa is 0.638 (agreement 0.751).
   0.596 is below the registered 0.6, which downgrades the guard's verdict to exploratory. That is
   **moot**: the guard was NOT RUN. The line labels feed only M3 and the guard; the prompt verdict
   rests on M1 and M2 from `value_gold.json`.
3. **Truncated context for some readers.** Several readers first viewed context turns truncated
   (200 to 300 characters) and/or fewer turns than registered. All were told to re-read in full and
   confirm, and every final label comes from untruncated views (reader A re-labelled all 281 items of
   chunk 1, 23 changed; the others re-read the affected items, 0 or 1 changed). Readers shared a
   scratch folder and some working files were overwritten mid-run; each reader moved to a private
   folder, discarded any view drawn from the wrong chunk, and spot-checked 10 ids against their item
   text. No reader saw the key or another reader's output.
4. **33 of 841 lines could not be located** in their transcript and were judged on the line alone.
   The sheet used the `.jsonl` turn files from the window-screen inputs, whose `.md` renderings are
   byte-identical to the pinned ones.
5. **Labels written by Claude models** on the owner's standing delegation (already registered as a
   deviation). Agreement describes two readings of one rubric, not the owner's judgment.

## What this does and does not show

- The clause did not demonstrably improve value fidelity, and the point estimate is negative; the
  data neither support adoption nor rule out a small gain. The shipped prompt stays.
- Nothing here measures answer quality, the default (evidence-less) extraction path, other models,
  other series or other languages (see the registration).
- The `+guard` rows are context, not a result. The cue as registered does not separate committed
  from proposal-class lines well enough (sensitivity 0.475, specificity 0.560) to be a guard.

## Provenance

The scorer was run with its registered command (`score_fidelity.py score`, seed 1649, 10,000
resamples) over the stored runs and `labels/`. The stored runs, sheets and transcripts are working
files outside the repository and are not committed; `labels/line_labels.json` is.
