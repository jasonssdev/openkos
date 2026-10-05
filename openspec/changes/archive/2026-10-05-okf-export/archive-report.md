# Archive Report: okf-export

**Date archived:** 2026-10-05
**Issue:** #1301
**ADR:** ADR-0048, "OKF export withholds link labels into withheld objects and objects labelled below their sources", Accepted at archive
**Tasks:** all 36 checked; no pending or blocked items

## PR

Implementation merged in #1304 (commit b5715d65). All CI checks green:
- pytest 3.12, 3.13, 3.14: passed
- Coverage: 96.45%
- Evals self-tests: 56 of 56

## Delta specs merged into `openspec/specs/`

Each delta was composed with `sdd-archive-compose` (exit 0); the MODIFIED heading matched exactly in the living spec.

| Domain | Requirements before → after |
|---|---|
| okf-export | — → 9 (9 added, new domain) |
| workspace-lock | 11 → 11 (0 added, 1 modified: "Every Command Is Classified") |

## Verification

No verify-report was produced. Implementation completed and all tests pass per final-state facts.

## Archive Contents

✓ proposal.md: present
✓ design.md: present
✓ specs/: present (2 domains)
✓ tasks.md: present, 36/36 tasks complete, 0 unfinished

## Source of Truth Updated

The following specs now reflect the new behavior:
- `openspec/specs/okf-export/spec.md` (new)
- `openspec/specs/workspace-lock/spec.md` (MODIFIED)

## SDD Cycle Complete

The change is archived. Implementation: merged to main with all CI green. Verification: not run. ADR-0048 accepted at archive. No unfinished tasks or unresolved findings observed.
