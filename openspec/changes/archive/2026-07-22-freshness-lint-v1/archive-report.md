# Archive Report: freshness-lint-v1 (Slice 1 of 4-slice volatility arc)

**Archived**: 2026-07-22  
**Status**: Complete and closed  
**Delivery**: PR #108, commit 95b7d10 (2 commits squashed, incl. default_tier rename)

## Summary

Slice 1 of the planned 4-slice volatility-aware lint arc has been successfully completed, tested (1337/1337 tests pass), verified (PASS WITH WARNINGS — review-budget overrun already accepted via size:exception), and archived. The change introduced a new canonical capability `concept-volatility` (capability #21 in the spec store), merged the lint capability with volatility-aware window resolution, accepted ADR-0007 (Volatility Taxonomy), and moved this change folder to the archive.

## Artifacts & Traceability

All SDD artifacts have been persisted to Engram for traceability:

| Artifact | Observation ID | Type | Purpose |
|----------|---|---|---|
| Proposal | #1556 | architecture | Initial scope, intent, and approach for S1 |
| Spec | #1557 | architecture | Full spec for new `concept-volatility` + delta for `lint` MODIFIED requirement |
| Design | #1558 | architecture | Technical design, per-tier windows, resolution algorithm, file changes |
| Tasks | #1560 | architecture | 28/28 tasks across 7 phases (TDD RED-GREEN-REFACTOR) |
| Apply-Progress | (referenced by verify-report) | architecture | Implementation proof: commit d0b7e79, 1357 insertions+60 deletions, 11 code files touched |
| Verify-Report | #1568 | architecture | Independent verification: PASS WITH WARNINGS, 1337 tests, 4R sweep recommended, review-budget overrun accepted |

## Change Delivery

**PR #108 — freshness-lint-v1 (Merged)**
- Commit SHA: 95b7d10
- Branch: feat/freshness-lint-v1 (squashed to main)
- Status: Merged and verified
- Changed files: 11 (code + tests + canonical specs + ADR)
- Test results: 1337/1337 passed
- Linting: All checks passed (ruff, mypy)
- Size: 873 changed lines (code/test/spec subset); 1357 total with planning docs
- Review: Full 4R sweep (review-reliability, review-risk, review-resilience, review-readability) due to >400-line budget; size:exception approved

## Spec Merge Outcome

### 1. New Canonical Capability: `concept-volatility`

Created: `openspec/specs/concept-volatility/spec.md` (capability #21)  
Source: `openspec/changes/freshness-lint-v1/specs/concept-volatility/spec.md` (full spec, no prior canonical spec exists)  
Action: Copied verbatim (no prior spec to merge with)

**Requirements**: 4 requirements / 6 scenarios, all covered by passing tests:
- Fixed Three-Tier Volatility Taxonomy (static/slow/volatile only)
- Per-Concept `volatility` Frontmatter Override
- Per-Type Default Volatility Registry
- Deterministic, Never-Raising Window Resolution

**Implementation**:
- Added `default_volatility: str` to `ObjectType` frozen dataclass
- Populated all 10 registry entries (static: Place/Event/Decision/Source; slow: Concept/Entity/Person/Organization; volatile: Procedure/Project)
- Added `VOLATILITY_TIERS` frozenset and `TYPE_TO_DEFAULT_VOLATILITY` dict
- Implemented `VolatilityWindows` dataclass and `resolve_windows()` / `window_for_doc()` pure functions
- Config: Per-tier windows in `openkos.yaml` (`volatility_windows: {slow: 90d, volatile: 7d}`) + legacy `freshness_window` as ultimate fallback

### 2. Modified Canonical Capability: `lint`

File: `openspec/specs/lint/spec.md`  
Status: **Already merged during apply phase** — no additional action needed  
Verification: Canonical spec already reflects the delta exactly:

**MODIFIED Requirement**: Stale-Stamp Scan
- Now resolves per-doc window via volatility precedence (per-concept override → per-type default → global fallback)
- `static`-tier concepts NEVER flagged, regardless of stamp age
- Added 8 new scenarios (override wins, type default wins, degrade paths, never raises)
- Test coverage: exhaustive 11-row precedence table in `test_window_for_doc_precedence_table`

**Non-Goals Update**: Replaced "volatility classification via the `freshness` field (lint never reads it)" with orthogonal-skip-flag wording clarifying that `freshness` remains a binary snapshot flag, orthogonal to volatility.

**Implementation**:
- Extended `LintDoc` with `type: str` and `volatility: str` fields
- Migrated `check_stale_stamps` from single `window: timedelta` to `windows: VolatilityWindows`
- Updated CLI to call `resolve_windows(cfg)` and surface notices
- Ingest unchanged and byte-stable (no `volatility` key emitted)

## Architecture Decisions

ADR-0007 — Volatility Taxonomy and Volatility-Aware Freshness Windows  
File: `docs/adr/0007-volatility-taxonomy.md`  
**Status (archive phase)**: Flipped from **Proposed** → **Accepted**

**Decision summary**:
- Fixed three-tier taxonomy (static/slow/volatile) as stable interface
- Per-type default tier on registry (frozen ObjectType attribute)
- Absent-by-default `volatility` frontmatter override (ingest stays byte-stable)
- Pure, never-raising window resolution with fixed precedence

**Updated index**: `docs/adr/README.md` row 0007 now shows **Accepted** (date: 2026-07-22)

## File Changes Summary

| Category | Files | Action |
|----------|-------|--------|
| Registry | `src/openkos/model/types.py` | Added `default_volatility` attr + VOLATILITY_TIERS + TYPE_TO_DEFAULT_VOLATILITY |
| Config | `src/openkos/config.py`, `templates/openkos.yaml.template` | Added `DEFAULT_VOLATILITY_WINDOWS`, read `volatility_windows`, config template block |
| Lint | `src/openkos/lint.py`, `src/openkos/cli/main.py` | Extended LintDoc, added VolatilityWindows, window_for_doc, resolve_windows, updated check_stale_stamps + CLI wiring |
| Tests | 5 files | 28 tasks across 7 TDD phases (RED-GREEN-REFACTOR), exhaustive table tests |
| Canonical Specs | `openspec/specs/lint/spec.md` | MODIFIED requirement + Non-Goals Update (already merged during apply) |
| New Canonical Spec | `openspec/specs/concept-volatility/spec.md` | NEW capability (created at archive time) |
| ADR | `docs/adr/0007-volatility-taxonomy.md`, `docs/adr/README.md` | Created ADR, indexed in README; flipped to Accepted at archive time |

## Verification Evidence

**Task Completeness**: 28/28 tasks checked [x]  
**Test Suite**: 1337/1337 tests passed  
**Linting**: ruff check, ruff format --check, mypy all clean  
**Spec Coverage**: 5 requirements / 14 scenarios, all mapped to passing tests  
**No-Drift**: Changed files limited to exactly the expected set; no over-reach  
**Contract Preservation**: lint.py read-only/never-fail/deterministic-clock v0 contract preserved; ingest byte-stable  
**Review**: Full 4R sweep recommended (>400 lines); size:exception approved

**Verdict**: PASS WITH WARNINGS (review-budget overrun already flagged and accepted)

## Deferred Slices (Planned 4-Slice Arc)

- **S2**: LLM-suggested per-type windows (`suggest-windows`)
- **S3**: Contradiction detection (`contradictions`)
- **S4**: Guided `reconcile` write verb

These slices remain in the roadmap and will be planned as separate SDD cycles.

## Archive Completion

**Original Change Folder**: `openspec/changes/freshness-lint-v1/` — **REMOVED** (not copied; fully moved)  
**Archived To**: `openspec/changes/archive/2026-07-22-freshness-lint-v1/`  
**Archive Contents**:
- proposal.md ✅
- design.md ✅
- tasks.md ✅ (28/28 complete)
- verify-report.md ✅
- specs/concept-volatility/spec.md ✅ (new canonical spec)
- specs/lint/spec.md ✅ (delta)
- archive-report.md ✅ (this file)

**SDD Cycle**: Complete and closed. Change is fully planned, implemented, tested, verified, and archived. Ready for the next change in the volatility arc or independent changes.
