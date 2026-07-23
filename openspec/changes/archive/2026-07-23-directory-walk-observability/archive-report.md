# Archive Report: Directory-Walk Observability Hardening (S3 Follow-Up)

## Executive Summary

Change `directory-walk-observability` archived and closed. Two new requirements merged into `openspec/specs/sensitivity-aware-llm/spec.md` (Walk-Incompleteness Observability, Defense-in-Depth Sensitivity Re-Check at Load). Single PR #118 (commit 85e1216), merged to main. Verification: PASS. Test count: 1706 passed. No CRITICAL issues. Archive report with full traceability persisted to Engram (topic `sdd/directory-walk-observability/archive-report`).

## Change Summary

**Title**: Directory-Walk Observability Hardening (S3 follow-up)

**Scope**: Observability signal + leak closure over the shipped S3 fail-closed sensitivity filter (`sensitivity-aware-llm` capability).

**Deliverable**: Two protective layers added to five sensitivity-filter verbs (query, contradictions, adjudicate, suggest-relations, suggest-volatility):

1. **Layer 1 (Signal)**: Incomplete-walk observability. When `okf._walk_errors` reports unlistable subdirectories, emit a warning to STDERR (exit 0) and continue, except when `--include-confidential` is passed (filter is deliberately off).
2. **Layer 2 (Leak Closure)**: Defense-in-depth re-check at document load. Each of 4 verbs (contradictions, adjudicate, suggest-relations, suggest-volatility) independently re-checks each document's sensitivity against its own frontmatter before sending to LLM, using the shared pure predicate `sensitivity.should_block(metadata, *, include_confidential)`. Query already conforms (S3 FIX-2, answer.py:211-214).

**Delivery**: Single PR #118 (squash commit 85e1216, 17 files, 1016 insertions+39 deletions). Implementation: feat/walk-observability branch. Verification: full main-branch test suite (1706 passed, including 27 new behavior-first tests).

## Locked Decisions

1. **Warn, not refuse** (Design Decision 1): Incomplete-walk warning emits to STDERR and exits 0; refuse mode for cloud-egress slice is explicit out-of-scope deferred (Design Decision 2).
2. **Shared CLI helper** (Design Decision 5): Single `cli/observability.py::warn_if_walk_incomplete(bundle_dir, *, mode="warn", include_confidential=False)` helper wired at exactly 5 identical call sites in `cli/main.py` (lines 2850, 2976, 3094, 3228, 3496).
3. **Extract excluded from this scope** (Design Decision 3, Requirement non-goal): `extract` command does not participate in per-doc re-checks; it already gates on workspace `default_sensitivity` floor (separate S3 feature).
4. **Signal + leak-closure scope** (Design Decision 4): Four resolution verbs (contradictions, adjudicate, suggest-relations, suggest-volatility) receive the per-doc re-check; query is already conformant, no change required.

## Merged Specifications

**File**: `openspec/specs/sensitivity-aware-llm/spec.md`

**Changes**: Added 2 new canonical requirements (appended, delta markers dropped):

- **Requirement: Walk-Incompleteness Observability** — 3 scenarios (incomplete walk warns + exit 0, clean bundle silent, --include-confidential suppresses warning)
- **Requirement: Defense-in-Depth Sensitivity Re-Check at Load** — 4 scenarios (confidential doc missed by walk is caught at load, --include-confidential bypasses re-check, query already conformant, shared predicate identity)

Existing 6 requirements preserved unchanged (Fail-Closed Sensitivity Resolution, Private/Public Pass Through, Uniform Enforcement, Extract Gates, --include-confidential Escape, Exclusion Not Redaction).

## Review Outcome

**Gate**: Native review receipt present and valid. Candidate tree, paths digest, policy, ledger, fix delta, and verification evidence all bound. Scope did not change post-apply.

**4R Review**: risk + reliability **CLEAN**; maintainability + cost identified 3 actionable findings.

**Findings Fixed (Correction Batch, commits fb45880 + 365e907 + b958ac8)**:

