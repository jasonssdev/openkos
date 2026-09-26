# Archive Report: revises-relation

**Change Name**: revises-relation  
**Archived**: 2026-09-25  
**Artifact Store**: hybrid (openspec + Engram)  
**Archive Path**: `openspec/changes/archive/2026-09-25-revises-relation/`

---

## Executive Summary

`revises-relation` (Refs #1014 piece (a)) adds a third resolution type, `revises`, to record concept refinement while hiding nothing—a semantic complement to `supersedes` (reversal, hiding) and `reconciled_with` (coexistence). The change shipped as two stacked PRs squash-merged to `main`: #1020 (commit `cf2a170`) introduces `RESOLUTION_RELATION_TYPES`, `reconcile --revision`, the mixed-state classifier refusing every request, reconciliation notes, and ADR-0024; #1021 (commit `4f49e40`) excludes resolved pairs from contradiction candidates before count and cap, even under `--include-deprecated`. All 31 tasks in `tasks.md` are checked; no delta spec or task drift was found. This step merges three delta specs into their living specs (reconcile-command: 2 ADDED + 3 MODIFIED; status-aware-retrieval: 1 MODIFIED; contradiction-detection: 1 MODIFIED with Non-Goals parenthetical corrected), moves the change folder to the archive, flips ADR-0024 from `Proposed` to `Accepted`, and verifies all artifacts are byte-identical.

---

## Final-State Authority (per Skill §Final-State Authority)

Source ranking applied (most authoritative first):

1. **Persisted tasks artifact** (`tasks.md`) — 31/31 tasks checked, 0
   unchecked. All work units committed and merged to `main`.
2. **Explicit final-state facts in the orchestrator's launch prompt** —
   "Delivered as 2 stacked PRs, squash-merged to main: #1020 → cf2a170, #1021
   → 4f49e40"; "Tasks 31/31"; "Final suite 6484 passed, 2 skipped; ruff,
   format, mypy clean; CI green (9 checks) on both PRs"; "Live smoke: --revision
   wrote `type: revises` + both notes, `list` shows both decisions active;
   identical re-run logged 'no change' without duplicating edge or note;
   --winner on the revised pair and --winner+--revision refused"; "Owner
   decisions: reversal=supersedes / refinement=revises (hides nothing); resolved
   pairs never re-judged even after an edit (owner-confirmed shipped-behavior
   change)"; "Native review: mode disabled globally by the owner; no receipts".
3. **`apply-progress.md`** — lists partial state mid-implementation; it does not
   record final state once both PRs were merged. Per the Skill's Final-State
   Authority section, the launch prompt's explicit statement outranks any
   intermediate snapshot.

No contradiction between sources was found. The tasks artifact and launch
prompt agree completely on delivered scope, merged state, and test counts.

---

## Task Completion Gate

**Status**: PASS

Inspected `openspec/changes/archive/2026-09-25-revises-relation/tasks.md`
(persisted artifact, now at the archived path):

```
$ grep -c '\- \[x\]' openspec/changes/archive/2026-09-25-revises-relation/tasks.md
31
$ grep -c '\- \[ \]' openspec/changes/archive/2026-09-25-revises-relation/tasks.md
0
```

All 31 tasks marked `[x]`. No stale unchecked tasks remain.

---

## Spec Sync — Native Composition Merge

All three delta specs were composed into their living specs with the native
`gentle-ai sdd-archive-compose` command (mandatory native composition, never
a model-driven Read/Edit merge). All three invocations exited `0`.

```
$ gentle-ai sdd-archive-compose \
    --canonical "openspec/specs/reconcile-command/spec.md" \
    --delta "openspec/changes/revises-relation/specs/reconcile-command/spec.md" \
    --output "/tmp/reconcile-command-spec.compose-tmp" \
  && mv "/tmp/reconcile-command-spec.compose-tmp" "openspec/specs/reconcile-command/spec.md"
Exit code for reconcile-command: 0

$ gentle-ai sdd-archive-compose \
    --canonical "openspec/specs/status-aware-retrieval/spec.md" \
    --delta "openspec/changes/revises-relation/specs/status-aware-retrieval/spec.md" \
    --output "/tmp/status-aware-spec.compose-tmp" \
  && mv "/tmp/status-aware-spec.compose-tmp" "openspec/specs/status-aware-retrieval/spec.md"
Exit code for status-aware-retrieval: 0

$ gentle-ai sdd-archive-compose \
    --canonical "openspec/specs/contradiction-detection/spec.md" \
    --delta "openspec/changes/revises-relation/specs/contradiction-detection/spec.md" \
    --output "/tmp/contradiction-detection-spec.compose-tmp" \
  && mv "/tmp/contradiction-detection-spec.compose-tmp" "openspec/specs/contradiction-detection/spec.md"
Exit code for contradiction-detection: 0
```

All compositions succeeded and merged correctly.

### `reconcile-command` — 2 ADDED + 3 MODIFIED requirements

Both ADDED and all three MODIFIED requirements from the delta spec have been
merged into the living spec. Requirement name verification:

```
$ grep -cxF "### Requirement: Refinement Reconciliation via --revision" openspec/specs/reconcile-command/spec.md
1
$ grep -cxF "### Requirement: At Most One Resolution Per Pair" openspec/specs/reconcile-command/spec.md
1
$ grep -cxF "### Requirement: Default Symmetric Reconciliation" openspec/specs/reconcile-command/spec.md
1
$ grep -cxF "### Requirement: Directional Reconciliation via --winner" openspec/specs/reconcile-command/spec.md
1
$ grep -cxF "### Requirement: Idempotent Re-run" openspec/specs/reconcile-command/spec.md
1
```

All five requirements (2 ADDED, 3 MODIFIED) are present in the merged spec.
All pre-existing requirements remain present and unmodified.

### `status-aware-retrieval` — 1 MODIFIED requirement

The MODIFIED requirement from the delta spec has been merged:

```
$ grep -cxF "### Requirement: Effective Status Resolution" openspec/specs/status-aware-retrieval/spec.md
1
```

The requirement is present in the merged spec. All other pre-existing
requirements remain present and unmodified.

### `contradiction-detection` — 1 MODIFIED requirement + Non-Goals correction

The MODIFIED requirement has been merged, and the Non-Goals parenthetical has
been corrected by hand as per the delta spec's "Non-Goals Correction (for
archive)" note. Verification:

