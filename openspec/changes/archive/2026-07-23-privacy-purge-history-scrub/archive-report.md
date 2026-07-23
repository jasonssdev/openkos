# SDD Archive Report: privacy-purge-history-scrub (Slice 2)

**Date**: 2026-07-23  
**Status**: Complete  
**Merged to main**: PR #121 (squash 4dfa70b)  
**Change folder archived to**: openspec/changes/archive/2026-07-23-privacy-purge-history-scrub/

## Executive Summary

Slice 2 of privacy-purge completes right-to-be-forgotten (RTBF) by extending Slice 1's whole-file expunge with a one-pass git-filter-repo `--file-info-callback` that content-scrubs `bundle/index.md` and `bundle/log.md` across ALL git history, removing purged concept bullets/tombstones by markdown link-identity with no residual-warning (the RTBF verb is now genuine). Delivered as PR #121 with `size:exception` acceptance for test-dense collision/parity coverage. MVP-2 roadmap (privacy-purge RTBF component) is now FULLY COMPLETE.

## Change Summary

### Scope & Deliverables

**Slice 2 = Whole-History Content-Scrub**
- Purged concept id/title/tombstone REMOVED from `index.md` and `log.md` across ALL commits (not just live state).
- Removal matches by markdown link-identity (same `_link_identity` used elsewhere), never bare substring.
- Surviving sibling concepts' bullets and prose-mention log entries preserved byte-identical across history.
- Scope limited to `bundle/index.md` and `bundle/log.md`; other files (e.g., surviving bundle bodies) untouched.
- Scrub runs in SAME single `git-filter-repo` pass as Slice 1's whole-file expunge (no second rewrite).
- Live log.md forget-tombstone removal via new `remove_log_entry` function mirroring `remove_index_entry`.
- **Removed**: `_PURGE_RESIDUAL_WARNING` constant and all echo sites — purge now completes RTBF, no residual-leak warning printed.
- **Preserved**: All 6 Slice-1 fail-closed safety rails, Phase-A purity (purge-set resolution), whole-file expunge, index rebuild, irreversibility.

### Delivery & Budget

- **Single PR**: PR #121 (GitHub auto-merge, feature branch chain).
- **Size**: 1208 total changed lines (960 code/test tracked, 84 new untracked test file); 1125 authored lines excluding tasks.md docs.
  - Production code: 206 (git.py) + 47 (log.py) + 103 (main.py) = **356 lines** (32% of authored).
  - Test code: 769 lines (68% of authored) — collision-safety (1.13) + parity (1.3) suites + multi-commit fixture inflation.