1. **C.1 (Maintainability WARNING)**: Centralized 5-way-duplicated inline sensitivity predicate into pure `sensitivity.should_block(metadata, *, include_confidential=False)`. Adopted at all 5 call sites (retrieval/answer.py, resolution/{contradiction,edge_typing,adjudication}.py, volatility_typing.py). Behavior-preserving refactor; all pre-existing tests stayed green.

2. **C.2 (Cost WARNING)**: `volatility_typing._reread_sensitivity_blocked` now takes `include_confidential` directly (matching sibling contract) instead of relying on external `if not include_confidential:` wrapper at call site. Removes asymmetric bare-return form that invited fail-open bypass if called by analogy with 3 siblings.

3. **C.3 (Resilience WARNING)**: Walk-independent per-doc re-read runs AFTER sampling (renamed `_sample_docs_by_type`, now returns `LintDoc` objects instead of truncated body strings), applied only to the sampled subset (≤5 per type) instead of full bundle beforehand. Eliminates needless full-bundle frontmatter re-reads for volatility's weakest-leverage guard. Upstream walk-based `blocked` filter unchanged (still full-bundle, cheap id check). Exclusion guarantee preserved on every doc sent to LLM.

**Verification**: Correction batch re-validated clean. All 1706 tests passed (1699 baseline + 7 new: 4 `should_block` unit tests + 3 volatility guard/sampling tests). mypy, ruff check, ruff format all passed.

**Spec Compliance**: Both requirements remain fully satisfied. FIX 1/2 are pure internal refactors; FIX 3 preserves exact exclusion semantics.

## Final Test Count

**Total**: 1706 passed

- **Baseline** (before change): 1672 passed
- **New tests added**: 27 (4 observability + 15 CLI signal + 8 leak-closure)
- **Correction batch verification**: 1706 passed (1699 baseline + 7 new: 4 `should_block` + 3 volatility sampling/guard)

**Runtime evidence**:
- `uv run pytest`: 1706 passed (exact match)
- `uv run mypy .`: Success, 121 source files
- `uv run ruff check .`: All checks passed
- `uv run ruff format --check .`: 121 files already formatted

## Deferred Follow-Ups

1. **S3.2: Query hot-path triple full-tree walk** (Design option 3c, not implemented): `query` command currently runs 3 separate full-tree walks per call (`sensitive_concept_ids` for blocking, `collect_docs` for lint, standalone answer phase re-check). Real fix: share one walk. Deferred to future S3 tranche due to scope/complexity. Design Decision 8 and Known Follow-ups documented in design.md.

2. **S3.3: Hard-refuse mode** (Design Decision 2, explicit out-of-scope): Cloud-egress slice will add `mode="refuse"` (raise NotImplementedError seam already in place in `warn_if_walk_incomplete`). Deferred to S4 cloud deployment.

3. **S3.1 cosmetic + perf**: Minor adjudication.py:162 docstring improvements and S3 double-walk perf analysis. Documented in design.md Known Follow-ups, not blocking archive.

## Artifact Traceability

**Engram Observations** (full content archived for audit trail):

- Proposal: `sdd/directory-walk-observability/proposal` (ID: 1700)
- Specification Delta: `sdd/directory-walk-observability/spec` (ID: 1701)
- Design: `sdd/directory-walk-observability/design` (ID: 1702)
- Tasks: `sdd/directory-walk-observability/tasks` (ID: 1703)
- Apply Progress: `sdd/directory-walk-observability/apply-progress` (ID: 1706)
- Verification Report: `sdd/directory-walk-observability/verify-report` (ID: 1707)
- Archive Report: `sdd/directory-walk-observability/archive-report` (this artifact, topic_key: `sdd/directory-walk-observability/archive-report`)

**Filesystem Artifacts**:

- Canonical spec (merged): `openspec/specs/sensitivity-aware-llm/spec.md`
- Change folder (archival): moved to `openspec/changes/archive/2026-07-23-directory-walk-observability/`
- Archive report (this file): `openspec/changes/directory-walk-observability/archive-report.md` (persisted at time of archival, also in Engram)

## SDD Cycle Closed

The change has been fully planned, implemented, verified, and archived. All tasks are complete (25/25 checkbox items marked [x]). Verification report passes with no CRITICAL issues. Review receipt valid. Canonical specs updated and source of truth established.

Ready for the next change.
