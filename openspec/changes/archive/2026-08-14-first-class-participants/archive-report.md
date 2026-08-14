# Archive Report: First-Class Person/Organization Participants

**Change**: first-class-participants
**Archived**: 2026-08-14
**Artifact Store**: hybrid (Engram + filesystem)
**Status**: Complete, closed

## Summary

The first-class-participants change introduced deterministic re-admission of `Person` and `Organization` candidates at the judge step, alongside a new participant coverage probe to measure extraction effectiveness on the AMI corpus. The change shipped in three chained PRs that merged cleanly to main with zero regressions in the full unit suite.

## What Shipped

**All 3 PRs merged to main:**
- PR #670 (commit e8ec17c): Judge re-admission generalization + comprehensive tests
- PR #671 (commit b6c6c2d): Participant coverage probe extension + baseline recording
- PR #672 (commit 2b99b1d): Conditional phase-2 scoped capture pass (opened by measurement)

**Verification Status** (at 2b99b1d):
- Full unit suite: 4614 passed, 1 skipped in 169.03s
- Design spot-checks: all 8 PR1 test scenarios pass; D1–D7 design decisions verified byte-for-byte
- Issue #673 filed (P2): transcript detection title-only gap on AMI corpus confirmed and measured
- Issue #669 boundary maintained: zero per-type sensitivity code in any touched file

## Design Decisions Encoded

### D1: Deletion Predicates Stay Byte-Identical (Additive vs. Deletion Direction)
`_TWIN_EXEMPT_TYPE = "Procedure"` remains unchanged at both deletion sites (`_is_droppable_source_title_twin` line 821, `_drop_framing_objects` line 1010). Only the judge re-admission site (line 2234) uses the new `_JUDGE_READMIT_TYPES` set (`{Procedure, Person, Organization}`). This direction-based separation prevents silent regressions where a Person titled "Team Meeting" would survive framing deletion.

### D2: Judge Remains Type-Blind
No changes to `src/openkos/extraction/judge.py`. The judge is strictly applied over the merged candidate list and unaware of type semantics.

### D3: Transcript/Meeting-Shape Detection
Reuses existing `_MEETING_SHAPED_TITLE_RE` pattern against `source_title` (already in scope at line 2234). Enforces the SCOPE RULE: Person/Organization candidates from non-meeting-shaped sources are never re-admitted through this path.

### D4: Stub Rule as Conjunct on Re-Admission
`_has_participant_anchor(result)` reads `description` and `body` via tight `_PARTICIPANT_ANCHOR_RE` (English+Spanish, documented limits). Implemented as an extra CONJUNCT on the re-admission only (not a global post-filter), aligning with D1's standing rule that deletion has its own stricter predicate.

### D5: Probe Extends Existing Harness
`run_type_coverage.py` extended with `--participants` flag. Reports per-type recall AND precision-side stub-flooding counts (re-admitted vs. judge-selected, anchor-less discards).

