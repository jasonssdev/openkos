# Pre-registration: the selector judge's loss vs the extractor's (#1571)

**Status: REGISTERED 2026-10-19, before any judge call was sent and before
any arm was scored.** This file is merged to `main` before the first model
call. A change after that point is a new registration, not an edit to this
one.

## Question

The #1559 per-stage loss table shows the selector judge (L10) as the largest
per-stage loss for every candidate: -0.0084 to -0.0398 window lift. The judge
runs on the extraction model itself, so a model's final score mixes how well
it extracts with how well it judges its own output. Is the loss the judge's
(any judge cuts that lift), or the coupling's (a model judging its own
candidates cuts more than a fixed judge would)?

## No new extraction

- Only the #1557 records and caches in
  `~/Dev/Projects/openkos-eval-runs/concept-screen/` are read. That
  directory is never written.
- Each record is replayed through production's unpatched
  `extract_concept_union`, with the screen's stage capture
  (`screen_pipeline.run_source`). Every call except the selector judge's is
  served from the #1557 chat cache. A non-judge cache miss raises and stops
  the record: the extraction is never re-generated.
- Only the selector-judge call goes to a model. New calls and their cache
  are written to `~/Dev/Projects/openkos-eval-runs/judge-study/`.
- Scoring reads embeddings from the #1557 `bge-m3` cache only, through the
  #1572 cache-only guard. The judge only selects among its input, so every
  arm's objects are a subset of the recorded `ceiling` stage, which #1559
  already embedded. A miss defers that measurement.

## Data

- Pool DA (ES2015a, IS1004b, TS3009c, ES2015d, IS1004d).
- **Primary:** round 1, run 1, all five extractors: `qwen3:8b`,
  `qwen3.6:35b-a3b`, `gemma4:26b-a4b`, `qwen3:14b`, `qwen3.6:27b`
  (25 meeting-runs). These are the figures the issue quotes.
- **Secondary:** runs 1 and 2 for `qwen3:8b` and `qwen3.6:27b`, the only
  extractors with two runs (20 meeting-runs). Reported only.
- The prose fixtures are not used: window lift, the primary, is defined on
  the AMI windows only.

## Arms

| Arm | Judge | How it is obtained |
|---|---|---|
| `none` | no judge | The recorded `ceiling` stage: the judge's input. It is what production keeps when the judge fails (fail-closed, design D7), and the backstop never bound in #1557. |
| `self` | the extractor | Seed index 0 is the recorded `judge` stage. Seed indices 1 and 2 are new calls to the extractor. |
| `fixed` | `gemma4:26b-a4b` | Seed indices 0, 1 and 2, all new calls. |

- **Fixed judge.** `gemma4:26b-a4b` is the packaged `DEFAULT_JUDGE_MODEL`
  (`config.py`, ADR-0047), digest `001e5dafc3c7…` as pinned in the #1557
  stamp. The run refuses to start if the installed digest differs.
- **`gemma4:26b-a4b` as an extractor** has the same model in both judge arms.
  Its `fixed` and `self` calls share a cache key, so they are one sample,
  reported once, and excluded from the coupling comparison.
- **Seeds.** Seed index `s` sends the judge seed `run + 100·s`, and the
  production retry adds 1000 as in #1557. Index 0 is exactly the #1557 seed.
- **Settings.** As #1557: `num_ctx` 12288, `num_predict` 8192, `think`
  false. The judge timeout is the screen's whole-source timeout for that
  model (3 × its stage-0 window timeout: 180 s for `gemma4:26b-a4b`).
  A judge that fails both attempts falls back to its whole input, as
  production does; such calls are counted and reported.

## Validity checks, before any number is read

1. **Self replay.** For every record, the all-cache replay at seed index 0
   with the extractor as judge must reproduce the recorded `ceiling`,
   `judge` and `final` stages exactly (type, title, description, window, in
   order), with zero model calls. A record that fails is excluded and
   listed. More than one failure makes the run invalid.
