# Proposal: decision-pass-window-retry — ask a failed decision-pass window once more

Refs #1361. Related: #1348 (the transcript decision pass).

## Intent

The #1348 registered run had 12 of 60 meeting-runs with a failed decision-pass
window (10 unparseable replies, 2 `BackendGenerationCapped`), against a bar of
at most 1 in 20. Sampling is non-deterministic, so asking the same window a
second time can succeed. The main extraction pass already does this (#1053).

## Scope

### In scope

- `run_decision_pass` asks a window exactly once more when its reply is
  unparseable or its call raises a `BackendError` other than
  `BackendUnavailable`. The failure is reported only when the retry also
  fails, and names the retry's cause.
- The evaluation harness counts real backend calls, so retries are visible.

### Out of scope

- Any prompt change; the `transcript_decisions` default stays `false`.
- Retrying `BackendUnavailable` (an unreachable backend will not answer a
  second time, the same exclusion as #1053), a valid `[]` reply, or a reply
  that parses but yields no verbatim item.
- The pre-flight call estimate: a retry happens only on failure, so the
  estimate stays the no-failure count, as it is for the main pass's retry.

## Domain

`extraction-union-judge`: MODIFIED requirement "The Decision Pass Runs Per
Window On Meeting-Shaped Sources".
