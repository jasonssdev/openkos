# Pre-registration: can the selector judge's cut be removed, keeping only the positional backstop? (#1579, step 5)

**Status: Registered when this file merges to `main`; no run before the merge.**
A change after the merge is a new registration, not an edit. The owner confirmed
every item that was marked for confirmation on 2026-10-27, unchanged from the
recommended defaults (summary at the end). Part of #1556; refs #1579.

This file changes nothing in `src/`, no default and no spec. No model runs
before the merge.

## Question

Steps 1 to 3 and the downstream-cost measurement (`CUT_PRECISION_RESULTS.md`,
`CUT_REDUNDANCY_RESULTS.md`, `LABEL_STUDY_RESULTS.md`, `DOWNSTREAM_COST.md`)
found a measured cost of the cut (window lift -0.0161 [-0.0276, -0.0081],
38 of 450 gold sentences) and no measured benefit on support, redundancy or
standalone value (all three inconclusive, point estimates near zero).
`DOWNSTREAM_COST.md` priced the other side: removing the cut adds about 115
objects and embedding calls and 8 to 10 identity review rows per 25 sources.

**Hypothesis H.** Dropping the selector judge and keeping only the existing
ceiling (24) and positional backstop (20) is (a) **non-inferior** to the
judge-cut on gold-window quality, and (b) its **downstream cost stays within
the budget** fixed below.

"Backstop only" is option D of `NEXT_STEP.md`: the arm `none` of #1571 (the
recorded `ceiling` stage, which is what production keeps when the judge
fails closed) with the backstop of 20 applied last, as production applies it.

## Honest framing, fixed before any number is computed

1. **The primary population is the data that suggested H.** Steps 1 to 3
   and the cost measurement all read the same 25 meeting-runs. A verdict on
   them alone is a re-analysis, not an independent test. That is why a
   confirmation set (below) is part of the decision rule, not an extra.
2. **The lift gate is close to guaranteed by construction.** The backstop-only
   set is the judge-kept set plus the restored objects, minus 23 positional
   cuts. Window lift is coverage, so adding objects cannot lower it much. The
   gate is a sanity check on the backstop's positional loss, not a hard test.
   The informative gates are precision and cost.
3. **The cost budget is set with the measured ratios already known** (from
   `DOWNSTREAM_COST.md`). It is a commitment the owner makes before the
   replay, not a surprise test. The run recomputes those counts under the
   registered arm definition and the pinned population.
4. **ADOPT-CANDIDATE adopts nothing.** It licenses writing an ADR and a spec
   delta (the `extraction-union-judge` spec requires a judge-selects
   contract), each in its own PR, as epic #1556 requires.

## Data, arms, seeds

**Primary population (model-free).** The population of steps 1 and 3, pinned
by the same hashes: round 1, run 1, pool DA (ES2015a, IS1004b, TS3009c,
ES2015d, IS1004d), the five extractors of #1557 (`qwen3:8b`, `qwen3.6:35b-a3b`,
`gemma4:26b-a4b`, `qwen3:14b`, `qwen3.6:27b`): 25 meeting-runs, 352 ceiling
objects, 303 carrying a window (sha256
`76a9d42c3d70445ae4899d9b21c4a1c08299a4030f4c3d3867d8462a052aef84`). Every
meeting is a development series (ES2015, IS1004, TS3009); the harness
allowlist `screen_data.ALLOWED_MEETINGS` refuses any other.

**Confirmation set (independent meetings).** Free-pool meetings with
`summlink` gold, extractor `qwen3:8b` (the production extractor), runs 1 to 3
of the stored #1557 caches: **IS1002b, IS1002c, IS1002d, IS1005a, IS1005b,
IS1005c**. **TS3012a/b/d are excluded although `POOL_F_WINDOWED` lists them:
TS3012 is ineligible under epic #1556.** The harness must refuse them
explicitly, not rely on the allowlist. No thesis evaluation or reserve series,
and no TS3005. Feasibility is a preflight item: the stored extraction and
`bge-m3` caches must cover these meetings with zero misses, or the
confirmation is reported as not run and the verdict cannot be ADOPT-CANDIDATE
(it falls to INCONCLUSIVE).