2. **Same judge input.** In every new replay, the `ceiling` stage must equal
   the recorded one. Otherwise the record is excluded for that arm.
3. **No extraction call.** Every non-judge call in every replay is a cache
   hit. Any miss stops the run.

## Measures

Window lift is the #1557 primary (`window_lifts`, `screen_metrics.lift`,
unchanged), averaged over windows that hold gold. For an arm with three seed
indices, each window's lift is first averaged over the seeds.

Per extractor:

- **Lift and counts per arm.** Window lift, objects kept (sum over
  meeting-runs, mean over seeds), objects judged out, and of those, how many
  carry a window (only those can move window lift).
- **Judge loss.** `lift(self) - lift(none)` and `lift(fixed) - lift(none)`.
- **Coupling.** `lift(fixed) - lift(self)`.
- **Intervals.** The paired two-stage bootstrap the screen used (meetings,
  then windows; 10,000 resamples; seed 42), on per-window deltas.
- **Lift lost per judged-out object.** The judge loss divided by the number
  of judged-out objects that carry a window. Descriptive only.
- **Seed noise.** For each arm with three seeds, the range of window lift
  across seed indices. Descriptive only.

## Decision rules

Applied per extractor, on the primary set, to the four extractors other than
`gemma4:26b-a4b`:

1. **Coupling-attributable** if the coupling interval's lower bound is
   above 0 (the fixed judge keeps significantly more lift than the self
   judge).
2. **Judge-attributable** if it is not coupling-attributable, and the
   fixed-judge loss interval's upper bound is below 0 (a fixed judge still
   costs lift).
3. **Unresolved** otherwise.

Overall verdict:

- **The coupling** if 2 or more of the 4 extractors are
  coupling-attributable.
- **The judge itself** if 2 or more are judge-attributable and none is
  coupling-attributable.
- **Not resolved at this n** otherwise. The point estimates are reported with
  their intervals, and the pattern is described, not decided.

Follow-up:

- If any extractor is coupling- or judge-attributable, one fix issue is
  filed. It proposes a change (for example a fixed model for the selector
  judge, or a change to the selection) and does not make it. No default
  changes here, and a default change would need an ADR.
- Otherwise the judge is recorded as **not established as the cause** at
  this n.

**Out of scope.** Window lift measures coverage. The judge exists to buy
precision, and G1 precision is not re-measured here. A fix issue must
weigh both.

## Forecast and the shortening rule

The forecast uses the #1557 recorded judge wall time per call:

| Block | Calls | Seconds per call | Forecast |
|---|---|---|---|
| Fixed judge on 30 non-gemma meeting-runs, 3 seeds | 90 | 15.0 | 22.5 min |
| Fixed (= self) judge on 5 gemma meeting-runs, seeds 1 and 2 | 10 | 15.0 | 2.5 min |
| Self judge, seeds 1 and 2: `qwen3:8b` (10), `qwen3.6:27b` (10), `qwen3.6:35b-a3b` (5), `qwen3:14b` (5) | 60 | 19.5, 62.1, 11.6, 36.9 | 35.3 min |
| Model loads (5) | | about 60 | 5 min |
| **Total** | 160 | | **about 65 min** |

The target is 2 h of Ollama time, and the run uses Ollama alone.

Order: the whole fixed-judge block first (one model resident), then the
self-judge blocks one extractor at a time.

**Shortening rule**, applied in order and decided after the fixed-judge
block from its measured seconds per call:

1. If the re-forecast total exceeds 2 h, the self arm runs seed index 1
   only.
2. If it still exceeds 2 h, the self arm keeps only the recorded seed
   index 0.
3. Hard stop at 2.5 h of model time: any block not finished is reported as
   deferred, not estimated.

A shortened self arm is compared on the seeds it has. The fixed arm keeps
its three seeds.

## Disclosed before scoring

While planning, the per-record judge-call wall times and the recorded
`ceiling` and `judge` stage object counts of the #1557 records were printed.
These are already published in `RESULTS.md` in aggregate. No fixed-judge
reply exists, and no arm was scored.
