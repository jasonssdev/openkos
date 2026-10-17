# Pre-registration: #1364, the decision pass on an unseen development series (TS3009)

**Status: REGISTERED 2026-10-17, before any run on TS3009.** The rules below
are fixed for those runs. A change after this point is a new registration, not
an edit to this one. This file is merged to `main` before the first run, so its
timestamp is public.

## Question

Does the transcript decision pass (`transcript_decisions`) still recover
hand-annotated decisions, and stay within the other #1348 bars, on a
development series it was never tuned on?

## Scope amendment to #1364

#1364 named IS1004 "and TS3009 if available". Only TS3009 is adjudicated in the
thesis repository; IS1004 holds machine-extracted rows with a blank
`final_status` and the harness refuses it. This registration therefore covers
**TS3009 alone**. IS1004 remains a follow-up, to be registered separately once
adjudicated. A TS3009-only verdict is weaker evidence than two series; it is
stated as such in the results.

## Data

AMI TS3009 a-d (development series, `scoring.DEV_SERIES`; thesis ADR-0005),
gold `TS3009.decisions.adjudicated.csv`: 29 rows, 26 `decision`, 2 `compuesta`,
1 `no_decision`, no `sin_soporte`, no `descartar`, none blank. The data stays in
the thesis checkout and is read-only; nothing from it is copied here.

Two properties of the gold, known before any run and not tuned:

- **Unrecallable decision rows.** `TS3009b.d07` and the five split children of
  the two `compuesta` rows (`TS3009c.d07-1..3`, `d11-1..2`) are `decision`
  rows with an empty `evidence_text`, so no Decision can match them. They stay
  in the recall denominator, as the 4-token floor does in #1348 (the harness
  reports them as `unrecallable`). Recall is therefore capped at 20/26 = 0.769
  by construction. A secondary recall over the 20 recallable rows is REPORTED,
  never used for a bar.
- **Blank `meeting_id`.** The five split children have a blank `meeting_id`.
  Before this registration the harness would have read them as meeting `""`
  (refusing the file at the allowlist). The harness now derives the meeting
  from the `decision_id` prefix (`TS3009c.d07-1` -> `TS3009c`) and still refuses
  a blank it cannot derive. This is a harness fix with a self-test, made before
  any run; the CSV is not edited.

## Arms and frozen configuration

- `pass`: **15 runs**, `--arm pass`, key on (the decision pass alone, matched
  through its evidence line), including the #1361 one-retry per failed window.
- `baseline`: **15 runs**, `--arm baseline` (today's extraction, key off). The
  ES2015 baseline does not transfer, so it is re-run on TS3009.

Both arms: `qwen3:8b` (Ollama digest `500a1f067a9f`), decision-pass prompt hash
`6d3962b0dd75650c` (the one frozen in #1348 and #1361), baseline prompt hashes as
stamped in the stored runs, the harness's generation ceiling (8192) and context
window (12288), harness code at the registering PR's merge commit. No prompt,
cap or matching-rule change. The matching rule and scoring are those of
`README.md`. n = 15 is the floor on this harness; a smaller arm is not a result.

A run counts only if it completes. Completed runs are reported as n of 60
meeting-runs per arm; an arm with fewer than 15 complete runs is re-run, never
averaged in its incomplete form.

## Decision rule

The #1348/#1361 bars, unchanged. No bar is moved, including R: the fixed +0.25
floor is kept, because replacing it with a noise-based gap after seeing the
ES2015 results is exactly the post-hoc change the issue warns about, and a new
series is meant to be judged by the old yardstick.

- **R** `pass` mean recall >= `baseline` mean recall + 0.25, AND the gap exceeds
  the larger of the two arms' min-max spreads.
- **P** precision-on-annotated mean >= 0.60, no run below 0.50. TS3009 has one
  non-decision row and no `sin_soporte`, so P has little power here; a vacuous
  pass is reported as such (count the false positives the arm could have made).
- **V** at most 81 Decisions, mean and every run.
- **F** `failure` set in at most 3 of 60 meeting-runs, and zero uncaught
  errors.
- **D** at most 20% of Decisions would merge at ingest.
- **C** calls and wall time reported, both terms.

## What decides "default on"

The recommendation to flip `transcript_decisions` to on requires ALL of R, P,
V, F, D to pass on TS3009, AND the earlier ES2015 result not to contradict it
(#1361 missed R by 0.006; this run is the tiebreaker, not a replacement). Even
then the flip is a separate decision made by the maintainer with IS1004 still
pending; this measurement changes no default. If any bar fails, the key stays
off and the result is recorded as it is.

## Expectations, stated before the run

- Baseline recall is low, as on ES2015 (0.119), so R is decided by whether the
  pass reaches roughly 0.37 or more of 26 rows (about 10 rows).
- TS3009 is small (4 meetings, 29 rows, 6 unrecallable): a single gold row is
  0.038 of recall. Differences under that are noise by construction.
- This is one series; a pass is evidence, not proof of generalisation.
