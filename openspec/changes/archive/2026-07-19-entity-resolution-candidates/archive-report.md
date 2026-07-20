# Archive Report: entity-resolution-candidates

**Change**: entity-resolution-candidates (MVP-2 slice 1 of a 2-3 change mini-chain) | **Archived**: 2026-07-19 | **Status**: Complete | **Repository**: openkos (main ca68124) | **Mode**: hybrid

This archive report closes the SDD cycle for the `entity-resolution-candidates` change: a read-only, whole-bundle derived-layer pass that surfaces CANDIDATE pairs/groups of same-type objects that might be the same real-world entity (e.g. "Stoicism" vs "Stoic Philosophy"), for human review only. It never decides, merges, or writes.

## Change Summary

**Purpose**: Today identity is a title-derived slug with no cross-object dedup check; differently-worded titles for the same entity silently fragment, and identical slugs are silently dropped. This slice makes that fragmentation VISIBLE without touching anything else in the system.

**Scope shipped**:
- New `src/openkos/resolution/` package: `__init__.py`, `normalize.py`, `similarity.py`, `candidates.py`.
- `normalize_key(title)`: deterministic Unicode folding (NFKD → drop combining marks → casefold → punctuation-to-space → collapse whitespace).
- `similarity.py`: deterministic stdlib-only near-match tier via `difflib.SequenceMatcher` token-subset containment, threshold `0.75`, minimum token length `3` (both named `Final` constants).
- `find_candidates(bundle_dir) -> list[CandidateGroup]`: reuses `okf._iter_docs` (same read/parse-error skip pattern as `state/fts.py`/`graph/sqlite_graph.py`), partitions non-Source documents by exact OKF `type`, and within each partition proposes HIGH-tier (exact normalized-key match, N-member groups) and LOW-tier (near-match pairs, HIGH∩LOW structurally disjoint) candidate groups.
- `CandidateGroup` frozen dataclass: `okf_type`, `member_ids` (sorted tuple), `tier`, `trigger` (the normalized key or similarity reason).
- Read-only `duplicates` CLI verb (`src/openkos/cli/main.py`), zero parameters (no `--json`/`--auto`), mirroring `lint`/`status`'s `require_workspace` gate and shape; prints candidate groups or a clear "no candidates" report; exits 0 in all non-workspace-refusal cases; performs zero writes.
- `tests/unit/resolution/test_layering.py`: AST-based (non-vacuous) static-import guard confirming `model`/`bundle`/`state` never import `openkos.resolution`, and `resolution` only imports `okf` (not `bundle`/`state`/`graph`).
- Integration proof: `find_candidates` run over `examples/good-life-demo/bundle` and the `duplicates` CLI verb run against the same bundle, both asserting byte+mtime-identical before/after.
- `docs/cli.md` updated to document `duplicates` alongside `lint`/`status`.

**Key implementation decision confirmed at verify time**: only `CandidateGroup` was implemented (an N-member-tuple-holding frozen dataclass); design.md's Interfaces section additionally named a `Candidate` type that was never needed and never implemented. Functionally equivalent — no spec scenario requires a separate `Candidate` type. Downgraded from a naming deviation to a SUGGESTION (non-blocking) at verify time, since it affects no observable behavior.

## Artifacts Archived

| Artifact | Location | Status |
|---|---|---|
| Proposal | `archive/2026-07-19-entity-resolution-candidates/proposal.md` | Moved from change folder; Engram `sdd/entity-resolution-candidates/proposal` (id 1162) |
| Specification (delta) | `archive/2026-07-19-entity-resolution-candidates/specs/entity-resolution/spec.md` | Copied verbatim to main spec tree at `openspec/specs/entity-resolution/spec.md` (new domain); Engram `sdd/entity-resolution-candidates/spec` (id 1163) |
| Design | `archive/2026-07-19-entity-resolution-candidates/design.md` | Moved from change folder; Engram `sdd/entity-resolution-candidates/design` (id 1164) |
| Tasks | `archive/2026-07-19-entity-resolution-candidates/tasks.md` | 22/22 checkboxes `[x]` across 5 phases (confirmed by direct filesystem read at archive time); Engram `sdd/entity-resolution-candidates/tasks` (id 1165) |
| Apply progress | (recorded below) | Engram `sdd/entity-resolution-candidates/apply-progress` (id 1166) |
| Verification Report | (recorded below) | Engram `sdd/entity-resolution-candidates/verify-report` (id 1167) |

