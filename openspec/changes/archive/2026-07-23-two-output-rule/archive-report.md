# Archive Report: two-output-rule

**Date**: 2026-07-23  
**Change**: `two-output-rule` (query --save)  
**Status**: ARCHIVED — MVP-2 COMPLETE

## Executive Summary

The `two-output-rule` change — the **LAST UNWIRED MVP-2 DELIVERABLE** — has been successfully implemented, verified, and archived. This change automates the second output of a query by introducing an opt-in `--save` flag that files the printed cited answer back into the bundle as a new OKF concept. With this change merged and verified, **MVP-2 is now 100% COMPLETE** as specified in roadmap.md:63-71.

**Merged**: PR #122 (squash b4b5e7b)  
**All tests pass**: 1834 passed (up from 1809 baseline, +25 new tests)  
**Verification verdict**: PASS (no CRITICAL issues)

## Change Scope

### Summary
- **Capability**: query-command (query-answer pipeline)
- **Feature**: Opt-in `--save` flag on `openkos query` command
- **Behavior**: Files the cited answer as a new derived OKF concept with:
  - body = answer text
  - title = question (or `--title` override)
  - description = question (or `--description` override)
  - type = "Concept" (or `--type` override)
  - provenance = cited concept ids
  - sensitivity = high-water-mark of cited concepts (fail-closed to confidential on unreadable/unparseable citations)
