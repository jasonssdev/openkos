# Archive Report: Reference-Aware Forget — Scope/Depth Cascade (S2b)

**Change**: `reference-aware-forget-cascade` (MVP-3 gap #8 · S2b)
**Archived to**: `openspec/changes/archive/2026-07-23-reference-aware-forget-cascade/`
**Archive Date**: 2026-07-23

## Change Summary

S2a shipped self-scope reference-aware `forget`, which refuses `forget <source>` when the source has cascade-member children (each renders a backlink via Related section). S2b extends this to allow purging a Source AND its orphaned provenance descendants in one bounded operation by interpreting those blocking backlinks as cascade members under `--scope source`.

**Scope**: New requirement (Scope Selection), three new requirements (Provenance Descendant Resolution, Full-Set Preview and Count Confirmation), and six modified requirements (Inbound Reference Detection, Unverifiable Referrer Detection, Refuse Forget, Log Entry on Forget, Resurrection Interaction Disclosure, Catalog-Before-File Write Ordering) now operate over the purge SET instead of a single concept.

**Out of scope**: `--depth` option (provenance tree depth-1 deferred), deleting raw/ files, git-history rewrite, derived-index purge, typed-graph descendants (derived_from/produced_by relations are not traversed).

## Delivery

| Artifact | Details |
|----------|---------|
| PR #115 (squash b6e281d) | Main merge to `main` — contains PR1 (cbeab08, provenance helper) + PR2 (68cf616, cascade wiring) + correction batch (squashed into 68cf616) |
| Prior delivery | PR1: `src/openkos/bundle/provenance.py` pure helper, `tests/unit/bundle/test_provenance.py` (15 tests) |
| Prior delivery | PR2: cascade wiring in `src/openkos/cli/main.py::forget`, test expansion in `tests/unit/cli/test_forget.py` (+36 new tests, 46 total) |
| Correction batch | Post-review fixes (readability, resilience, reliability test gaps) squashed into PR2, verified by fresh full-suite run |

## Review & Verification

**Full 4R Review Conducted**: risk (clean — no security exposure), reliability (fixed via 3 new correction-batch tests + non-vacuity proofs), readability (tuple-order convention fix), resilience (K-of-N partial-failure observability added).

**Verification Result**: PASS WITH WARNINGS
- Fresh test evidence: 1599 passed (61 delta tests mapped to all delta scenarios), zero failures
- Code quality: ruff/format/mypy all pass (whole-tree clean)
- Spec/implementation alignment: all 9 ADDED + 6 MODIFIED delta scenarios have passing tests
- Known non-blocking gaps accepted as follow-ups (see below)

**Tasks Status**: All implementation tasks (Phases 1–6) marked complete:
- Phase 1–3 (Provenance helper RED/GREEN/REFACTOR): ✅
- Phase 4–6 (Cascade wiring RED/GREEN/REFACTOR + verify): ✅

## Locked Decisions Honored

1. **Scope Selection**: `--scope {self,source}` with self default, byte-identical S2a behavior for self
2. **Provenance Descendant Resolution**: Non-empty-provenance subset invariant guards against over-delete; fixed-point iteration; MUST NOT import `openkos.graph`
3. **Set-Difference Refuse Gate**: Intra-set backlinks (referrer ∈ purge_set) excluded; external + unverifiable still fail-closed
4. **Full-Set Preview + Count Confirmation**: Every member id listed; count stated in prompt; `--force` does NOT auto-confirm
5. **N-Tombstone Log Entry**: One per removed concept, sorted order preserved
6. **Catalog-Before-File N-Unlink**: index/log written BEFORE all unlinks, unlinks in sorted order LAST
7. **K-of-N Partial-Failure Reporting**: Cascade (N>1) errors now report how many of N were removed + how many remain + recovery path

## Spec Sync to Canonical

**File**: `openspec/specs/forget-command/spec.md`
**Action**: Merged S2b delta (3 ADDED, 6 MODIFIED, 0 REMOVED)

| Requirement | Action | Notes |
|-------------|--------|-------|
| Scope Selection | ADDED | New, enables `--scope {self,source}` |
| Provenance Descendant Resolution | ADDED | New, computes orphan-closure purge set |
| Full-Set Preview and Count Confirmation | ADDED | New, states total N in preview + prompt |
| Inbound Reference Detection | MODIFIED | Now detects over entire purge set, not just root |
| Unverifiable Referrer Detection | MODIFIED | Now scans for any purge-set member id in text |
| Refuse Forget When Inbound References Exist | MODIFIED | Now applies set-difference filter; intra-set refs dropped |
| Log Entry on Forget | MODIFIED | Now writes N tombstones (one per member), not one |
| Resurrection Interaction Disclosure | MODIFIED | Now checks every purge-set member's outbound supersedes |
| Catalog-Before-File Write Ordering | MODIFIED | Now handles N-member sorted unlinks; added K-of-N partial-failure report |
| Concept-ID Resolution and Path Safety | PRESERVED | Unchanged |
| Workspace Presence Check | PRESERVED | Unchanged |
| Nonexistent Concept Refusal | PRESERVED | Unchanged |
| Generic Index Entry Removal | PRESERVED | Unchanged |
| `--force` Is Orthogonal to the Confirm Gate | PRESERVED | Unchanged |
| Review/Confirm Flow | PRESERVED | Unchanged |
| Malformed Bundle Handling | PRESERVED | Unchanged |

**Merge Conflicts**: None. All MODIFIED requirements cleanly replaced their S2a versions in the canonical spec; ADDED requirements inserted in logical order before related existing requirements.

## Known Accepted Follow-Ups (Non-Blocking)

1. **Partial-failure false "Removed" claim**: Tombstones are written to log.md before unlinks occur; if an unlink fails partway through the N members, the log will show "removed" for concepts that may not have been unlinked. **Mitigation**: Git-recoverable; no programmatic log consumer exists yet; operator can verify via `git status` and recover with `git checkout`.

2. **Re-run after partial cascade**: If a cascade partially fails and the operator re-runs `forget`, they may hit the "Nonexistent Concept Refusal" on already-deleted members. **Mitigation**: `openkos lint` or manual `forget --scope self` on remaining orphans per the lint guidance; inherent to the approved non-transactional model.

3. **Inline scan not extracted**: The per-member inbound reference scan in `forget` is not extracted to a `bundle/` helper (unlike the pure `provenance` helper). **Rationale**: `forget` is the only current consumer; extraction deferred as planned for a future refactor.

## Engram Artifacts

All phase artifacts persisted with observation IDs:
- Proposal (#1671): `sdd/reference-aware-forget-cascade/proposal`
- Spec (#1672): `sdd/reference-aware-forget-cascade/spec` (delta)
- Design (#1674): `sdd/reference-aware-forget-cascade/design`
- Tasks (#1675): `sdd/reference-aware-forget-cascade/tasks`
- Verify Report (#1682): `sdd/reference-aware-forget-cascade/verify-report`

**This Archive Report**: `sdd/reference-aware-forget-cascade/archive-report` (persisted at archive time)

## SDD Cycle Status

**✅ Complete**: S2b of MVP-3 gap #8 is fully designed, implemented, verified, and archived.

**Next Arc Slice**: S3 (sensitivity) — the next HARD GATE before cloud/export. Handles access-control-aware purge and right-to-be-forgotten compliance layers.

**Note**: S2a + S2b together complete the "full lifecycle/forget surface" except the deferred right-to-be-forgotten purge (raw/ removal + git-history rewrite), which remains git-recoverable and is intentionally deferred beyond S3.

---

**Archived**: 2026-07-23 (UTC)
**Archive Agent**: sdd-archive (executor)
**Source of Truth**: `openspec/specs/forget-command/spec.md` (now merged and canonical)
