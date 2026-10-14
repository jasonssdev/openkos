# Pre-registration: #1361, retrying a failed decision-pass window once

**Status: REGISTERED 2026-10-14, before the registered run.** The rules below
are fixed for that run. A change after this point is a new registration, not an
edit to this one.

## Question

Does asking a failed decision-pass window once more bring the window failure
rate under bar F, without breaking the other #1348 bars?

## What changed since #1348

Only `decision_pass.run_decision_pass`: a window whose reply is unparseable, or
whose call raises a `BackendError` other than `BackendUnavailable`, is asked
exactly once more; `failure` is set only when the retry also fails. Prompt,
`MIN_VALUE_WORDS`, `MAX_PASS_DECISIONS`, model, matching rule and scoring are
unchanged.

## Data and arms

AMI ES2015 a-d only (development series), the same adjudicated CSV as #1348.

- `pass`: re-run, **15 runs**, with the retry.
- `baseline`: **reused** from the #1348 registered run (`RESULTS-1348.md`:
  recall 0.119, min-max 0.000-0.222, 15 runs), not re-run. This change does not
  touch the key-off path, so the baseline arm executes the same code.

Frozen configuration: `qwen3:8b`, prompt hash `6d3962b0dd75650c`, the harness's
generation ceiling and context window, as in #1348.

## Decision rule

The #1348 bars, unchanged:

- **R** pass mean recall >= baseline mean + 0.25, and the gap exceeds the larger
  of the two arms' min-max spreads. With the reused baseline this means pass
  mean recall >= 0.369, and a gap greater than both 0.222 and the pass arm's own
  spread.
- **P** precision-on-annotated mean >= 0.60, no run below 0.50.
- **V** at most 81 Decisions, mean and every run.
- **F** `failure` set in at most 1 of 20 meeting-runs (at most 3 of 60), and
  zero uncaught errors.
- **D** at most 20% of Decisions would merge at ingest.
- **C** calls and wall time reported; the calls now include retries.

Adopt (flip `transcript_decisions` to on) only if ALL hold. Anything short of
that records the result and ships nothing beyond the retry itself, which is
behind the off-by-default key. Bar F alone passing is reported as fixing F, not
as adoption.

Expectation, stated before the run: R is unlikely to pass. #1348 missed it by
0.058 (0.311 against 0.369), and recovered windows add only what one window
yields.
