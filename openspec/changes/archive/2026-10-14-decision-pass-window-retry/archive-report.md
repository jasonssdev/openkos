# Archive Report: decision-pass-window-retry

**Change**: decision-pass-window-retry — ask a failed decision-pass window once more
**Archived**: 2026-10-14
**Archive Path**: `openspec/changes/archive/2026-10-14-decision-pass-window-retry/`

## Summary

Implemented and merged in PR #1362 (closes #1361): `run_decision_pass` retries a
window exactly once on an unparseable reply or a `BackendError` other than
`BackendUnavailable`, and reports a failure only when the retry also fails. The
registered measurement is recorded in `evals/transcript_decisions/RESULTS-1361.md`
(PR #1363). No verify-report was produced.

## Specs Synced

| Domain | Action | Requirement | Operation |
|--------|--------|-------------|-----------|
| extraction-union-judge | Updated | The Decision Pass Runs Per Window On Meeting-Shaped Sources | MODIFIED (replaced in place) |

The heading was confirmed present in the main spec before replacing; its two
existing scenarios are kept and three are added. No ADDED, REMOVED or RENAMED
entries.

## Checked Against Code

Each MUST in the modified requirement was checked against
`decision_pass.run_decision_pass` and `_ask_window` on `main`: `[]` and a parsed
reply with no verbatim item are not retried; `BackendUnavailable` is not retried
and is reported; the reported failure is the retry's cause.