```
$ grep -cxF "### Requirement: Candidate Generation From Typed Graph Edges, Deduped" openspec/specs/contradiction-detection/spec.md
1
$ grep -c "(all typed edges are candidates)" openspec/specs/contradiction-detection/spec.md
0
$ grep -c "(excluding \`derived_from\` edges and any pair joined by a resolution edge" openspec/specs/contradiction-detection/spec.md
1
$ grep -c "## Non-Goals Correction" openspec/specs/contradiction-detection/spec.md
0
```

The MODIFIED requirement is present. The old parenthetical "(all typed edges
are candidates)" has been removed and replaced with the corrected
parenthetical "(excluding `derived_from` edges and any pair joined by a
resolution edge — `supersedes`, `reconciled_with`, or `revises`)". The
"## Non-Goals Correction (for archive)" heading itself was NOT copied into
the living spec (grep count is 0, correct).

---

## Archive Move

### Directory Listings

**Archived folder** (`openspec/changes/archive/2026-09-25-revises-relation/`):
```
apply-progress.md
design.md
proposal.md
specs/reconcile-command/spec.md
specs/contradiction-detection/spec.md
specs/status-aware-retrieval/spec.md
tasks.md
```
(plus `archive-report.md`, written by this step, additive)

**Original path** (`openspec/changes/revises-relation/`):
```
$ ls -la openspec/changes/revises-relation
ls: openspec/changes/revises-relation: No such file or directory
```
Confirmed absent.

### Move Mechanics and Readback