**Arms** (all derived from the stored #1571 replays and #1557 caches; no
extraction call):

| Arm | Objects |
|---|---|
| `judge-self` | the extractor's own judge, seed index 0 (the recorded `judge` stage) |
| `judge-fixed` | `gemma4:26b-a4b`, seed index 0 (a stored #1571 replay) |
| `backstop-only` | the recorded `ceiling` stage with the backstop of 20 applied last |

Comparison is `backstop-only` minus each judge arm, **paired** per window (the
judge-kept set is a subset of the ceiling, so the sets are nested). Seed
indices 1 and 2 of the judge arms are reported, not decisive. `gemma4:26b-a4b`
as extractor has one arm (self = fixed), as in steps 1 to 3.
For the confirmation set the `judge-self` arm is the recorded judge stage; the
`judge-fixed` arm needs new `gemma4:26b-a4b` calls (forecast below).

Interval: the two-stage cluster bootstrap of steps 1 to 3
(`screen_metrics.two_stage_bootstrap`; meetings with replacement, then windows
within a drawn meeting; **10,000 resamples, seed 42**), percentile; one-sided
95% bounds for non-inferiority gates.

## Metrics, margins, justification

### P1, primary: window lift (non-inferiority)

Per-window mean lift (the #1557 / #1571 quantity), `backstop-only` minus
`judge`, pooled over windows, per arm. **Margin: -0.005 (lower one-sided 95%
bound must be above it).**

Justification: -0.005 is the materiality bar `STAGE_LOSS_RESULTS.md` used for
a stage loss (L10 sat at or beyond it for 5 of 5 models). It is also about a
third of the measured judge cost (0.0161) and well inside the interval
widths of #1571 (about +-0.01 pooled), so it can be resolved. Expected
(estimate, not a result): the point estimate is positive, near +0.016 less the
backstop's positional loss (23 of 352 objects, in 8 of 25 sources), so the
lower bound should clear -0.005 comfortably. Fails only if the positional cut
removes gold-bearing objects the judge would have kept.

### P2, co-primary: G1 precision (non-inferiority)

Share of windowed objects `gpt-oss:20b` labelled strict-G1 `supported`
(step 1 labels, stored), `backstop-only` minus each judge arm, paired by
window. The restored objects are exactly the cut objects minus the positional
cuts, so the difference equals `w * (P_restored - P_kept)` where `w` is the
restored share of the backstop-only set (about 0.43, estimate).
**Margin: -0.05 (lower one-sided 95% bound above it). Owner-confirmed:
-0.05.**

Justification: step 1 found restored-type objects as supported as kept ones
(self 0.928 vs 0.927; fixed 0.937 vs 0.919). The step 1 interval half-width on
the cut-minus-kept difference was about 0.07; scaled by `w` it is about 0.03,
so -0.05 is resolvable, while -0.03 would not be. -0.05 matches the floor
#1586 used, and is tighter than the -0.10 that #1590 accepted for a different
question (an engine on identical weights). Hard-to-resolve alternatives:
-0.03 (not powered), -0.10 (looser than any earlier precision bar).

The 23 positionally cut objects have no step 1 label if they carry no window;
they are removed from both sides, and the count is reported as `k of N`.

### S1, secondary (reported; gating status below)

- **Clutter share** (step 3 labels: `merge` or `drop`), `backstop-only` minus
  judge, per arm. Expected interval upper bound about 0.08 (self) and 0.06
  (fixed) from `w * D_upper`, so it may not clear a tight margin.
  **Owner-confirmed: NON-gating, reported with the control's weakness
  (the step 3 control passed only at the bar, 8 of 11).** If the owner makes it
  gating, the recommended margin is +0.05 on the increase, and an
  INCONCLUSIVE verdict becomes the likely outcome.
- Per-type counts (Decision, Project, Concept, Event): descriptive only.
  The Project and Decision cut rates (Project 43 of 61 and 55 of 61; Decision
  57 of 129 and 41 of 129) are exactly what this arm would restore.

### C1, cost budget (deterministic counts, `downstream_cost.py` logic)

Same stages and groupings as `DOWNSTREAM_COST.md`, ratio `backstop-only /
judge-kept`, arms `self` and `fixed`. Budget, **owner-confirmed,
below**:

| Count | Measured (`self` / `fixed`) | Budget (registered) |
|---|---|---|
| Objects, per source set | 329 vs 214 (1.54) / 209 (1.57) | at most **1.65x** |
| Reindex embedding calls | same as objects | at most **1.65x** |
| Identity groups = review rows, per 25 sources | +8 / +10 | at most **+12** |
| Identity groups = review rows, per five-bundle workspace | +19 / +23 | at most **+25** (cap 50 must not bind) |
| Revision pair pool (similarity-blind upper bound), per workspace | 1308 vs 481 (2.72x) / 692 (1.89x) | **reported only**, not gated |

Justification: the budget is the measured exposure plus about 7% to 20%
headroom for the confirmation set's different mix. The revision pool is not
gated because it is similarity-blind and the threshold is unmeasured
(`DOWNSTREAM_COST.md` limit). Edge typing and contradictions are zero on a
fresh extraction by construction and are **not priced**.

### V1, validity checks (any failure voids the run)

1. Replaying the `ceiling` stage with the backstop reproduces the recorded
   `final` stage of the `none` path: 23 of 352 objects cut, in 8 of 25
   sources, and `final` equal on every record (35 of 35 in the plan).
2. Population hashes equal the registered ones; zero embedding cache misses
   (cache-only guard, `OLLAMA_HOST` poisoned for scoring).
3. The step 1 and step 3 labels used equal the stored files (hash recorded in
   the results).

## Sample size and power (assumptions labelled)

Normal approximation. Nothing here is a result. `harness plan` must print the
exact figures from the stored data.

- **P1.** #1571's pooled judge-loss interval is about +-0.01 on the same 25
  meeting-runs. Expected `backstop-only` minus judge is about +0.01 to +0.016;
  lower bound about +0.005; margin -0.005 is cleared with power near 1.
  *Assumes* the positional cut loses little lift (the `none` arm without the
  backstop is only an upper bound; this study measures it).
- **P2.** SE of the paired difference about `0.43 * 0.035 = 0.015` (from step
  1's cut-minus-kept SE of about 0.035). Lower bound at a true difference of 0:
  about -0.025; power to clear -0.05 about 0.95. At a true difference of
  -0.01: about 0.85. At -0.03: about 0.35. *Assumes* step 1's variance
  transfers and `w` is about 0.43.
- **Confirmation set.** About 100 to 150 window-runs; qwen3:8b judges out few
  objects (5.7 of 31 per meeting-run for `self` in round 1, 11 for `fixed`).
  Its lift interval is wide, about +-0.02. It therefore can show only that
  lift is not clearly worse (rule below), **not** that it is within -0.005.
  Its precision on the restored objects (tens of objects) is descriptive only.

Because one 25-run population and one small confirmation set are all the
eligible data, INCONCLUSIVE is an honest possible outcome.

## Decision rule

Order: V1 failure or degraded run decides nothing (the run is void or
degraded, reported as such). Otherwise, evaluating arms `self` and `fixed`
on the primary population:

| Outcome | When |
|---|---|
| **ADOPT-CANDIDATE** | P1 non-inferior **and** P2 non-inferior in **both** arms, C1 within budget in both arms, and the confirmation set ran with its lift point estimate `backstop-only` minus judge not below -0.005 (it cannot be inferior at that precision, only not clearly worse) |
| **NOT** (do not remove the judge) | P1 or P2 **inferior** in either arm (the one-sided 95% upper bound is below its margin), or C1 over budget in either arm |
| **INCONCLUSIVE** | everything else: a gate not resolved, arms disagreeing, or the confirmation set not run |

If S1 is made gating by the owner it joins the ADOPT-CANDIDATE conjunction and
the NOT disjunction by the same rule.

**What each outcome licenses.**

- *ADOPT-CANDIDATE* licenses **writing an ADR**, a spec delta to
  `extraction-union-judge`, and the production change in its own PR(s). It
  does not adopt, does not change any default, and is not an approval. The
  results PR adopts nothing.
- *NOT* is recorded as the finding that the cut buys protection the data
  cannot see, or that its removal costs too much; the judge stays.
- *INCONCLUSIVE* changes nothing. The next question is a human-labelled
  sample, since more LLM labelling at this n is unlikely to narrow the
  intervals (`LABEL_STUDY_RESULTS.md`).

## What this cannot show

- **Whether a person wants the restored objects.** P2 measures source
  support; S1 is a weak LLM label. No human curator is in the loop.
- **Text that is not a meeting.** All sources are English AMI meetings. The
  judge also runs on prose notes in production; they are not tested.
- **Large sources.** The backstop binds in 8 of 25 sources and is positional,
  not quality-aware. On sources with many more than 20 objects the loss would
  be larger and is not bounded here.
- **Revision candidates, proximity edges, edge typing, contradictions.**
  Not priced (see C1).
- **Extractors outside the five**, languages other than English, and any
  hardware other than the recorded one.
- **Independence.** The primary population is the one that suggested H; the
  confirmation set is small.

## GPU forecast

- **Primary population: no model call.** Replay, scoring and cost counts are
  CPU-only, with embeddings from the cache and `OLLAMA_HOST` poisoned.
- **Confirmation set** (needs Ollama exclusive to the study, run only when
  idle, after the other registered job finishes):
  - `judge-self`: recorded judge stage, no call.
  - `judge-fixed`: one `gemma4:26b-a4b` call per meeting-run, 18
    meeting-runs (6 meetings x 3 runs) at about 3.4 s (#1571 measured 350 s
    over 102 calls) plus one model load: about **2 to 3 min**.
  - G1 labels for the restored objects only (`gpt-oss:20b`, the step 1
    protocol, one call per window): at most 200 windows x 9.9 s (step 1) at
    most **33 min**; expected well under 15 min.
  - **Budget: 45 min of measured model time.** If the first 20 label calls
    average more than 25 s, stop and report; no partial verdict is read. If
    the fixed-judge digest differs from `001e5dafc3c7...` or `gpt-oss:20b`
    from `f38aa0c53da5...`, the run refuses.
- Raw replies and labels go outside the repo
  (`~/Dev/Projects/openkos-eval-runs/backstop-only/`). Only numbers enter the
  repository, in a separate results PR that adopts nothing.

## Harness code needed (not written here)

A new `evals/concept_model_screen/backstop_only.py` with a model-free
`--self-test` (the eval sweep discovers harnesses by that flag), reusing
`cut_profile.py`, `cut_precision.py`, `label_study.py`, `downstream_cost.py`,
`judge_study.py` and `screen_metrics.py`:

1. `population`: recompute and print the pinned hash, counts and the
   confirmation meeting list; refuse any meeting outside the allowlist and
   refuse TS3012 explicitly.
2. `replay`: for each record, apply the production backstop to the `ceiling`
   stage (import the constant; do not restate it, or pin a self-test) and
   assert equality with the recorded fail-closed `final` (V1.1).
3. `score`: paired per-window P1 (lift from the cached embeddings, cache-only
   guard), P2 (join to stored step 1 labels), S1 (join to stored step 3
   labels), per-type descriptive counts; two-stage bootstrap; apply the
   decision rule, including `degraded`/`void` precedence; print `k of N`.
4. `cost`: wrap `downstream_cost.py` for the backstop-only set and check C1
   against the registered budget.
5. `confirm` (the only subcommand that calls models): fixed-judge replays and
   G1 labels for the confirmation set, resumable, with the digest pins and
   the 25 s stop line.
6. Tests: the decision rule on synthetic intervals (each outcome reachable),
   TS3012 refusal, nested-set pairing, and a mutation check on the margin
   comparison direction (a verdict must flip when the margin flips).

## Owner confirmations (summary)

Confirmed by the owner on 2026-10-27, all at the recommended default.

| # | Item | Registered value |
|---|---|---|
| 1 | P1 lift margin | -0.005 |
| 2 | P2 precision margin | -0.05 |
| 3 | S1 clutter gating | non-gating; if gating, +0.05 |
| 4 | C1 object and embedding ratio | at most 1.65x |
| 5 | C1 identity review rows | +12 per 25 sources, +25 per workspace |
| 6 | Confirmation set required for ADOPT-CANDIDATE | yes, 6 non-TS3012 free-pool meetings, `qwen3:8b` |
| 7 | GPU budget for the confirmation | 45 min |
