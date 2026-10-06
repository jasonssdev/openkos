# Archive Report: curate-auto-merge

**Date archived:** 2026-10-05
**Issue:** #1298
**ADR:** ADR-0049, "The structural base/-N Identity class merges without prior consent under measured constants, one commit per run", Accepted at archive
**Tasks:** all 99 tasks complete; 0 unfinished or blocked items

## Implementation

Merged to main in 4 squash PRs:
- #1306 (slice 1, commit `6794000b`): seam, constants, run eligibility, InstalledModel.digest, ADR-0049
- #1307 (slice 2, commit `d95be00e`): pass core, planner gates, apply with commit and restore
- #1308 (slice 3, commit `672b6b12`): CLI wiring, --auto-merge flag, ineligibility reporting, disclosure
- #1309 (slice 4, commit `38b2817b`): accept-recommended selector, TTY pre-pass, per-merge commits

Slice 5 (docs) is PR #1310, currently open; awaiting merge. All PRs had 9 CI checks green:
- pytest 3.12, 3.13, 3.14: passed
- Coverage: 96.51%
- Evals self-tests: 56 of 56 passed
- Ruff check and format: passed
- MyPy: passed

## Delta specs merged into `openspec/specs/`

Each delta was composed with `sdd-archive-compose` (exit 0); the MODIFIED headings matched exactly in the living specs. One NEW domain created.

| Domain | Requirements before → after | Action |
|---|---|---|
| curate-command | 26 → 28 | 2 added, 1 modified |
| entity-resolution-adjudication | 48 → 49 | 1 added, 1 modified |
| entity-resolution-merge | 21 → 22 | 1 added |
| identity-auto-merge | — → 12 | 12 added, new domain |
| job-runtime | 12 → 13 | 1 added |
| llm-client | 18 → 19 | 1 added |
| workspace-autocommit | 8 → 9 | 1 added |

Total: 7 domains, 133 → 152 requirements (19 added, 3 modified, 0 removed).

## Verification

No verify-report was produced. Implementation completed with all CI green and all tasks ticked per final-state facts. Rubric identity verified equal at measured commit `63b5f551` and at HEAD.

## Archive Contents

✓ proposal.md: present
✓ design.md: present
✓ exploration.md: present
✓ specs/: present (7 domains: curate-command, entity-resolution-adjudication, entity-resolution-merge, identity-auto-merge, job-runtime, llm-client, workspace-autocommit)
✓ tasks.md: present, 99/99 tasks complete, 0 unfinished

## Source of Truth Updated

The following specs now reflect the new behavior:
- `openspec/specs/curate-command/spec.md` (MODIFIED: 2 added, 1 modified)
- `openspec/specs/entity-resolution-adjudication/spec.md` (MODIFIED: 1 added, 1 modified)
- `openspec/specs/entity-resolution-merge/spec.md` (MODIFIED: 1 added)
- `openspec/specs/identity-auto-merge/spec.md` (NEW: 12 requirements)
- `openspec/specs/job-runtime/spec.md` (MODIFIED: 1 added)
- `openspec/specs/llm-client/spec.md` (MODIFIED: 1 added)
- `openspec/specs/workspace-autocommit/spec.md` (MODIFIED: 1 added)

## ADR Accepted

ADR-0049, "The structural base/-N Identity class merges without prior consent under measured constants, one commit per run", is accepted and in effect. Status updated in:
- `docs/adr/0049-structural-identity-class-merges-under-measured-constants.md` (frontmatter and body)
- `docs/adr/README.md` (index row)

## SDD Cycle Complete

The change is archived. Implementation: merged to main in 4 squash PRs, all CI green. Docs PR #1310 is open and pending merge. Verification: not run. ADR-0049 accepted. No unfinished tasks or unresolved findings observed.

The work implements the opt-in `curate --auto-merge` command for the measured structural Identity class (base/-N pairs), with automatic merging under fixed constants and fresh verdicts only, one commit per run with per-merge log entries for exact undo via `openkos unmerge`. Identity also offers an `accept-recommended` answer on its own measured bars, with per-merge commits. The measured class is ineligible when the model, context window, max generation tokens, sampling pins, or rubric digest differ from the pre-registered measurement. Never available unattended or in the daemon.
