# Pre-registration: how much gold content each extraction stage loses (#1559)

**Status: REGISTERED 2026-10-20, before any new per-stage figure, boundary
contrast or cap replay was computed.** This file is committed before the
harness `stage_loss.py` exists. A change after that point is a new
registration, not an edit to this one.

## Question

#1557 and #1558 stored every stage of the real pipeline for every run, so
the gold content each stage loses can be read without generating anything.
The issue's first table (L6 to L11, point estimates, round 1) has no
intervals, and the other loss points of the #1557 design, section 4.1, have
no number at all. This study produces one table over L1 to L14: a measured
loss with an interval, an exposure count where the loss itself cannot be
isolated, or an explicit "not measurable from stored data, needs X".

## No generation

- Only the records and caches under
  `~/Dev/Projects/openkos-eval-runs/concept-screen/` (#1557) and
  `~/Dev/Projects/openkos-eval-runs/window-screen/` (#1558) are read.
- No chat call and no embedding call is made. Embeddings come from each
  directory's `bge-m3` cache (`cache/embed.sqlite`, digest `790764642607`),
  through the cache-only guard of `drop_ablation.py`. A cache miss raises. A
  measurement that hits a miss is recorded as **not measurable**, with the
  number of missing texts, and is not computed another way.
- No production code changes. Production's own functions are called where a
  stage is replayed.

## Populations

- **P1, the model screen.** Round 1, run 1, pool DA (ES2015a, IS1004b,
  TS3009c, ES2015d, IS1004d), the five models of `drop_ablation.py`:
  25 meeting-runs, 4,000-char windows. **Secondary, reported only:** runs 1
  and 2 for `qwen3:8b` and `qwen3.6:27b` (10 more).
- **P2, the window screen.** `qwen3:8b`, runs 1 to 3, pool DA, the four arms
  4,000, 6,000, 8,000 and 12,000: 15 meeting-runs per arm.

## Metric

The #1557 primary, unchanged: window lift, the bge-m3 coverage of a window's
own gold minus a foreign meeting's gold, averaged over the windows that hold
gold (`window_lifts`). A stage's loss is its lift minus the previous stage's
lift. Intervals are the paired two-stage bootstrap of the screen (meetings,
then windows; 10,000 resamples; seed 42). "Pooled" means each window's
delta is averaged over the five models first, so a loss point is judged on
the typical candidate and not on one model.

In gold-sentence units, beside the lift: a windowed gold sentence is
**covered** when its best cosine against its own window's objects is at
least 0.60 (a value of the screen's tau grid). Per stage: gold sentences
covered at the previous stage and not at this one (lost), and the reverse
(gained), as `n of TOTAL`. Reported only; no rule reads them.

## Measurements, per loss point

| # | Measurement | Population | How |
|---|---|---|---|
| L6, L7, L8, L10, L11 | Stage loss with interval, per model and pooled | P1 | Recorded stages `raw`, `dedup`, `drops`, `ceiling`, `judge`, `final`. L8 is `pre_ceiling` to `ceiling`. The additions between `drops` and `pre_ceiling` (participant capture, re-ask) are a gain, not a loss point, and are shown as such. |
| L12 | Loss of the legacy cap of 6 | P1 | Replay: the first `_MAX_OBJECTS_PER_SOURCE` (6) objects of the recorded `drops` stage, in production order, against the full `drops` stage. This is the legacy path only (`union_judge` off): it has no participant capture and no judge. The re-ask additions are not replayed. |
| L3 | Skipped windows | P1, P2 | Count of gold windows whose call failed (`skipped_chunks`, outcome `timeout` or `unparseable`), `n of TOTAL`, and the gold sentences in them. A failed window scores lift 0 at every stage. The lift at stake is **imputed**, not measured: failed gold windows as a share of all, times the mean `drops` lift of the other gold windows of the same model. |
| L2 | Exposure to the one-object attractor | P1 | Share of window calls whose raw reply held exactly one object, and the windowed gold sentences in such windows that hold three or more gold sentences, with how many of them are below the 0.60 cover. The loss itself needs a counterfactual prompt (model calls), so this is an exposure, not a loss. |
| L1 | Boundary contrast | P2 | See below. |
| L5, L14 | Context-window exposure | P1, P2 | From the stored calls: largest prompt, calls whose prompt plus reply exceeded `num_ctx`, calls ended by `length`, by call kind. A quality loss cannot be isolated. |
| L9 | Excerpt exposure | P1 | Count of recorded `bounded_prompt_calls` against the number of selector-judge calls. The effect on selection needs judge calls on the full source. |
| L4, L13 | Not measurable | | Recorded with what each needs. |

### L1, the boundary contrast

Gold sentences with a linked dialogue act (`summlink`) are classified per
arm as **straddling** (the linked acts fall in two or more windows) or
**contained**. For each arm A in {6,000, 8,000, 12,000} against the 4,000
baseline:

- **R (resolved):** straddles at 4,000 and is contained at A.
- **C (control):** contained at both.
- Outcome per sentence and arm: its best cosine against the `drops`-stage
  objects of any window, averaged over the three runs. The `drops` stage is
  used so the judge's selection (L10) does not enter.
- **Contrast** = mean (arm A minus 4,000) over R, minus the same over C. The
  control nets out the general effect of a coarser window (fewer objects).
  A positive contrast means the boundary removal helped the sentences that
  straddled. The interval is a bootstrap over meetings, then sentences.
- Straddling shares per arm are reported as `n of TOTAL`.
- Limits, stated now: sentences are not randomized to R and C, a straddling
  sentence differs in kind from a contained one, and n is small. The contrast
  is a lower-powered stand-in for the experiment that was never run, and is
  read as such.

## Decision rules

1. **Material loss point** (L6, L7, L8, L10, L11, L12, pooled over the five
   models):
   - **Material:** the pooled loss is at most -0.005 and its interval's
     upper bound is below 0.
   - **Suggestive:** the pooled loss is at most -0.005 and the interval
     holds 0.
   - **Not material:** otherwise.
   - -0.005 is about a tenth of the pooled raw lift (about 0.06) and the size
     of the seed-to-seed swing of the judge stage in #1571 (0.0306 vs 0.0258
     on `qwen3:8b`).
   - The number of models with a point at or below -0.005 is reported as
     `n of 5`.
2. **L1 verdict**, per arm: **supported** if the contrast's lower bound is
   above 0 and R holds at least 10 sentences; **reverse** if its upper bound
   is below 0; **not detected** otherwise. L1 is **material** if at least 2
   of the 3 arms are supported, **not detected** if none is, and otherwise
   **suggestive**.
3. **L3** is material only if failed gold windows are at least 2% of the
   gold windows; the imputed lift is never quoted as a measurement.
4. **Exposure rows (L2, L5, L9, L14)** carry no verdict on loss. They say
   whether the loss point is reached in these data.
5. **Dominance.** The loss points are ordered by the size of their pooled
   point loss among those that are material or suggestive; ordering never
   promotes a "not material" point.
6. **Reconciliation, before any new number is read.** The recomputed stage
   lifts must reproduce the published #1559 table (`qwen3:8b`: 0.0478,
   0.0478, 0.0390, 0.0390, 0.0390, 0.0306, 0.0306) to four decimals on all
   five models, and the recorded L7 loss must equal #1572's. A mismatch
   invalidates the run.
7. **Close rule for #1559.** The issue's done-when is a per-stage loss table
   with a fix issue for every material loss point. It can close if every
   loss point is either measured, an exposure, or explicitly not measurable
   with its requirement named, and every material loss point already has a
   fix issue or is reported to the owner for one.

## Reuse by citation (not recomputed)

- **L7, per rule:** #1572, `DROP_ABLATION_RESULTS.md`.
- **L10, coupling and precision:** #1571, `JUDGE_STUDY_RESULTS.md`; #1579
  step 1, `CUT_PRECISION_RESULTS.md`.
- **Window size (L1, L2 mechanism), latency and feasibility (L5):** #1558,
  `evals/concept_window_screen/RESULTS.md`.

The new numbers are the intervals on L6, L8 and L11, the pooled L7 and L10
intervals, L12, L3, the L1 contrast and the exposure counts. The point
estimates of L6 to L11 reproduce the published ones.

## Disclosed before registering

Only counts that need no embedding were looked at, to decide what is
measurable: the straddling share of gold sentences per arm (36 of 90 at
4,000), the window outcomes per model, the number of recorded
`bounded_prompt_calls`, and the prose fixtures' sizes. No lift, coverage or
cap replay was computed. The `transcript_decisions` option is off in the
screen's pipeline (`screen_pipeline.py`), so no decision-pass output was
stored.
