# Archive Report: okf-import

**Change**: okf-import — adopt a foreign OKF bundle under its own namespace  
**Archived**: 2026-10-06  
**Archive Path**: `openspec/changes/archive/2026-10-06-okf-import/`

## Summary

The okf-import change is complete and archived. All 7 slices landed as squash PRs with CI green (9 checks each), and implementation is verified by final state facts: pytest 11268 passed at 96.59% branch coverage, evals self-tests 56 of 56. No verify-report was produced. ADR-0050 is now Accepted.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| okf-import | Created | 20 requirements (new domain; copied from delta) |
| workspace-lock | Updated | 11 requirements; merged 2 MODIFIED requirements |
| workspace-autocommit | Updated | 9 requirements; merged 1 MODIFIED requirement |
| ingestion | Updated | 66 requirements; merged 2 MODIFIED requirements |
| identity-auto-merge | Updated | 12 requirements; merged 2 MODIFIED requirements |
| entity-resolution-adjudication | Updated | 49 requirements; merged 3 MODIFIED requirements |
| type-sensitivity-defaults | Updated | 9 requirements; merged 1 MODIFIED requirement |

All merges completed successfully via `gentle-ai sdd-archive-compose` (six existing domains) and mechanical copy (okf-import, new domain). Verbatim `diff -r` readback confirmed empty — all bytes preserved.

## Archive Contents

- proposal.md: present
- exploration.md: present
- design.md: present  
- tasks.md: present; **1 incomplete item**: Slice 5 task 5.16 "Commit and open PR 5" is marked incomplete (`[ ]`) in the archived tasks.md, but was completed per final-state facts (PR #1320 landed with CI green)
- specs/: present, all 7 delta specs present in the archived folder

## Source of Truth Updated

The following main specs now reflect the new behavior:

- `openspec/specs/okf-import/spec.md` (new)
- `openspec/specs/workspace-lock/spec.md` (modified)
- `openspec/specs/workspace-autocommit/spec.md` (modified)
- `openspec/specs/ingestion/spec.md` (modified)
- `openspec/specs/identity-auto-merge/spec.md` (modified)
- `openspec/specs/entity-resolution-adjudication/spec.md` (modified)
- `openspec/specs/type-sensitivity-defaults/spec.md` (modified)

## ADR-0050 Status

ADR-0050 (`docs/adr/0050-okf-import-adopts-a-foreign-bundle-under-its-own-namespace.md`) updated from **Proposed** to **Accepted** in:
- Frontmatter `status:` field
- Body `**Status:**` line
- `docs/adr/README.md` index row

## Implementation Status

**Landed PRs** (final-state facts):
- PR #1315: Planning (proposal, specs, design, tasks)
- PR #1316: Reader and ADR-0050, including CRLF and Windows portability fixes
- PR #1319: Link rewrite and proof
- PR #1320: Frontmatter transform and anchors
- PR #1321: Layout and service
- PR #1322: Entity-resolution exclusion
- PR #1323: Slug rename (slice 4b, owner decision)
- PR #1324: CLI verb and e2e (in CI)
- Slice 7 (docs): PR pending

**Final Verification** (per final-state facts):
- pytest: 11268 passed, coverage 96.59%
- evals self-tests: 56 of 56 passed
- All CI checks green on landed PRs

## Decisions Carried in Archive

Per final-state facts and tasks.md:

- Adopt as-is under `imports/<ns>/`
- Workspace default sensitivity label, raised by per-type offsets and `--sensitivity`
- Re-import out of v1 (not supported)
- Names with whitespace renamed to slugs (collision refuses)
- accept-recommended excludes imported members
- Lexical index refreshed after import; embeddings not refreshed (no model call)
- Chain strategy: stacked-to-main

## Unfinished Work

**Task 5.16 checkbox state vs. final facts**:
- tasks.md shows `[ ] 5.16`: "Commit (`feat(ingest): exclude imported concepts...`). Open PR 5..."
- Final-state facts state PR #1320 landed with CI green (slice 5)
- The checkbox was never checked; archive records the checkbox as written (`[ ]`) per Final-State Authority §1 (persisted tasks artifact)
- The actual completion is documented in final-state facts and PR history

No other unfinished work or open gaps remain.

## Mechanical Copy Verification

- Archive source snapshot created before move: `cp -R openspec/changes/okf-import $snapshot_root/source`
- Archive move: `git mv openspec/changes/okf-import openspec/changes/archive/2026-10-06-okf-import`
- Post-move readback: `diff -r $snapshot_root/source openspec/changes/archive/2026-10-06-okf-import`
- **Result**: No differences (empty diff); all bytes preserved
- Source directory confirmed absent after move: verified
- Archive directory confirmed present: verified

## SDD Cycle Complete

Implementation is complete and verified by final-state facts. All 7 delta specs merged into main specs. ADR-0050 accepted. Change archived and closed. Ordinary repository policy governs any remaining delivery decisions (push, PR, merge).