- **Budget decision**: 800-line budget exceeded; orchestrator **explicitly accepted `size:exception`** for this PR due to test-dense safety coverage for an irreversible history operation.
- **Fallback documented** but not applied: Slice-1-style two-PR split (git.py callback plumbing PR#1, main.py/log.py wiring PR#2) remains available if future splits are needed; both halves would fit under budget independently.

### Locked Architectural Decisions

1. **Snippet mechanism**: Static `_FILE_INFO_CALLBACK_SNIPPET` constant in `git.py` (no subject-data interpolation), written to temp file, passed to git-filter-repo via `--file-info-callback` argv. Snippet reads purge-set ids from sidecar temp file via `OPENKOS_SCRUB_IDS_FILE` env var — never direct interpolation (injection safety).

2. **File-info-callback gates**: Snippet gates on `filename in (b"bundle/index.md", b"bundle/log.md")` only; reads blob via filter-repo API, removes matching lines, re-inserts, returns new blob_id. For index.md, matches by link-identity only (no anchor matcher); for log.md, matches by link-identity OR `(id: <x>)` structured anchor for tombstone lines. No anchor matcher on index.md to prevent over-scrub of surviving bullets containing anchor patterns in their prose.

3. **Matching precision**: Link-identity matching reimplement ed in snippet bytes (since it runs in filter-repo subprocess), synchronized with Python `bundle.index._link_identity` via explicit parity test (`test_scrub_snippet_parity.py`). First link in line matched, never substring. Line must start with list marker (`* ` or `- `).

4. **One-pass folding**: `--file-info-callback` appended to the same argv list as `--invert-paths --paths-from-file` (Slice 1's expunge); single `_run` subprocess call, no second rewrite.

5. **Fold into purge, no flag**: Scrub ALWAYS runs; not a `--scrub` or `--content-scrub` opt-in flag. Rationale: content-scrub is part of complete RTBF semantics, not a variant mode.

6. **Sidecar temp file for ids**: Purge-set ids written to a temp file (one per line), path passed via env, never in argv or snippet source. Reduces risk of id exposure in process table or subprocess error messages.

7. **No sidecar injection**: `_validate_scrub_identities` rejects empty string, `\n`, `\r`, control chars before any temp-file creation or subprocess invocation — fail-closed guard.

8. **Live log.md cleanup**: New `remove_log_entry(log_text, concept_id) -> (str, int)` in `bundle/log.py`, reuses `_LINK_RE`, `_BULLET_MARKERS`, `_link_identity` from `bundle/index.py` (single matcher source, not fork). Wired at both Phase B sites (GitFinalizeError path + success path) immediately after `_purge_clean_live_index`.

## Verification Outcomes

### Test Coverage
- **Total test count**: 1808 passed (up from 1762 pre-Slice-2, +46 net new tests including correction batch).
- **Correction batch added**: +8 tests for CRITICAL/WARNING defect fixes post-initial verify:
  - **CRITICAL (Resilience)**: Sidecar/snippet/paths temp-file leak of sensitive ids on write failure — fixed by assigning `Path` immediately after NamedTemporaryFile creation, `finally` unlink guaranteed.
  - **WARNING (Resilience)**: OSError from temp-file setup not mapped to GitError — fixed with try/except OSError wrapping, re-raised as plain GitError (not GitFinalizeError, since no subprocess ran yet).
  - **WARNING (Risk)**: index.md anchor over-scrub asymmetry (surviving bullet whose prose contains `(id: <purged>)` could be incorrectly removed) — fixed by applying anchor matcher ONLY to log.md, link-identity only on index.md.
  - **WARNING (Reliability)**: bytes/Python `_identity` divergence on `//` multi-leading-slash links — fixed by normalizing both sides with `lstrip("/")` to strip ALL leading slashes before further canonicalization.
  - **SUGGESTION** (Reliability): Collision-safety test (b) strengthened from substring-count parity to full per-commit blob byte diff (before/after with only known lines removed), matching original tasks.md intent.
- **Spec scenarios**: All 5 spec scenarios (mem 1738) covered with real runtime tests (real git + git-filter-repo, no mocking).
- **Regression coverage**: Slice-1 adapter tests all remain green; `scrub_identities=None` produces byte-identical behavior to pre-Slice-2 expunge.
- **Type checking**: `uv run mypy .` — no issues (129 files).
- **Linting**: `uv run ruff check .` — all checks passed; `uv run ruff format --check .` — 129 files formatted.
- **Dependency lock**: `uv sync --locked` — resolved and checked clean.

### 4R (Code Review) Findings

**PASS WITH WARNINGS**:
- **Risk/Security**: No injection vectors (static snippet + env-sidecar, validation pre-subprocess). Path-blind over-scrub prevented by filename gate + link-identity matching. Temp-file cleanup guaranteed by finally block.
- **Resilience**: CRITICAL sidecar-leak defect fixed in correction batch. OSError mapping corrected. One-pass filter-repo avoids partial-rewrite risk inherent in two-pass designs.
- **Readability**: Parity test (1.3) extracts snippet source via string markers and exec's it — genuinely tests deployed bytes. Bidirectional cross-references added (index.py docstring ↔ git.py snippet). `remove_log_entry` imports (not forks) matcher from index.py with import assertion test.
- **Reliability**: Parity suite covers 11+1 parametrized cases matching bundle.index._link_identity. Collision-safety suite (1.13 a/b/c) walks historical blobs, proves absence in earlier commits, sibling/prose-mention preservation byte-identical, filename gate untouches bundle bodies. No regressions on existing forget/purge tests (165 passed).

**Flagged for Archive**:
- **SIZE WARNING**: 1125 authored lines vs. 800-line review budget (+325, ~41% overrun). Composition: 356 production / 769 test. Orchestrator **explicitly accepted `size:exception`** because overage is test-dense safety coverage (COLLISION-SAFETY + PARITY suites) for an irreversible, sensitive git-history operation, not review-dense logic. Production logic (356 lines) forms one tightly-coupled, non-severable unit (snippet + expunge_paths + log removal + wiring).
- **SUGGESTION**: Collision-safety test (b) was initially substring-count parity; corrected to full blob byte-diff per-commit per tasks.md 1.13(b) original intent (non-blocking).

### Applied to Main

- **Squash commit**: 4dfa70b (PR #121, merged to main, branch deleted).
- **Final state**: All artifacts in place; tasks all checked; verify-report green with no blockers; size exception accepted; ready for archive and MVP-2 closure.

## Merged Spec Artifact

**Canonical spec updated**: `openspec/specs/privacy-purge/spec.md`  
**Changes applied**:
- **ADDED**: Requirement "Whole-History Content-Scrub Of index.md And log.md" (3 scenarios: purge-absence history, sibling/prose-round-trip, scope gate).
- **ADDED**: Requirement "Live log.md Tombstone Cleanup" (1 scenario: prior forget tombstone removed from live log.md).
- **REPLACED**: "Mandatory Residual-Leak Warning" → "Live Index Cleanup After Successful Purge" (MODIFIED requirement, removes warning text, adds "no residual warning is printed" scenario).
- **PRESERVED**: All 6 existing requirements unchanged (Purge Set Resolution, Fail-Closed Safety Rails, Whole-History Expunge, Index Cleanup Delete-And-Rebuild, Irreversibility).

**Result**: Canonical spec now reflects Slice 2 behavior as shipped — purge completes RTBF with no residual-leak warning.

## Artifact Traceability

All SDD phase artifacts retrieved and persisted:

| Artifact | Engram ID | Location | Status |
|----------|-----------|----------|--------|
| Proposal | 1737 | sdd/privacy-purge-history-scrub/proposal | Captured |
| Spec (delta) | 1738 | sdd/privacy-purge-history-scrub/spec | Captured, merged to canonical |
| Design | 1740 | sdd/privacy-purge-history-scrub/design | Captured |
| Tasks | 1741 | sdd/privacy-purge-history-scrub/tasks | Captured, all 30+ checkboxes [x] |
| Verify-report | 1747 | sdd/privacy-purge-history-scrub/verify-report | Captured, PASS WITH WARNINGS |
| Archive-report | (this) | sdd/privacy-purge-history-scrub/archive-report | Saved to Engram + filesystem |

## MVP-2 Completion Statement

**Privacy-Purge RTBF Roadmap Completion**:
- Roadmap items 63–71 (right-to-be-forgotten feature deliverables, reference-aware forget + purge with RTBF): ALL SHIPPED.
- Slice 1 (PR #114, c492dba): reference-aware forget with tombstones, purge Phase A, whole-file expunge + live index cleanup + warning.
- Slice 1 (PR #115, c492dba): reference-aware forget cascade (`--scope source` + orphan-after-delete), purge cascade.
- Slice 2 (PR #121, 4dfa70b, THIS): history content-scrub of index.md/log.md, live log.md tombstone cleanup, removed residual-leak warning → **RTBF now GENUINE**.

**MVP-2 is FULLY COMPLETE**: Right-to-be-forgotten is no longer a letter-of-roadmap promise; it is delivered as real, verified behavior with collision-safety and parity test coverage for the irreversible history scrub.

## Deferred / Not In Scope

**Technical debt noted in proposal**:
- Shared Phase-A/reference-aware helper extraction between forget and purge (still deferred, no MVP impact).
- Committed `.openkos/fts.db` leak vector (deferred to future slice, no RTBF impact).

**No follow-up SDD needed** — privacy-purge RTBF is complete and verified. Maintenance/polish can proceed via normal issue/PR workflow.

## Sign-Off

- **Phase**: SDD archive
- **Executor**: sdd-archive (executor mode, not orchestrator)
- **Change**: privacy-purge-history-scrub (Slice 2)
- **Mode**: hybrid (Engram + filesystem archive folder)
- **Spec sync**: Complete (delta → canonical)
- **Archive folder**: openspec/changes/archive/2026-07-23-privacy-purge-history-scrub/
- **Ready for**: Change cycle closure. MVP-2 roadmap complete. Next phase: maintenance, follow-up issues.