- **Purity**: Default `--save` OFF; without it, query behavior is byte-identical to pre-change (read-only)
- **Gating**: Preview + confirm + non-TTY gate (reusing ingest's pipeline)
- **No auto-reindex**: User must run `openkos reindex` after filing (hint printed)

### Files Modified
1. `src/openkos/model/okf.py` — added `related_note` kwarg to `build_concept` (default preserves ingest byte-identity)
2. `src/openkos/cli/main.py` — added `--save`, `--title`, `--description`, `--type`, `--auto` flags; added `_FiledAnswerPlan` + `_stage_filed_answer` helper; gated save block in query command
3. `openspec/specs/query-command/spec.md` — updated Non-Goals; added 5 new requirements for --save behavior

### Test Coverage
- Unit tests for `_stage_filed_answer` (provenance, defaults, overrides, zero-citations, collision, sensitivity fold, high-water-mark confidential)
- Unit tests for `build_concept` signature change (byte-identical golden, custom related_note render)
- Integration tests for query --save (purity, save writes, overrides, zero-citations refuse, preview/confirm/--auto gate, non-TTY refuse, slug collision, reindex hint)
- Correction-batch tests (malformed YAML fail-closed to confidential, missing file fail-closed to confidential, cfg.review false gate)
- Full-suite verification: 1834 passed, mypy clean, ruff clean

## Architecture Decisions (Locked)

1. **Opt-in `--save` default OFF** — query purity preserved; only explicit user action files a concept. Entire save block gated behind `if save:` and placed AFTER existing answer/citations print and AFTER no_match early return.

2. **Title/Description = Question** — Filed concepts are derived *from* the question, so the question itself is the canonical title/description. User can override via `--title`/`--description` for custom naming (e.g., summarization).

3. **Provenance = Cited Concept IDs** — Establishes the dependency graph for forget/purge cascades; filed concept is a real first-class node, not a note-dump.

4. **Sensitivity = High-Water-Mark** — Propagates the most-restrictive sensitivity of cited concepts to the filed concept. **CRITICAL FIX** (correction batch): Any unreadable or unparseable cited concept now fails *closed* to confidential (not skipped). This prevents under-classification when a concept's frontmatter cannot be read/parsed at save time.

5. **Reuse Ingest's Builder + Pipeline** — `build_concept` (with new `related_note` kwarg), `_stage_filed_answer` (mirrors `_stage_derived_objects`), confirm gate (TTY + cfg.review gating), Phase-B write order (create-only write_exclusive, then catalog). Consistent with ingest's proven patterns.

6. **Slug Collision Detection** — Phase A checks `path.exists()`, refuses on collision (exit 1). Mirrors ingest's create-only reconciliation.

7. **Zero-Citations Refuse** — A soureless concept is not a real derived node. `build_concept` requires non-empty provenance. Edges-only constraints remain in the graph. Query --save with zero citations exits 1 with a clear message.

8. **No Auto-Reindex** — Filed concept is not immediately indexed; user must explicitly run `openkos reindex` to make it retrievable. Hint is printed on success. Separates write from indexing concerns (consistent with initial design philosophy).

## Review and Verification

### Bounded Review Outcome
- **Review gate**: Passed (approval/allow receipt)
- **Scope**: 6 files, +833/-47 lines
- **Risk tier**: Medium (1 dominant-risk lens: reliability, behavior/determinism, zero-citations guard)
- **Verdict**: PASS

### Critical Fix in Correction Batch (Phase 6)
**Issue**: Citation re-read crashed on malformed YAML → citation frontmatter parse exception → uncaught traceback.  
**Fix**: `_stage_filed_answer` now catches `Exception` broadly (mirrors `_assemble_context` in retrieval/answer.py) and folds `combine_sensitivity(acc, "confidential")` on any read/parse failure. Unreadable or unparseable citations now fold sensitivity to **confidential** (fail-closed), consistent with the project's pervasive "cannot verify -> confidential" stance.  
**Test evidence**: `test_stage_filed_answer_malformed_frontmatter_folds_confidential`, integration test confirms clean exit (0) and confidential sensitivity on malformed YAML.  
**Spec updated**: "Sensitivity Is The High-Water-Mark" requirement + "Unreadable or unparseable citation folds to confidential" scenario now correctly reflect shipped behavior.

### Spec Corrections (Phases 1 + Archive-Time)
1. **Zero-Citations Scenario (Phase 1.2)**: Changed from "fall back to default, filing still proceeds" to "REFUSE, exit non-zero, no write" — aligns with design decision (build_concept requires non-empty provenance).

2. **Non-Goals Line (Archive-Time)**: Reversed the exclusion of "automated re-filing of an answer back as a concept" by gating it behind `--save`. Updated Non-Goals to: "automated re-filing without `--save`; LLM-generated titles; mandatory `--title`." (The latter three remain out-of-scope: no auto re-filing when --save absent, no LLM title generation, no mandatory --title override.)

### Purity Verification
- `query` without `--save` produces byte-identical stdout + stderr vs baseline
- No new file/index/log entry created when `--save` is absent
- Save block starts AFTER `typer.echo(result.answer)` + citations AND after `no_match_cause` early return
- Test `test_query_purity_without_save_is_byte_identical` asserts exact string equivalence

### Ingest Byte-Identity Verification
- `build_concept` gained `related_note: str = "source this was extracted from"` trailing kwarg
- Default value is identical to the old hardcoded phrase → omitting the kwarg is byte-identical
- Ingest never passes `related_note` (grep-confirmed no other callers)
- Ingest test suite (76 tests) passes unchanged
- Pre-existing golden `test_build_concept_output_byte_identical_regression` remains unmodified and passes

### Tasks vs Reality
All 68 implementation checkboxes in openspec/changes/two-output-rule/tasks.md are marked complete ([x]):
- Phase 1: Spec correction (1.1-1.2) ✓
- Phase 2: build_concept parameterization (2.1-2.4) ✓
- Phase 3: _stage_filed_answer helper (3.1-3.10) ✓
- Phase 4: Wire --save into query (4.1-4.13) ✓
- Phase 5: Full-suite verification (5.1-5.2) ✓
- Phase 6: Bounded correction batch (6.1-6.8) ✓

## Test Results

**Full Suite**: `uv run pytest -q` → 1834 passed (up from 1809 baseline at start)
- +21 new tests in Phase 4 (test_query_save.py)
- +4 new tests in Phase 6 (malformed/missing citation, review false gate)
- No regressions in existing ingest or query test suites

**Type Check**: `uv run mypy .` → Success (130 source files, no issues)

**Linting**: `uv run ruff check .` → All checks passed

**Formatting**: `uv run ruff format --check .` → 130 files already formatted

## Spec Merge Summary

**Canonical Spec**: `openspec/specs/query-command/spec.md`

### Changes Applied
1. **Non-Goals line updated**: Replaced "automated re-filing of an answer back as a concept" with "automated re-filing without `--save`; LLM-generated titles; mandatory `--title`."

2. **5 new requirements added** (after "Dense-Unavailable Runs Degrade And Hint At Reindex"):
   - Read-Only Purity Without `--save`
   - `--save` Files The Cited Answer As A New Concept
   - Sensitivity Is The High-Water-Mark Of Cited Concepts (includes fail-closed unreadable/unparseable scenario)
   - Preview, Confirm, And Non-TTY Gate For `--save`
   - Filed Concept Is Not Auto-Reindexed

3. **All other requirements preserved** (Workspace Gate, Happy-Path Answer Rendering, No-Match Is Not An Error, FTS/Graph-Unavailable Degradation, Docstring Update, --limit Option, LLM And Index Error Mapping, Citations Reflect The Answer Exactly, Stderr Retrieval Summary, Build-Time Skip Notices, Dense-Unavailable Degradation).

## Traceability

### SDD Artifacts (Engram)
| Artifact | ID | Type |
|----------|----|----|
| Proposal | 1768 | architecture |
| Spec Delta | 1769 | architecture |
| Design | 1771 | architecture |
| Tasks | 1773 | architecture |
| Verify-Report | 1777 | architecture |

### Implementation Evidence
- **PR #122**: `git log --oneline` shows `b4b5e7b` (squash merge of feat/two-output-rule)
- **Commits**:
  - ccfdcb1 docs/spec correction
  - d4227b7 okf.py related_note kwarg
  - ec8c718 query --save wiring
- **Branch**: feat/two-output-rule (now merged to main)

## Remaining Deferred Work (NOT MVP-2)

### Phase-A Helper Extraction
Shared `forget`/`purge` Phase-A logic (sensitivity fold, re-read citation frontmatter, fail-closed on unreadable) is now implemented in both `_assemble_context` (retrieval/answer.py) and `_stage_filed_answer` (cli/main.py). Future refactor can extract to a shared `_fold_sensitivity_from_citations(...)` helper. Deferred to Phase-A reuse/DRY pass.

### MVP-3 (Next Arc)
- **MCP Server**: Expose ingest/query/reindex/forget/purge as MCP tools for Claude/browser integration
- **Import/Export**: YAML snapshot + restore for bundle portability
- **REST API**: HTTP gateway for remote bundle access
- **Memory Projections**: Timeline slices, concept graphs, sensitivity-aware filtering

MVP-3 is the next major arc after this archive. MVP-2 itself is now feature-complete.

## Status: ARCHIVED

**Location**: `openspec/changes/archive/2026-07-23-two-output-rule/`

**Contents**:
- ✅ proposal.md
- ✅ specs/query-command/spec.md (delta)
- ✅ design.md
- ✅ tasks.md (all 68 checkboxes complete)
- ✅ archive-report.md (this file)

**Canonical Specs Updated**:
- ✅ `openspec/specs/query-command/spec.md` — Non-Goals reversed, 5 new requirements added

## MVP-2 Completion Statement

**MVP-2 is now 100% COMPLETE.**

Every deliverable from roadmap.md:63-71 has been shipped:
- query command (answer + citations) ✓ (sdd-query)
- ingest command (phase A + B, dry-run, freeze, forget/purge cascade) ✓ (sdd-ingest, sdd-ingest-cascades)
- lint command (bundle validation) ✓ (sdd-lint)
- reindex command (FTS/graph rebuild, bundle introspection) ✓ (sdd-reindex)
- status command (workspace overview) ✓ (sdd-status)
- **query --save** (filed answer, derived concept, opt-in) ✓ (sdd-two-output-rule) **← This change**

All core CLI operations, reference-aware cascades, and the compounding-knowledge loop are now functional and production-ready.

---

**Archive Report Generated**: 2026-07-23 by sdd-archive executor  
**SDD Cycle**: Closed  
**Next**: MVP-3 arc (MCP server, import/export, REST API, memory projections)
