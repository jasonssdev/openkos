# Pre-registration: screening generation models for the transcript decision pass

**Status: REGISTERED 2026-10-18, before any screening run.** The rules below are
fixed for the screen and for the confirmatory run it may trigger. A change after
this point is a new registration, not an edit to this one. This file is merged to
`main` before the first screening run, so its timestamp is public.

## Question

Does a different local generation model make the transcript decision pass
(`transcript_decisions`) recover the hand-annotated decisions better than
`qwen3:8b`, measured on the thesis's own decision-level metric?

This is a **screen**: it picks at most one model worth a confirmatory run. It
adopts nothing. No default (model, key, prompt or cap) changes because of it.

## Why

#1364 ended with "nothing here separates a prompt problem from a `qwen3:8b`
problem". On TS3009 the pass with `qwen3:8b` recalled 0.149 of the gold, failed
11 of 60 meeting-runs (all `OllamaGenerationCapped`, all on TS3009a) and produced
83.8 Decisions per run. The thesis's generation model is not fixed yet, so other
models are in scope.

## Models

All four already pulled in the local Ollama; none is pulled or removed for this.

| Model | Role |
|---|---|
| `qwen3:8b` | baseline, re-run (the stored #1361/#1364 runs predate the per-item subject and value this metric needs) |
| `qwen3:14b` | candidate |
| `qwen3.6:27b` | candidate (dense) |
| `qwen3.6:35b-a3b` | candidate (mixture of experts, about 3B active) |

## Data and arms

AMI ES2015 a-d and TS3009 a-d, the two adjudicated development series
(`scoring.DEV_SERIES`; IS1004 is not adjudicated). The held-out evaluation
series are never touched; the harness refuses them. Gold files and transcripts
stay in the thesis checkout, read-only; nothing from them is copied here.

- `--arm pass` only (the decision pass alone). No baseline arm: the question is
  between models, not between the pass and today's extraction.
- **n = 5 runs per model per series** for the screen. n = 5 swings widely on
  this harness, which is why the selection rule below is written against the
  observed run-to-run spread and why a winner gets a confirmatory n = 15.

Frozen configuration for every model: decision-pass prompt hash
`6d3962b0dd75650c`, the harness's generation ceiling (8192) and context window
(12288), the #1361 one retry per failed window, `MAX_PASS_DECISIONS` 25 and
`MIN_VALUE_WORDS` 3, harness code at this registration's merge commit. Only the
model tag varies.

The one harness change in this registration is that pass items now store the
Decision's `subject` and `value` (the thesis metric embeds them), and
`--results-dir` lets runs be stored outside the worktree. Neither changes what
the pass does or how the harness recall is computed.

## Timing probe (before this registration, not part of the screen)

One pass-arm run per model on TS3009, to forecast wall-clock. The probe runs are
kept but are **not scored** on either metric and are excluded from every number
in the results. Only their wall time and their per-meeting Decision counts and
failures (printed in the run log) were seen.

| Model | TS3009 probe wall time | Failed meeting-runs | Decisions (a/b/c/d) |
|---|---|---|---|
| `qwen3:8b` | 785 s | 1 of 4 (TS3009a, generation cap, 516 s) | 9/25/25/25 |
| `qwen3:14b` | 340 s | 0 of 4 | 6/15/17/11 |
| `qwen3.6:35b-a3b` | 209 s | 0 of 4 | 14/22/25/25 |
| `qwen3.6:27b` | 855 s | 0 of 4 | 13/18/20/11 |

Forecast for the screen. TS3009 per run: the #1364 `qwen3:8b` mean (633.1 s),
the probe for the others. ES2015 per run: the #1361 `qwen3:8b` mean (167.8 s),
scaled by each model's speed relative to `qwen3:8b` on the three TS3009
meetings where no window hit the cap (b, c, d: 268.6 s for `qwen3:8b`).

| Model | speed vs `qwen3:8b` (b+c+d) | ES2015 s/run | TS3009 s/run | screen (5 + 5 runs) | n = 15 on both |
|---|---|---|---|---|---|
| `qwen3:8b` | 268.6 / 268.6 = 1.00 | 168 | 633 | 1.11 h | 3.34 h |
| `qwen3:14b` | 278.2 / 268.6 = 1.04 | 174 | 340 | 0.71 h | 2.14 h |
| `qwen3.6:35b-a3b` | 155.9 / 268.6 = 0.58 | 97 | 209 | 0.43 h | 1.28 h |
| `qwen3.6:27b` | 659.0 / 268.6 = 2.45 | 412 | 855 | 1.76 h | 5.28 h |

The screen is forecast at about 4.0 h of model time, inside the 10-hour
ceiling, so no model is dropped or shortened. The models run one at a time
(Ollama serializes), each model's ES2015 then TS3009.

## Metrics

**Primary: the thesis metric** (`thesis_metric.py`, validated against the
thesis's own ES2015 extraction: same true positives at every tau). Per run and
series: reference units are the gold `decision` rows (split `compuesta`
children included) as `final_object: final_content`; a pass Decision is
`subject: value`; cosine similarity of `bge-m3` embeddings; one-to-one
Hungarian assignment within each meeting; pairs with similarity >= tau count.

- The deciding statistic is **mean per-run F1 at tau = 0.60**; mean per-run
  recall at tau = 0.60 is a no-regression condition.
- The full curve, tau = 0.50 to 0.90, is reported for every model, with recall
  and F1 at 0.50, 0.60, 0.70 and 0.80 in the tables. If the ordering of models
  changes across the curve, the results say so.

**Secondary: the harness recall** (`scoring.py`, the evidence-overlap rule of
#1348), reported, not used for selection.

**Guardrails**, per model and series: failed meeting-runs (`failure` set) as n
of 20, and Decisions per meeting (mean). Also reported: mean seconds per run,
and the language of the produced subject and value (English or Spanish; the
gold is Spanish and the thesis records the metric collapsing at tau >= 0.80
for English extractions).

## Selection rule (screen)

For a candidate C against `qwen3:8b` (B), on each series s separately:

- `dF1_s` = mean F1 of C at 0.60 minus mean F1 of B at 0.60.
- `S_s` = the larger of C's and B's per-run F1 min-max range at 0.60 on s.

C **advances** only if ALL hold:

1. **Margin.** On BOTH series, `dF1_s >= max(0.05, S_s)`: better by at least
   0.05 F1 and by more than either model's own run-to-run spread.
2. **No recall loss.** On BOTH series, C's mean recall at 0.60 >= B's.
3. **Failures.** On each series, C's failed meeting-runs <= max(2, B's count),
   of 20.
4. **Volume.** On each series, C's mean Decisions per meeting <= 1.10 x B's.

If several candidates advance, the one with the largest mean of `dF1_ES2015`
and `dF1_TS3009` advances; at most one model advances. If none does, the result
is recorded as it is and the confirmatory run does not happen.

## Confirmatory run (only if a model advances)

Fresh **n = 15** runs of the advancing model on both series, pass arm, same
frozen configuration, no reuse of screen runs (a screen winner's numbers carry
selection bias). The comparator is a fresh n = 15 of `qwen3:8b` when both
together are forecast to fit in about 8 hours; otherwise the advancing model
runs alone, it is compared against `qwen3:8b`'s 5 screen runs, the comparison is
labelled weaker, and the fresh `qwen3:8b` n = 15 is listed as the next step.

The confirmatory run applies the same four conditions with n = 15. Passing it
makes the model a candidate for a separate, maintainer-owned decision; it still
changes no default.

## Expectations, stated before the run

- Every model's thesis-metric scores collapse at tau >= 0.80 if its output is
  English, as on the thesis's ES2015 extraction.
- F1 is held down by volume: the pass emits up to 25 Decisions per meeting
  against 4 to 8 gold decisions, so precision stays low for every model, and a
  model that emits fewer Decisions can gain F1 without recalling more.
  Condition 2 exists so that a gain made only by producing less does not
  advance.
- At `qwen3:8b`'s volume (about 80 Decisions per run against 26 or 27 gold
  decisions) one extra match is worth 2 / (80 + 27) = 0.019 F1, so the 0.05
  floor is about 3 extra matched decisions per run (0.05 / 0.019 = 2.7).
