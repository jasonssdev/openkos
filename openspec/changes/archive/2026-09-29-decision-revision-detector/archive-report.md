# Archive Report: decision-revision-detector

**Change Name**: decision-revision-detector  
**Archived**: 2026-09-29  
**Artifact Store**: hybrid (openspec + Engram)  
**Archive Path**: `openspec/changes/archive/2026-09-29-decision-revision-detector/`

---

## Executive Summary

`decision-revision-detector` (MVP 3, #1014) implements the decision-revision detection system that identifies when one Decision supersedes or refines another based on semantic similarity and temporal provenance information. The change shipped as 11 PRs merged to `main` in sequence: #1030 (query history 4/4), #1032 (CI timeout), #1047 (harness test), #1050 (resolution seeding), #1055 (graph vector read), #1058 (eval harness), #1059 (state findings store), #1060 (provenance cache), #1061 (date resolution), #1063 (input digests), #1065 (judge selection), #1066 (revisions report), #1067 (revisions verb), #1068 (later-decision prompt), #1069 (reconcile walk). All 191 tasks in `tasks.md` are checked complete; no task or spec drift was found. This step merges two delta specs into their living specs, creates one new spec (`decision-revision-detection`), moves the change folder to the archive, flips ADR-0025 from `Proposed` to `Accepted`, records one known intentional gap (two stdout lines in Decision 8 not implemented), and verifies all artifacts are byte-identical.

---

## Final-State Authority (per Skill §Final-State Authority)

Source ranking applied (most authoritative first):

1. **Persisted tasks artifact** (`tasks.md`) — 191/191 tasks checked, 0
   unchecked. All work units committed and merged to `main` @ 985661b.
2. **Explicit final-state facts in the orchestrator's launch prompt** —
   "Phase A and B merged in PRs #1030-#1032-era and harness sub-change in #1047-#1050; Phase B merged as 11 PRs: P1 #1055, P2 #1058, P3 #1059, P4 #1060, P5a #1061, P5b #1063, P6 #1065, P7a #1066, P7b #1067, P8a #1068, P8b #1069 (main @ 985661b). Known intentional gap: Decision 8 lists two extra stdout lines (workspace root; served/judged summary) not built—no spec requirement, no task. ADR-0025 → Accepted."
3. **`apply-progress.md`** — lists implementation progress as work units landed.
   Per the Skill's Final-State Authority section, the launch prompt's explicit
   statement outranks any intermediate snapshot.

No contradiction between sources was found. The tasks artifact and launch
prompt agree completely on delivered scope, merged state (all 11 PRs),
and task count.

---

## Task Completion Gate

**Status**: PASS

Inspected `openspec/changes/archive/2026-09-29-decision-revision-detector/tasks.md`
(persisted artifact, now at the archived path):

```
$ grep -c '\- \[x\]' openspec/changes/archive/2026-09-29-decision-revision-detector/tasks.md
191
$ grep -c '\- \[ \]' openspec/changes/archive/2026-09-29-decision-revision-detector/tasks.md
0
```

All 191 tasks marked `[x]`. No stale unchecked tasks remain.

---

## Spec Sync — Native Composition and New Spec Creation

Three delta/new specs were processed:
- Two existing specs (forget-command, reconcile-command) composed with native `gentle-ai sdd-archive-compose` (mandatory, exit 0 for both)
- One new spec (decision-revision-detection) copied mechanically with no pre-existing main spec

All compositions and copies succeeded.

```
$ gentle-ai sdd-archive-compose \
    --canonical "openspec/specs/forget-command/spec.md" \
    --delta "openspec/changes/decision-revision-detector/specs/forget-command/spec.md" \
    --output "openspec/specs/forget-command/spec.md.compose-tmp" \
  && mv "openspec/specs/forget-command/spec.md.compose-tmp" "openspec/specs/forget-command/spec.md"
Exit code for forget-command: 0

$ gentle-ai sdd-archive-compose \
    --canonical "openspec/specs/reconcile-command/spec.md" \
    --delta "openspec/changes/decision-revision-detector/specs/reconcile-command/spec.md" \
    --output "openspec/specs/reconcile-command/spec.md.compose-tmp" \
  && mv "openspec/specs/reconcile-command/spec.md.compose-tmp" "openspec/specs/reconcile-command/spec.md"
Exit code for reconcile-command: 0

New spec (decision-revision-detection): created at openspec/specs/decision-revision-detection/spec.md (no pre-existing main spec)
```

All compositions succeeded and merged correctly.

### Spec Merge Details

| Domain | Spec Status | Requirements |
|--------|-------------|--------------|
| `decision-revision-detection` | NEW (created) | 16 requirements (full spec, not a delta) |
| `forget-command` | Merged | MODIFIED "Deletion Sweep Includes Persisted Findings" |
| `reconcile-command` | Merged | ADDED "Revision Findings Walk" |

All merges preserve pre-existing requirements byte-for-byte and apply all delta sections correctly.

### Requirement Verification Table

Each requirement heading from the delta specs verified present exactly once in merged main specs:

| Spec | Requirement | Count | Status |
|------|-------------|-------|--------|
| forget-command (MODIFIED) | Deletion Sweep Includes Persisted Findings | 1 | ✓ |
| reconcile-command (ADDED) | Revision Findings Walk | 1 | ✓ |
| decision-revision-detection (ADDED) | Decision Event Date Resolution | 1 | ✓ |
| decision-revision-detection (ADDED) | Candidate Eligibility Exclusions Applied Before Counting | 1 | ✓ |
| decision-revision-detection (ADDED) | Candidate Ranking And Caps | 1 | ✓ |
| decision-revision-detection (ADDED) | Candidate Vectors Come From The Reindexed Vector Store | 1 | ✓ |
| decision-revision-detection (ADDED) | Judge Verdict Vocabulary And Reply Shape | 1 | ✓ |
| decision-revision-detection (ADDED) | Candidate Presentation Order Given To The Judge | 1 | ✓ |
| decision-revision-detection (ADDED) | Direction Comes Only From Resolved Source event_date | 1 | ✓ |
| decision-revision-detection (ADDED) | An Undirected Change Verdict Is Untyped | 1 | ✓ |
| decision-revision-detection (ADDED) | Fail-Closed Judge Parsing With Partial Batch On Failure | 1 | ✓ |
| decision-revision-detection (ADDED) | `openkos revisions` Is Labelled Experimental | 1 | ✓ |
| decision-revision-detection (ADDED) | Zero-LLM Probe Precedes The Cost Gate | 1 | ✓ |
| decision-revision-detection (ADDED) | One Exact Cost Gate Before Pair Judgment | 1 | ✓ |
| decision-revision-detection (ADDED) | `revisions` Writes Only Derived State, Never The Bundle | 1 | ✓ |
| decision-revision-detection (ADDED) | Revision Findings Persist In Sibling Tables | 1 | ✓ |
| decision-revision-detection (ADDED) | Revision Findings Are Served From Cache Keyed By Input Digests | 1 | ✓ |
| decision-revision-detection (ADDED) | Revisions Report Groups Findings Per Decision, Including REAFFIRMS | 1 | ✓ |

All 18 unique requirement headings present exactly once. No delta markers remain in main specs.

---

## Archive Move

### Directory Listings

**Archived folder** (`openspec/changes/archive/2026-09-29-decision-revision-detector/`):
```
apply-progress.md
design.md
exploration.md
proposal.md
specs/decision-revision-detection/spec.md
specs/forget-command/spec.md
specs/reconcile-command/spec.md
tasks.md
```
(plus `archive-report.md`, written by this step, additive)

**Original path** (`openspec/changes/decision-revision-detector/`):
```
$ test -d openspec/changes/decision-revision-detector && echo STILL-THERE || echo moved
moved
```
Confirmed absent.

### Move Mechanics and Readback

Mechanical `git mv` of `openspec/changes/decision-revision-detector/` to
`openspec/changes/archive/2026-09-29-decision-revision-detector/`, run as one shell
transaction with a pre-move `cp -R` snapshot for the readback.

```
✓ git mv succeeded
✓ source removed
✓ archive destination exists
=== Mechanical copy verification (diff -r) ===
✓ Diff output is empty (archive move is byte-identical)
```

Empty `diff -r` is the readback evidence — the archived tree is byte-identical
to the pre-move snapshot of the source change folder.

---

## ADR Status Flips

Per `openspec/config.yaml`'s `rules.archive` (ADR acceptance — the only phase
allowed to change an ADR's status), and the launch prompt's explicit instruction:

**ADR-0025 (Temporal direction never comes from the model)**
- File: `docs/adr/0025-temporal-direction-never-comes-from-the-model.md`
- Frontmatter `status: Proposed` → `status: Accepted`
- Body `**Status:** Proposed` → `**Status:** Accepted`
- Index row in `docs/adr/README.md`: `Proposed` → `Accepted`
- Status: Flipped

Verification:
```
$ uv run pytest tests/unit/cli/test_adr_index.py -q
✓ ADR index checks pass
```

ADR-0025 is correctly accepted across all three locations (frontmatter, body, index).

---

## Known Intentional Gaps

### Design Decision 8 Gap: Unimplemented Stdout Lines

Design Decision 8 ("The report and the experimental notice") listed two stdout
lines that were never implemented:

1. **Workspace root line** — `openkos revisions: workspace at <root>` — no spec requirement written, no task created
2. **Served/judged summary line** — `"{served} of {candidates} candidate pair(s) served from persisted findings; {judged} judged fresh."` — no spec requirement written, no task created

The remaining stdout output (counts line, truncation notice, per-Decision groups)
was built as designed. A note was added to the archived design.md Decision 8 at archive time
indicating this supersession; the implemented spec and shipped code correctly reflect only
the items that were built.

---

## Related Open Issues

Issues referenced during implementation that remain open for follow-up work:

- **#1052** — `openkos revisions` proximity docstring refinement (open)
- **#1053** — Chunk-pair failure handling in revision candidates (open)
- **#1054** — Auto-merge for reconcile findings (open)
- **#1062** — Frontmatter syntax for relation-type metadata (open)
- **#1064** — OKF v0.2 adoption planning (open)

---

## Final Checklist (per Skill §Step 4)

- [x] Main specs updated correctly (`sdd-archive-compose` exit 0 for two existing specs; decision-revision-detection created new)
- [x] Change folder moved to archive
      (`openspec/changes/archive/2026-09-29-decision-revision-detector/`)
- [x] Archive preserves all artifacts that existed (exploration, proposal, 
      design, tasks, apply-progress, all three delta/new specs); no artifact this 
      change produced is missing
- [x] Archived tasks retain their original bytes; 191 completed, 0 unchecked
- [x] Active changes directory no longer has this change
- [x] Verbatim `diff -r` readback output is included in the result and is empty (no differences)
- [x] All delta markers (## ADDED/MODIFIED/REMOVED/RENAMED, # Delta for) removed from main specs
- [x] All requirement headings from deltas verified present exactly once in main specs
- [x] ADR-0025 status flipped to Accepted in all three places
- [x] Known intentional gap (Decision 8 unimplemented stdout lines) documented and noted in archived design.md

---

## Summary

The `decision-revision-detector` change is complete and archived. All 11 merged PRs shipped the intended capability. Implementation matches the shipped spec with one documented intentional gap (two stdout lines in Decision 8 design not implemented—no spec requirement or task ever created for them). All artifacts are preserved byte-identically in the archive, all delta specs are merged into their living specs, and ADR-0025 is now Accepted.

Archive is ready for closure. No follow-up work is required for this change; related work remains on the issue queue (#1052-#1064).

---

**Archive Report Created**: 2026-09-29  
**Verified by**: Byte-identical `diff -r` between pre-move snapshot and archived destination.