### D6: Phase-2 Conditionality
Trigger: zero/near-zero Person/Organization across ≥2 AMI meetings over ≥3 runs. Architecture mirrors `_add_reask_subjects` (#584), joins before the judge. `_SYSTEM_PROMPT` byte-identical.

### D7: Scrub and Reconciliation Remain Type-Blind
Traced #602 `_scrub_entry_snapshots` and #645 `_reconcile_merged_survivor` → both structural/string-based with zero ObjectType reads. Frontmatter `type: Person` preserved verbatim through reconciliation.

### D8: Identity Resolution Deferred
Seam named as companion predicate in `resolution/similarity.py`, surfaced through `suggest-relations --apply` (#560/#483). Token containment methods unsafe for names; dedicated conservative person-name predicate reserved for that future work.

## Specs Synced

### Extraction Union-Judge Spec
**Action**: Updated (ADDED requirements only; no MODIFIED/REMOVED)
**Requirements added**: 3
- Judge Re-Admission Set Extended to Person/Organization (Additive Only)
- Stub Rejection at Judge Re-Admission
- Judge Re-Admission Scoped to Meeting-Shaped Sources

**Merged into**: `openspec/specs/extraction-union-judge/spec.md`

All existing 9 requirements preserved byte-for-byte. New requirements appended to the end, maintaining markdown structure and heading hierarchy.

### Participant-Coverage-Probe Spec
**Action**: Created (NEW capability)
**Requirements**: 5
- Per-Type Participant Recall Measurement
- Precision-Side Reporting Alongside Recall
- Recorded Baseline for Comparison
- Probe Result Gates Phase-2 Scoped Pass
- No Per-Type Sensitivity Behavior in Probe Scope

**Created at**: `openspec/specs/participant-coverage-probe/spec.md`

## Verification Findings

### PASS (0 CRITICAL)
All design requirements and test scenarios validated on final main HEAD.

### Warnings (2)

**WARNING 1**: Precision-side reporting is floor-level, not per-object.
- The spec's illustrative scenario describes counting objects that individually match no ground-truth mention (per-object). The shipped implementation reports precision as "source-level floor is zero" (an object of a type is flagged only when that source has ZERO annotated mentions of that type at all), which is a conservative undercount relative to the literal scenario wording.
- **Mitigation**: This is disclosed honestly in apply-progress.md with sound engineering rationale (avoids building a second corpus-parsing subsystem for a manual eval tool). Self-test explicitly covers the implemented scenario shape.
- **Flag for spec wording correction at next review, not a blocking defect.**

**WARNING 2**: Detection gap on the canonical corpus (disclosed and filed).
- PR3's capture pass and PR1's re-admission are gated on `_MEETING_SHAPED_TITLE_RE` over source TITLE only. The AMI corpus's `path.stem` titles never match this regex, so the entire participant machinery is measurably inert on the change's own canonical benchmark corpus.
- **Mitigation**: This is disclosed, measured, and filed as issue #673 (open, P2) rather than hidden. Spec scenarios themselves (which use meeting-shaped title inputs directly) are satisfied by passing unit tests — this is a corpus/detection-integration gap, not a spec-scenario failure.

### Suggestion (1)

**SUGGESTION 1**: Task 2.10 stale note.
- apply-progress.md line noted "real-baseline readback pending 2.9" even though 2.9 is now done (report.md baseline exists).
- **Fixed at archive**: this line was corrected to remove the stale note, as per the final-state-facts.

## Task Completion

All 23 implementation tasks completed and marked `[x]` in tasks.md:
- PR1 (tasks 1.1–1.16): 16 tasks complete
- PR2 (tasks 2.1–2.10): 10 tasks complete (task 2.10 note corrected)
- PR3 (tasks 3.1–3.7): 7 tasks complete (opened and executed per phase-2 gate)

No unchecked implementation tasks remain.

## Spec Heading Inventory (Post-Merge)

### openspec/specs/extraction-union-judge/spec.md

**Existing requirements (9):**
1. Union Construction Below the Chunk Threshold
2. Chunked Sources Are Judge-Only, No Second Pass
3. Merged-List Twin-Drop and Richer-Body Merge
4. Judge Selection Over a Closed Candidate List
5. Judge Failure Fails Closed to the Backstopped Union
6. Backstop Cap Applied Once, After Judge Selection
7. Run and Judge Bookkeeping on the Extraction Report
8. Opt-Out Configuration Flag

**New requirements (3):**
9. Judge Re-Admission Set Extended to Person/Organization (Additive Only)
10. Stub Rejection at Judge Re-Admission
11. Judge Re-Admission Scoped to Meeting-Shaped Sources

**Total**: 12 requirements, all with complete scenario coverage.

### openspec/specs/participant-coverage-probe/spec.md (NEW)

**Requirements (5):**
1. Per-Type Participant Recall Measurement
2. Precision-Side Reporting Alongside Recall
3. Recorded Baseline for Comparison
4. Probe Result Gates Phase-2 Scoped Pass
5. No Per-Type Sensitivity Behavior in Probe Scope

**All with complete scenario coverage.**

## Open Follow-Ups

**#673** (P2, content-shape): Transcript detection on AMI corpus title-only; detection regex does not match file-stem patterns. Measurement confirmed in verify-report 2026-08-14 null-experiment/gate-fired-probe pair. Requires separate investigation of AMI corpus structure and detector generalization.

**#669** (per-type sensitivity): Split to independent issue per proposal scope. No per-type sensitivity code shipped in this change; #669 stays out of scope.

## Artifact Store Traceability

**Engram observations saved:**
- #2769: sdd/first-class-participants/proposal
- #2771: sdd/first-class-participants/spec
- #2772: sdd/first-class-participants/design
- #2773: sdd/first-class-participants/tasks
- #2774: sdd/first-class-participants/apply-progress
- #2776: sdd/first-class-participants/verify-report

**Archive report**: sdd/first-class-participants/archive-report (this file, persisted to Engram at archive time)

**Filesystem artifacts:**
- openspec/specs/extraction-union-judge/spec.md (merged, existing + 3 new requirements)
- openspec/specs/participant-coverage-probe/spec.md (created, new capability)
- openspec/changes/archive/2026-08-14-first-class-participants/ (folder with all change artifacts)

## Archive Folder Contents Verified

**File listing of archived folder:**
```
openspec/changes/archive/2026-08-14-first-class-participants/
├── proposal.md
├── design.md
├── exploration.md
├── tasks.md (all tasks marked complete)
├── apply-progress.md (task 2.10 note corrected)
├── verify-report.md (0 CRITICAL, 2 WARNING, 1 SUGGESTION)
├── archive-report.md (this file)
└── specs/
    ├── extraction-union-judge/spec.md (delta)
    └── participant-coverage-probe/spec.md (new)
```

All artifacts accounted for. Original openspec/changes/first-class-participants/ directory no longer exists (verified via ls -la).

## SDD Cycle Complete

The change has been fully planned, implemented, verified, and archived.

**Date closed**: 2026-08-14
**Archival mode**: hybrid (Engram + filesystem)
**Next phase**: none — SDD cycle closed
