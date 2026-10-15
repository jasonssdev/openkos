# Archive Report: curate-decisions-as-data

**Archived**: 2026-10-14 (slice S5, PR 10 of 10)
**Archive folder**: `openspec/changes/archive/2026-10-14-curate-decisions-as-data/`
**Store mode**: hybrid (Engram report plus filesystem merge and move)

## Artifacts Read

- Engram observations read: #3549 (`sdd/curate-decisions-as-data/verify-report`), a condensed summary. Its text says "Full report returned to orchestrator"; the full report is not persisted. #3547 (`sdd/curate-decisions-as-data/s5-verify-notes`), a spec overstatement finding.
- Files read: `proposal.md`, `design.md`, `tasks.md`, `exploration.md`, and the delta specs under `specs/`. These files are authoritative over Engram copies.
- Other Engram IDs exist for this change (#3533 explore, #3535 proposal, #3536 spec, #3537 design, #3538 tasks, #3539 tasks-part2, #3540 apply-progress, #3543 merge-authorization, #3534 scope-decisions). They were not read in this phase.

## Verification Status (per verify-report, at verification time)

- Verdict: PASS-WITH-CORRECTIONS. 0 CRITICAL, 3 WARNING, 4 SUGGESTION. No code defect.
- Verify-time results: 521 targeted tests passed; `ruff check`, `ruff format --check` and `mypy .` clean.
- The full verify report is in `verify-report.md`. The archive agent first wrote it from a condensed Engram summary (#3549). The orchestrator then replaced it with the full report.

## Corrections C1-C8: Applied

- **Who applied them.** The archive agent could not apply them, because only the condensed summary of the verify report had been persisted. The orchestrator applied all eight from the full report afterwards, in a follow-up commit.
- **How they were matched.** Each old sentence was matched exactly once, ignoring whitespace. The corrections went to both `openspec/specs/curate-decisions/spec.md` and the archived delta, and the two files remain byte-identical.
- **What changed.** Spec text only. No requirement heading was renamed, there are still 16 requirements, and no code changed.
- **The --auto-merge sentence.** The overstated sentence about `--auto-merge` writing through the identity merge action is gone from `openspec/specs/`. `rg` finds no match.
- **What each correction covers:**
  - C1: `supersedes` relation rows carry no action.
  - C2: the merge-withholding conditions.
  - C3: `fresh` is `None` for action-less kinds.
  - C4: the two-half token re-validation, and a row-level Stale that can arrive after a planned reconciliation.
  - C5: the volatility token covers the bytes of `openkos.yaml`.
  - C6: keep-distinct failure is fail-open, and a tier refusal is raised.
  - C7: `--auto-merge` keeps `apply_auto_merges`.
  - C8: the per-item `asymmetric` exemption, and the unattended-identity consent input.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| curate-decisions | Created | `openspec/specs/curate-decisions/spec.md`: 16 requirements, copied mechanically from the delta (no prior main spec). |
| curate-command | Updated | 4 MODIFIED requirements replaced in place by `gentle-ai sdd-archive-compose` (exit 0): Identity Stage Reuses Merge Cores; Structure Stage Writes Through The Relate Core; Metadata Stage Writes Tiers, Reports Sensitivity; Curate Reads And Resolves The Pending Queue. 29 requirements total, unchanged count. Six consent requirements verified byte-identical. |

- `pending-work` spec: no change needed (S5.3).
- One blank line inserted before "### Requirement: Identity Prompt Separates Skip From A Keep-Distinct Ruling". The merge had dropped it. No other lines changed.

## Open Findings

- None. Both findings that were open at first archive are resolved by C7 (`--auto-merge` keeps `apply_auto_merges`) and C8 (the per-item exemption). S5.1 and S5.4 were done by the verify phase (`verify-report.md`), which ran a two-way requirement-by-requirement check against the shipped code.

## Follow-ups (not in this change)

1. "Judging as an application service": the required next MVP 6 stage, before the desktop review screen (`docs/roadmap.md:230`; ADR-0053 line 78).
2. Recording Structure and Metadata declines.
3. Relate-sequence duplication between `curate` and `suggest-relations --apply`.
4. A non-printing commit adapter for a future API caller.

## Other Closure Actions

- ADR-0053 status changed to Accepted in three places: frontmatter `status`, the `**Status:**` line, and `docs/adr/README.md`. The ADR status consistency test (`tests/unit/test_adr_index.py`) passes: 219 tests.
- `docs/architecture.md`: unchanged. Line 66 is a generic `application/` listing that names no services.

## tasks.md

- All 105 tasks are checked, S5.1–S5.9 included. The orchestrator ticked S5 after the verify, the corrections and this archive were observed. Evidence: `verify-report.md`, the `rg`/`ls`/`diff` checks above, and the gates in the archive commit.