Note: the Engram `sdd/entity-resolution-candidates/tasks` observation (id 1165) reflects an earlier upsert snapshot with Phase 4-5 boxes still unchecked (Unit 1 only); the filesystem `openspec/changes/entity-resolution-candidates/tasks.md` is the authoritative source in hybrid mode and was read directly at archive time, confirming all 22 boxes `[x]`. This matches the verify-report's own independent confirmation ("tasks.md Phases 1-5: all 22 checkboxes `[x]` — confirmed by direct file read, not trusted from apply-progress"). No task-completion gate issue exists; this is a known Engram topic-key upsert staleness, not an incomplete-work signal.

## Spec Merge Summary

| Action | Domain | Details |
|---|---|---|
| **CREATED** | `entity-resolution` | New spec domain created at `openspec/specs/entity-resolution/spec.md` (no prior main spec existed for this capability) |
| Requirement count | 8 requirements + 13 scenarios | Full specification of the read-only candidate-generation contract: whole-bundle scan, strict per-type blocking, HIGH exact-key tier, LOW near-match tier, deterministic/read-only building, no-self-pair/pair-once/trivial-bundle handling, degrade-not-crash, and the read-only `duplicates` CLI verb |
| Source | Delta spec from change folder | `openspec/changes/entity-resolution-candidates/specs/entity-resolution/spec.md` → `openspec/specs/entity-resolution/spec.md` |
| Merge mode | Direct copy (no pre-existing spec; delta already authored in living-spec shape — no ADDED/MODIFIED delta headers to convert) | First spec for this domain; delta becomes canonical main spec verbatim |

No requirements or scenarios were invented, dropped, or altered during merge — the delta spec (Engram id 1163) was already structured as a standalone capability spec (Purpose / Non-Goals / Requirements+Scenarios) and is preserved faithfully in `openspec/specs/entity-resolution/spec.md`, matching the structure of `openspec/specs/graph-projection/spec.md` and `openspec/specs/ingestion/spec.md`.

## Verification Status

**Final Verdict** (Engram `sdd/entity-resolution-candidates/verify-report`, id 1167): **VERIFIED** — 0 CRITICAL, 0 WARNING, 1 SUGGESTION.

**Evidence Summary** (independently reproduced at verify time, on branch `feat/erc-02-cli-verb`, not merely copied from apply-progress):
- `uv run pytest` → 715 passed, exit 0
- `uv run ruff check .` → "All checks passed!", exit 0
- `uv run ruff format --check .` → "70 files already formatted", exit 0
- `uv run mypy .` → "Success: no issues found in 70 source files" (mypy strict), exit 0
- `uv run pytest --cov=openkos.resolution --cov-branch --cov-report=term-missing tests/unit/resolution/` → 59 passed; **100% branch coverage** on `openkos.resolution` (110 stmts / 34 branches, 0 missed; gate ≥ 90%, comfortably exceeded)
- `uv run pytest -q tests/unit/cli/test_duplicates.py tests/unit/resolution/test_layering.py -v` → 13 passed, exit 0
- Independently reproduced numbers matched apply-progress's self-reported numbers exactly (715/8/0/0)