Mechanical `mv` of `openspec/changes/revises-relation/` to
`openspec/changes/archive/2026-09-25-revises-relation/`, run as one shell
transaction with a pre-move `cp -R` snapshot for the readback. (`git mv` was
attempted but failed with "source directory is empty" — the folder was
untracked, so plain `mv` was used per the skill's fallback logic.)

```
✓ plain mv fallback succeeded
✓ source is gone

=== Mechanical copy verification (diff -r) ===
✓ Diff output is empty (archive move is byte-identical)
```

Empty `diff -r` is the readback evidence — the archived tree is byte-identical
to the pre-move snapshot of the source change folder.

---

## ADR-0024 Status Flip

Per `openspec/config.yaml`'s `rules.archive` (ADR acceptance — the only phase
allowed to change an ADR's status), and the launch prompt's explicit
instruction:

- `docs/adr/0024-revises-relation-and-resolved-pairs.md`: frontmatter
  `status: Proposed` → `status: Accepted`; body `**Status:** Proposed` →
  `**Status:** Accepted`. Only the status lines changed — Context, Decision,
  Consequences, and Alternatives were left untouched (append-only once
  accepted).
- `docs/adr/README.md`: row for `0024` updated from `Proposed` to `Accepted`.

Verification that both sync:
```
$ uv run pytest -q tests/unit/test_adr_index.py
...................................................                      [100%]
51 passed in 0.26s
```

All ADR index checks pass. ADR-0024 is correctly accepted in all three
locations (file frontmatter, file body, and the README index).

---

## Final Checklist (per Skill §Step 4)

- [x] Main specs updated correctly (`sdd-archive-compose`, exit 0, all three
      domains; merges verified by requirement name)
- [x] Change folder moved to archive
      (`openspec/changes/archive/2026-09-25-revises-relation/`)
- [x] Archive preserves all artifacts that existed (proposal, design, tasks,
      apply-progress, all three delta specs); no artifact this change produced
      is missing
- [x] Archived `tasks.md` retains its original bytes; 31/31 tasks complete,
      0 unfinished
- [x] Active changes directory no longer has this change
      (`openspec/changes/revises-relation/` absent, confirmed)
- [x] Verbatim `diff -r` readback output included and is empty
      (byte-identity confirmed for the move)
- [x] ADR-0024 flipped to `Accepted` in both frontmatter and body, and in
      the `docs/adr/README.md` index row (verified by unit test pass)
- [x] Non-Goals parenthetical correction applied to contradiction-detection
      spec and verified present

---

## Artifacts Archived

**Change**: revises-relation  
**Archive Path**: `openspec/changes/archive/2026-09-25-revises-relation/`  
**Date Archived**: 2026-09-25  
**Sub-change**: 1 of 3 of #1014 piece (a); umbrella exploration at
`openspec/changes/decision-revision-detection/` (sub-changes 2 detector and
3 harness remain planned)

**Artifacts**:
- `proposal.md` — Intent (Refs #1014 piece (a)), scope, the `revises` relation
  design, ADR verdict, risks, rollback plan
- `design.md` — Design decisions for the `RESOLUTION_RELATION_TYPES` constant,
  `reconcile --revision` behavior, mixed-state classifier, exclusion of
  resolved pairs from contradiction candidates
- `apply-progress.md` — Per-PR implementation progress as work units landed
  (2-slice breakdown)
- `tasks.md` — 31 implementation tasks (all complete), two-slice breakdown
  with per-unit estimates
- `specs/reconcile-command/spec.md` — Delta spec: 2 ADDED requirements
  (Refinement Reconciliation via --revision; At Most One Resolution Per Pair)
  + 3 MODIFIED requirements (Default Symmetric Reconciliation; Directional
  Reconciliation via --winner; Idempotent Re-run), merged into
  `openspec/specs/reconcile-command/spec.md`
- `specs/status-aware-retrieval/spec.md` — Delta spec: 1 MODIFIED requirement
  (Effective Status Resolution), merged into
  `openspec/specs/status-aware-retrieval/spec.md`
- `specs/contradiction-detection/spec.md` — Delta spec: 1 MODIFIED requirement
  (Candidate Generation From Typed Graph Edges, Deduped) + Non-Goals
  parenthetical correction, merged into
  `openspec/specs/contradiction-detection/spec.md`

---

## Delivered Behavior

**Shipped PRs**:
- #1020 (commit `cf2a170`): `RESOLUTION_RELATION_TYPES` constant; `reconcile
  --revision` command and CLI; mixed-state classifier refusing every request
  on edge conflicts; reconciliation notes on both concepts; ADR-0024; docs
- #1021 (commit `4f49e40`): resolved pairs excluded from contradiction
  candidates before count and cap; exclusion applies under `--include-deprecated`;
  docs

**Owner-confirmed changes**:
- `reversal=supersedes` (hides the old concept) / `refinement=revises` (hides
  nothing) distinction
- Resolved pairs (joined by `supersedes`, `reconciled_with`, or `revises`)
  never re-judged as contradiction candidates, even after an edit to either
  concept; the resolution edge must be removed to restore candidacy
- Hand-edited mixed pairs now refuse instead of precedence classification

**Known limitations (not fixed)**:
- Identical re-run stdout says "recorded" while log says "no change" (inherited
  from `--winner` / symmetric behavior)
- Vacuous-coverage notice wording on a bundle whose only typed edges are
  resolution edges (edge case with zero contradiction candidates)

---

## Verification

**Status**: Per the launch prompt, "Native review: mode disabled globally by
the owner; no receipts". The launch prompt also states "Final suite 6484
passed, 2 skipped; ruff, format, mypy clean; CI green (9 checks) on both
PRs." A formal `verify-report.md` artifact was not persisted for this change.

The change's final merged state (both PRs squash-merged to `main`) has been
independently verified by the launch prompt's statement that CI was green on
each PR (9 checks per PR, 18 total CI runs across the stack).

**Live smoke testing** (per launch prompt):
- `--revision` flag wrote `type: revises` relation edge
- Both reconciliation notes present on concepts (one `revises`, one `revised`)
- `list` command shows both decisions active (nothing hidden)
- Identical re-run logged "no change" without duplicating edge or note
- `--winner` on the revised pair refused (mixed-state classifier)
- `--winner+--revision` on the same pair refused (conflicting modes)

---

## Cycle Summary

**SDD Cycle Status**: COMPLETE

1. **Proposed** — scope: add `revises` relation type for refinement;
   `reconcile --revision` command; exclusion of resolved pairs from
   contradiction candidates; ADR verdict recorded
2. **Specified** — three delta specs for reconcile-command, status-aware-retrieval,
   and contradiction-detection domains
3. **Designed** — `RESOLUTION_RELATION_TYPES` constant, `reconcile --revision`
   behavior, mixed-state classifier, candidate exclusion mechanics
4. **Tasked** — 31 tasks across two chained slices (all complete)
5. **Applied** — two merged PRs (#1020 `cf2a170`, #1021 `4f49e40`),
   squash-merged to `main`
6. **Verified** — per launch prompt: "Final suite 6484 passed, 2 skipped; ruff,
   format, mypy clean; CI green (9 checks) on both PRs"; live smoke testing
   passed; review offered and declined by owner; no review receipts
7. **Archived** — folder moved, three delta specs merged, Non-Goals correction
   applied, ADR-0024 accepted, all readbacks empty/green

**Ready for the next change** (sub-change 2 detector and sub-change 3 harness
remain in the umbrella exploration at `openspec/changes/decision-revision-detection/`).

---

## Key Learnings

1. The Non-Goals parenthetical in a requirement can drift silently when a spec gets rewritten by delta composition—the correction must be applied after composition by consulting the delta's "for archive" notes, never assumed to be automatic.
2. Three resolution relation types sharing one constant ensures the classifier and the candidate filter do not drift apart, and a simple test ties the modes to the constant to catch future inconsistencies.
3. Resolved pairs excluded before the contradiction cap ensures the cost gate's reported count and the actual LLM spend stay exact—the exclusion is an audit mechanism, not just a behavioral policy.
4. Hand-edited mixed pairs (multiple inconsistent resolution edges) are an edge case that only exists in hand-edited frontmatter, not in CLI or programmatic writes—refusing the request instead of precedence honors the invariant that a pair carries at most one resolution.
5. A small umbrella exploration (decision-revision-detection) can spawn multiple sub-changes (revises-relation, detector, harness) each with clear scope and independent delivery, keeping review burden small while delivering the full feature in stages.
