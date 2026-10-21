# Pre-registration: does the selector judge's cut buy precision? (#1579, step 1)

**Status: REGISTERED 2026-10-20, before any `gpt-oss:20b` call was sent for
this study and before any cut or kept object was labelled.** A change after
the commit that adds this file is a new registration, not an edit to this
one.

## Question

#1571 showed that every selector judge (the extractor judging itself, or the
packaged `gemma4:26b-a4b`) costs window lift. Window lift measures coverage;
the judge exists to buy precision. Are the objects the judge **cuts** about
as source-supported as the objects it **keeps**? If so, the cut costs lift
without buying precision.

This is step 1 of #1579 only. It measures; it changes no production code and
no default. Any loosening of the selection needs its own ADR afterwards.

## No generation by candidate models

- Only the #1571 replay records in
  `~/Dev/Projects/openkos-eval-runs/judge-study/records/` and the #1557
  corpus under `~/Dev/Projects/openkos-eval-runs/concept-screen/` are read.
  Neither directory is written.
- The only model called is `gpt-oss:20b`, the #1557 guardrail judge, with the
  #1557 G1 prompt and parser (`screen_judge.py`, unchanged). No extraction,
  no selector-judge call, no embedding.
- New judge calls and their cache go to
  `~/Dev/Projects/openkos-eval-runs/cut-precision/`.

## Population

- **Primary:** round 1, run 1, pool DA (ES2015a, IS1004b, TS3009c, ES2015d,
  IS1004d), all five extractors (`qwen3:8b`, `qwen3.6:35b-a3b`,
  `gemma4:26b-a4b`, `qwen3:14b`, `qwen3.6:27b`): 25 meeting-runs.
- **Secondary (reported only):** run 2 for `qwen3:8b` and `qwen3.6:27b`, the
  only extractors with two runs: 10 more meeting-runs.
- **Objects.** For each record, the `ceiling` stage (the selector judge's
  input) is partitioned, per judge arm, into KEPT (present in that arm's
  `judge` stage) and CUT (the rest). An object is matched by
  (type, title, description, window), with multiplicity. Only objects that
  carry a window are scored, as in #1557's G1 (the source excerpt is that
  window). Objects with no window are counted and excluded.
- **Arms.** `self` (the extractor as judge) and `fixed` (`gemma4:26b-a4b`),
  each at seed index 0 for the decision. `self` seed 0 is the recorded #1557
  judge stage, `fixed` seed 0 is the #1571 replay. Seed indices 1 and 2 are
  reported as a stability check (point estimates only). For
  `gemma4:26b-a4b` as an extractor the two arms are the same sample and are
  reported once.

## Measure

- **One label per ceiling object.** Per record and per window, all window
  ceiling objects go to one G1 call (`gpt-oss:20b`, prompt and parser from
  `screen_judge.py`: `supported | partial | unsupported`; seed 0, one retry
  at seed 1000 on an unusable reply; a window unusable twice is excluded and
  counted). Because the same ceiling is the input of every arm, one call
  labels the objects for both arms and every seed, so cut and kept are
  judged in the same call, with the same companions. This differs from #1557,
  which labelled the final objects of a window; the absolute levels are
  therefore not compared with #1557's 0.927 / 0.903, only cut with kept.
- **Precision** = supported / all labelled, pooled (strict, as #1557).
  Reported beside it, not decided on: lenient precision
  (supported + partial) / all labelled.
- **Statistic.** D = precision(KEPT) - precision(CUT), pooled over windows.
  Interval: the two-stage cluster bootstrap the screen uses (meetings, then
  windows within a drawn meeting; 10,000 resamples; seed 42; percentile 95%),
  on per-window (kept supported, kept labelled, cut supported, cut labelled),
  the paired form of #1557's `pooled_precision_delta`.
- Counts printed as `n of TOTAL`: labelled objects of all window objects,
  windows labelled of all windows, cut and kept sizes.

## Decision rule

Per judge arm (`self`, `fixed`), on the primary population pooled over the
five extractors, seed index 0. Margin 0.05, the same as #1557's G1 tolerance.

0. **Insufficient** if fewer than 30 CUT objects are labelled in the arm.
   No verdict.
1. **The cut buys precision** if the lower bound of D is above +0.05: kept
   objects are supported by more than five points over cut ones.
2. **The cut is too strict** if the upper bound of D is below +0.05 **and**
   the point precision of the CUT objects is at least 0.80: cut objects are
   mostly supported and not clearly worse than kept ones.
3. **Inconclusive** otherwise (the interval straddles +0.05, or cut
   objects are both not clearly worse than kept ones and below 0.80).

Overall: **too strict** if both arms say rule 2; **buys precision** if both
say rule 1; otherwise **mixed or inconclusive**, with the pattern described,
not decided. Per-extractor numbers (and the secondary runs) are reported with
their intervals and are not decisive.

Consequences, stated before the data:

- Too strict: #1579 step 2 proposes loosening the selection (drop only
  unsupported or duplicate candidates). That is a proposal and needs an ADR.
- Buys precision: the lift cost is recorded as the price of precision, and
  #1579 is closed with that record.
- Mixed or inconclusive: recorded as such. No default changes.

**Limits stated in advance.** One judge model with its own noise; one label
per object (no repeat labelling); a window-only source (an object whose
support lies in another window is read as unsupported); the strict and
lenient views can differ. Windows whose labels are unusable twice are
excluded; if more than 10% of windows are excluded, the run is reported as
degraded and not decided.

## Forecast

- Windows to label (windows with at least one windowed ceiling object, from
  the `self` seed-0 records): primary 154 (`qwen3:8b` 22, `qwen3.6:35b-a3b`
  28, `gemma4:26b-a4b` 34, `qwen3:14b` 37, `qwen3.6:27b` 33), secondary 62
  (`qwen3:8b` 26, `qwen3.6:27b` 36): **216 calls**.
- #1557 recorded G1 calls at 4.5 to 5.6 s. Ceiling batches are larger than
  final batches, so the forecast uses 8 s per call (upper estimate 16 s).
- 216 calls x 8 s = about 29 min, plus one model load of about 1 min:
  **about 30 min** (upper estimate about 1 h). `gpt-oss:20b` is the only
  model resident. This is well under the 3 h target, so the population is not
  reduced.
- If the first 20 calls average more than 25 s, the secondary population is
  dropped and the primary alone is run.
- Scoring is model-free.