**All 10 requirement families verified PASS** against `openspec/specs/entity-resolution/spec.md` (spec obs #1163):
1. Whole-bundle read-only `find_candidates` reusing `okf._iter_docs` — PASS.
2. Strict same-type partitioning; Sources excluded; no cross-type candidates — PASS.
3. Two deterministic tiers (HIGH exact key; LOW `difflib` ratio ≥ 0.75, min token length 3; HIGH∩LOW structurally disjoint) — PASS.
4. No self-pairs; each unordered pair once; stable deterministic ordering; N-member HIGH groups — PASS.
5. Degrade-not-crash; empty/single-object bundle → no candidates, no raise — PASS.
6. Ephemeral candidates — no persisted OKF type, no new bundle state — PASS.
7. LOW-tier precision tradeoff documented and characterization-tested; threshold locked on both sides of 0.75 — PASS.
8. `duplicates` CLI verb: read-only, `require_workspace` gate, exit 0 with/without candidates, writes nothing, not named `resolve`/`merge`, no confirm gate — PASS.
9. Layering: `resolution` imports `okf` read-only; canonical MUST NOT import `resolution`; genuine non-vacuous AST guard — PASS.
10. Non-goals respected (no LLM/embeddings/merge/tombstone/stable-id code; no `ingest` or canonical-layer file touched in the diff) — PASS.

**Accepted design tradeoff — LOW-tier high-recall / lower-precision**: the LOW similarity tier deliberately favors recall over precision. Short-token false positives (e.g. `"cats"`/`"carts"`-style near-matches) are a known, accepted, and explicitly characterization-tested behavior — `similarity.py`'s docstring documents the tradeoff with a worked structural analogy, and dedicated tests (`test_is_near_match_short_token_false_positive_is_accepted_by_design` and its sibling) lock this behavior so it cannot be silently "fixed" by a future change. This is intentional: LLM adjudication (slice 2) owns precision — candidates are proposals for human/LLM review, never automatic decisions, so a wider recall net at this stage is the correct tradeoff.

**Issues at final close**:
- 0 CRITICAL.
- 0 WARNING (the `Candidate`/`CandidateGroup` naming deviation from design.md was downgraded to SUGGESTION — no spec scenario or observable behavior is affected).
- 1 SUGGESTION outstanding (non-blocking): update design.md's Interfaces snippet to drop the stray `Candidate` name for future-reader clarity. Recorded here for traceability; does not block archive per the skill's CRITICAL-only blocking policy.

## Delivery History

Delivered as a **2-unit Feature Branch Chain** (tasks forecast, Engram id 1165: ~550-650 estimated changed lines against the 400-line review budget — `ask-on-risk` fired, High risk, chained PRs recommended).

1. **Sub-PR #74** — Unit 1 (Phases 1-3): `resolution` library core — `normalize.py`, `similarity.py`, `candidates.py`, `__init__.py`, and the full RED-first unit test suite for normalization, similarity, and `find_candidates` (partitioning, HIGH/LOW tiers, no-self-pair/pair-once/ordering/determinism, degrade-not-crash, empty/single-object bundle).
2. **Sub-PR #75** — Unit 2 (Phases 4-5): `duplicates` CLI verb, the AST-based layering guard, `docs/cli.md` update, and the real-bundle integration proof (`test_real_bundle_readonly` plus the CLI's own read-only-over-good-life-demo test). Included a test-only strengthening (no production code change): the CLI read-only snapshot helper (`_snapshot`/`_snapshot_entry`) was upgraded to compare byte content AND `st_mtime_ns` (previously bytes-only), closing a gap where a touch-without-content-change regression would have gone undetected — matching the sibling library-level integration test's stricter check. Both affected tests still passed after the fix, confirming no touch regression existed.

Both sub-PRs landed to `main` via **PR #76** (the tracker/integration PR), merged as commit **ca68124**.

**Repository State**: `main` @ `ca68124`.

## Deferred / Non-Goals (explicit, unchanged from proposal through archive)

The following remain explicitly out of scope for `entity-resolution-candidates` and are deferred to later slices in the mini-chain or beyond, per the proposal's Out of Scope section and the spec's Non-Goals:

- LLM adjudication of candidates — **slice 2 of this mini-chain** (next planned change).
- Destructive `merge`/`resolve` verb, merge record, tombstone, sensitivity recompute, un-merge — **slice 3 of this mini-chain** (planned after slice 2, confirm-gated and reversible).
- Embedding/vector-based candidate generation (separate later MVP-2 deliverable, not part of this mini-chain).
- Any mutation of bundle bytes; any change to `ingest`'s single-source Phase A/B contract — confirmed untouched by diff-stat inspection at verify time (16 files, all additive, 1646 insertions / 0 deletions, no `ingest` or canonical-layer file touched).
- Stable/content-based concept ids (separate concern; `concept_id` remains the existing bundle-relative path).

## Risks & Limitations Recorded

| Risk | Likelihood | Status |
|---|---|---|
| LOW-tier tradeoff: high recall admits short-token false positives (e.g. "cats"/"carts"-style near-matches) | Med (by design) | Accepted — documented in `similarity.py`'s docstring and locked by two dedicated characterization tests so the behavior cannot be silently "fixed"; precision is explicitly deferred to slice 2 (LLM adjudication), which reviews candidates rather than trusting them automatically |
| Candidate record could harden into a 10th pseudo-type (proposal principle 4 risk) | Med | Mitigated — candidates stay ephemeral (`CandidateGroup` frozen dataclass only), no persisted OKF type, no `bundle/` state file; confirmed absent from the diff at verify time |
| `Candidate`/`CandidateGroup` naming deviates from design.md's Interfaces snippet | Low | Non-blocking SUGGESTION; functionally equivalent, no spec scenario requires a distinct `Candidate` type; design.md updated with an implementation note is a candidate for a later doc-only cleanup, not required before archive |
| Engram `tasks` observation (id 1165) reflects a stale Unit-1-only snapshot (topic-key upsert did not capture Unit 2's checkbox updates) | Low | Non-blocking — hybrid mode's authoritative source is the filesystem `tasks.md`, which was read directly and confirmed 22/22 `[x]` both at verify time and at this archive |

## Archival Actions Completed

**Filesystem**:
- [x] Living spec created at `openspec/specs/entity-resolution/spec.md` (new capability domain; direct copy from the delta spec, format matched against `openspec/specs/graph-projection/spec.md` and `openspec/specs/ingestion/spec.md`)
- [x] Change artifacts (proposal, design, tasks, specs) written to `openspec/changes/archive/2026-07-19-entity-resolution-candidates/`, byte-identical to the pre-archive change-folder originals
- [x] This archive report written to `openspec/changes/archive/2026-07-19-entity-resolution-candidates/archive-report.md`

**Engram**:
- [x] Archive report to be saved with topic key `sdd/entity-resolution-candidates/archive-report`
- [x] All artifact observation IDs recorded above for traceability (proposal 1162, spec 1163, design 1164, tasks 1165, apply-progress 1166, verify-report 1167)

## Known Limitation of This Archival Pass

The executor performing this archive had access only to `Read`/`Write`/`Edit`/`Glob` and Engram tools — no shell/`git` tool was available in this session (same constraint as the preceding `graph-projection` archive). Every artifact was therefore **written as a byte-identical copy** to the archive and living-spec locations rather than moved with `git mv`, and the original `openspec/changes/entity-resolution-candidates/` directory could **not** be deleted by this executor. The orchestrator is expected to run the equivalent of `git rm -r openspec/changes/entity-resolution-candidates/` after confirming the archived copies are byte-identical to the originals, so the rename is preserved as a move in history and the active changes directory no longer lists this change.

## Next Steps

**For the project**:
- `entity-resolution-candidates` (slice 1) unblocks slice 2 of the mini-chain: LLM adjudication of the candidates surfaced here (owning precision, reviewing HIGH/LOW tier proposals).
- Slice 3 (destructive, confirm-gated, reversible merge — tombstone, un-merge, sensitivity recompute) is planned after slice 2 and remains untouched by this change.
- The deferred non-goals above (embedding/vector-based candidate generation, stable/content-based concept ids) remain open follow-up candidates for future MVP-2 change proposals, separate from this mini-chain.

**For archive verification**:
- No CRITICAL or WARNING issues remain; the single outstanding SUGGESTION (design.md's stray `Candidate` name) is non-blocking and may be addressed in a future doc-only cleanup.
- The only outstanding action is the mechanical `git rm`/deletion of the original change folder noted above, owned by the orchestrator (no shell/git tool available to this executor).

## Traceability

This archive report records the final state of the `entity-resolution-candidates` change from proposal through verification and archival. The change has been:
- Fully specified (8 requirements, 13 scenarios)
- Fully designed (6 architecture decisions, ADR gate correctly did not fire — all decisions cheaply reversible)
- Fully implemented (2-unit feature-branch-chain, sub-PRs #74-#75, landed via #76)
- Fully verified (100% branch coverage on `openkos.resolution`, mypy strict clean, 715/715 tests passing, 0 CRITICAL / 0 WARNING / 1 non-blocking SUGGESTION)
- Fully delivered (main @ ca68124)

The SDD cycle is CLOSED for slice 1. The change is archived and ready for slice 2 (LLM adjudication) to begin, pending the mechanical folder-removal follow-up noted above.

**Archive Date**: 2026-07-19 (ISO format)
**Repository Head**: ca68124 (main)
**Archival Status**: COMPLETE (content), PENDING (original-folder removal — requires shell/git access not available to this executor)
