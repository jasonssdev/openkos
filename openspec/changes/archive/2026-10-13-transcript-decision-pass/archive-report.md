# Archive Report: transcript-decision-pass

**Change**: transcript-decision-pass — an opt-in decision pass over meeting-shaped sources
**Archived**: 2026-10-13
**Archive Path**: `openspec/changes/archive/2026-10-13-transcript-decision-pass/`

## Summary

Implemented and merged in PR #1352 (core `extraction/decision_pass.py`), #1353
(config key `transcript_decisions` and wiring in `extract_concept_union`) and
#1356 (measurement harness) and #1358 (prompt revision and a
`MIN_VALUE_WORDS = 3` floor in `validate_item`); Refs #1348. No verify-report
was produced.

## Specs Synced

| Domain | Action | Requirement | Operation |
|--------|--------|-------------|-----------|
| extraction-union-judge | Updated | Transcript Decision Pass Is Opt-In And Default-Off | ADDED (appended) |
| extraction-union-judge | Updated | The Decision Pass Runs Per Window On Meeting-Shaped Sources | ADDED (appended) |
| extraction-union-judge | Updated | Pass Items Are Admitted Fail-Closed | ADDED (appended) |
| extraction-union-judge | Updated | A Pass Decision Body Is Composed Deterministically | ADDED (appended) |
| extraction-union-judge | Updated | Pass Decisions Replace The Main Pass's Decisions Fail-Closed | ADDED (appended) |

All five headings were confirmed absent from the main spec before appending. No
MODIFIED, REMOVED or RENAMED entries.

## Spec Corrections At Archive

Checked against `decision_pass.py` and `extract_concept_union`; the corrected
text is in both the main spec and the archived delta.

- Title: the delta said "bounded in length" and "description names the subject
  and the value". Code: title is the value without closing punctuation, at most
  `MAX_TITLE_CHARS = 100`, cut at a word boundary; description is exactly
  `<subject>: <value>`.
- Cap: the delta said "a fixed bound"; the value is `MAX_PASS_DECISIONS = 25`
  (an unmeasured placeholder in code, so the spec states it once, in the
  admission requirement).
- Replacement vs placement: pass Decisions DO replace the main pass's
  Decisions (they are filtered out of the merged set before the judge), and
  they are APPENDED to the retained set after the judge and the positional
  backstop, so the backstop never drops one. The delta omitted the placement;
  the requirement now states it. Replacement was not changed to "append
  alongside", which the code does not do.

## Merged From PR #1358

`MIN_VALUE_WORDS = 3` (a value of fewer than three words is dropped in
`validate_item`) is stated in "Pass Items Are Admitted Fail-Closed", with a
scenario. The revised prompt wording is not a spec requirement.

## Test Allowlist

`transcript_decisions` removed from `_PENDING_ARCHIVE` in
`tests/unit/test_config_keys_specified.py`; the living spec now names the key.

## Archive Contents

- proposal.md: present
- specs/: extraction-union-judge delta
- exploration.md, design.md, tasks.md: not produced by this change
