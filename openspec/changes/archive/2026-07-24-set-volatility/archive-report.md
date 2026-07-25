# Archive Report: set-volatility (#140)

**Date Archived**: 2026-07-24  
**Proposer Observation ID**: #1888  
**Spec Observation ID**: #1889  
**Design Observation ID**: #1890  
**Tasks Observation ID**: #1891  
**Verify Report Observation ID**: #1893  
**PR**: #167  
**Status**: CLOSED, MERGED TO MAIN

---

## Summary

The `set-volatility` write verb has been successfully implemented, verified (PASS WITH WARNINGS), and merged to main. This capability enables safe comment-preserving edits to `type_tiers:` in `openkos.yaml`, completing the slice that pairs with `suggest-volatility` (the read-only advisor).

---

## What Shipped

### Core Capability: `set-volatility` Write Verb

**Command**: `openkos set-volatility <ConceptType> <tier>`

- **Purpose**: Safe write layer for per-concept volatility tier configuration
- **Implementation**:
  - Pure comment-safe text-surgery core: `config.set_type_tier(yaml_text, concept_type, tier) -> str`
  - Text-in/text-out, no YAML round-trip, no new dependencies
  - Validation: tier ∈ {static, slow, volatile}; ConceptType ∈ full 10-entry REGISTRY incl. `Source`
  - Fail-closed on unparseable shapes (inline flow-mapping, malformed blocks, tab indents, etc.)
  - Preview + confirm gate (mirrors `relate` precedent)
  - Idempotent no-op if already at requested tier
  - Auto-commits `openkos.yaml` on successful write

### Modified Capability: `suggest-volatility` Hint Update

- **Changed**: trailing hint in suggest-volatility output
- **From**: "hand-edit `type_tiers:` in `openkos.yaml`"
- **To**: "run `openkos set-volatility <ConceptType> <tier>`"
- **Behavior**: remain zero-write; all 3 original scenarios preserved; hint text updated only

---

## Safety Design (Load-Bearing)

1. **Text Surgery, Not YAML Round-Trip**
   - Core `config.set_type_tier()` operates on `yaml_text.splitlines(keepends=True)`
   - Preserves every byte outside the target entry (comments, formatting, blank lines)
   - Verified by fixture-based testing (e.g., test case (a): existing entry with trailing comment untouched)

2. **Fail-Closed on All Un-Editable Shapes**
   - Inline flow-mapping: `type_tiers: {Person: volatile}`
   - Multiple type_tiers headers (duplicate key)
   - Non-mapping scalar values (type_tiers: foo | [] | null)
   - Tab-indented content
   - Inconsistent entry indentation
   - Duplicate type entry within block
   - All cases: raise `ValueError` before file write, caught by CLI exception handler

3. **Pre-Write Full REGISTRY Validation**
   - Tier syntax validated in CLI (clear listing message if invalid)
   - ConceptType validated against full 10-entry REGISTRY (includes `Source`), NOT just `CLASSIFIABLE_TYPES`
   - Mismatch: exit 1 with stderr listing valid options; no file write
   - Verified by test cases `test_invalid_tier_rejected_no_write_no_commit` and `test_invalid_concept_type_rejected_lists_valid_types`

4. **Atomic Write via `fsio.write_atomic`**
   - Write uses existing atomic helper (path-rename) to guard against partial/corrupted files
   - Consistent with CLI convention (shared with `relate` and other write verbs)

5. **Commit Integrity**
   - Auto-commit only after successful write (Phase B in design)
   - Idempotence checked BEFORE core call: if already at target tier, no core call, no write, no commit
   - Commit message: `openkos: set-volatility <ConceptType> -> <tier>`
   - Verified by tests `test_idempotent_already_set_tier_is_noop` and `test_successful_write_lands_and_autocommits`

---

## Scope

### IN (Implemented)

- `set-volatility` CLI command (two-phase: read+validate, preview+confirm, write+commit)
- Pure text-surgery core `config.set_type_tier()` in config.py
- Strict write-time validation (tier vocabulary, ConceptType REGISTRY, config shape)
- Preview + confirm gate (mirrors `relate`)
- Idempotent no-op behavior (exit 0 if already at tier; no commit)
- Auto-commit on successful write (`_autocommit` helper)
- Update `suggest-volatility` hint to point at new `set-volatility` command

### OUT (Explicit Non-Goals, Deferred or Rejected)

- `suggest-volatility --apply` interactive walk (core extracted for future reuse, but apply itself deferred)
- `ruamel.yaml` or other new YAML dependencies (rejected; text surgery chosen instead)
- Any changes to volatility inference/typing or `concept-volatility` (read semantics untouched)
- Changes to other CLI verbs or `type_tiers:` read paths

---

## Verification Summary

**Verdict**: PASS WITH WARNINGS (0 CRITICAL, 2 WARNINGS)

### Test Coverage

- **Test Suite**: 2051 passed in 106-114s (strict TDD, all red->green)
- **Quality Gates**:
  - `uv run ruff check .` → All checks passed
  - `uv run ruff format --check .` → 134 files already formatted
  - `uv run mypy .` → Success: no issues found in 134 source files

### Requirement Coverage

- **Requirements**: 9/9 (8 new in volatility-config + 1 modified in volatility-suggestion)
- **Scenarios**: 16/16 (all scenarios from both specs exercised)

### Load-Bearing Verification

1. **Comment-Safety (VERIFIED)**
   - Case (a): existing entry update preserves trailing comments
   - Case (b): new entry insertion under existing block, rest untouched
   - Case (c): absent/fully-commented header creation, rest of file untouched
   - Fixtures include real comments: `# rarely changes`, `# model: gemma3`, etc.

2. **Fail-Closed (VERIFIED at core level, 1/6 at CLI level)**
   - All 6 fail-closed shapes tested and raise `ValueError` at core level
   - 1 shape (inline flow-mapping) verified end-to-end at CLI/file-byte level: `test_unparseable_config_shape_fails_closed`
   - Remaining 5 shapes verified at core only; CLI wiring is structurally generic (single `except (OSError, ValueError)` block with no shape branching), so risk is low but not directly per-shape proven
   - **WARNING**: CLI-level byte-identical assertion could be stronger (suggested follow-up: add remaining 5 CLI-level tests)

3. **Full REGISTRY Validation (VERIFIED)**
   - `Source` included in REGISTRY and in valid_types derivation (code inspection: `types.REGISTRY` line 36-47)
   - `Source` appears in error message listing for negative tests
   - **WARNING**: No positive end-to-end test invokes `set-volatility Source <tier>` as a successful write; acceptance is proven by code but not exercised

4. **Validation Behavior (VERIFIED)**
   - Invalid tier listed with options: `test_invalid_tier_rejected_no_write_no_commit`
   - Invalid ConceptType listed with options including `Source`: `test_invalid_concept_type_rejected_lists_valid_types`
   - Both exit != 0 with no file change

5. **Preview/Confirm/Commit (VERIFIED)**
   - Preview format verified: `test_preview_line_format_printed_before_confirm`
   - Confirm accept writes: `test_interactive_accept_writes`
   - Confirm decline no-op: `test_interactive_decline_writes_nothing`
   - `--auto` bypass: `test_auto_skips_the_prompt_and_writes`
   - Non-TTY refusal: `test_non_tty_without_auto_refuses`
   - Commit message with `openkos:` prefix: `test_successful_write_lands_and_autocommits`

6. **Idempotence (VERIFIED)**
   - Already-at-tier no-op: `test_idempotent_already_set_tier_is_noop` (exit 0, no write, no commit)
   - Explicit override of REGISTRY default treated as real write: `test_explicit_override_equal_to_registry_default_is_real_write`

7. **Hint Update (VERIFIED)**
   - `suggest-volatility` output changed from old hint to new `set-volatility` command
   - Diff is exactly 1 line in test_suggest_volatility.py
   - Full suite (17/17 suggest-volatility tests) confirm no regression

### Warnings (Non-Blocking)

1. **Coverage Gap: 5 of 6 Fail-Closed Shapes at CLI Level**  
   Only 1 fail-closed shape (inline flow-mapping) is proven end-to-end at the file-byte-identical level. The other 5 (malformed block, non-mapping scalar, tab-indent, inconsistent indent, duplicate entry) are verified at the core level; CLI wiring is structurally shape-agnostic (single exception handler), so behavioral risk is low. Recommendation (not blocking): add per-shape CLI-level tests in a follow-up if reviewer risk tolerance requires it.

2. **Coverage Gap: `Source` Acceptance Not Exercise-Tested**  
   `Source`'s acceptance is provable by code inspection (REGISTRY-derived valid_types, confirmation in error listing). No end-to-end test runs `set-volatility Source <tier>` as a successful positive write. Recommendation: add a `Source`-tier positive test in follow-up if desired for full exercise coverage.

---

## Merged Specs

### New Capability

- **Spec**: `openspec/specs/volatility-config/spec.md` (CREATED)
- **Requirements**: 8 (command shape, tier validation, type validation, comment-safe editing, fail-closed behavior, preview/confirm, idempotence, auto-commit)
- **Scenarios**: 9 (success + error cases + edge cases)

### Modified Capability

- **Spec**: `openspec/specs/volatility-suggestion/spec.md` (UPDATED)
- **Changed**: 1 requirement (Workspace-Gated, Read-Only Per-Type Suggestion) — hint text updated
- **Preserved**: 3 other requirements (fail-closed parsing, Ollama error handling, deterministic input selection)
- **Scenarios**: 3 scenarios in modified requirement preserved; all other scenarios in other requirements unchanged

---

## Files Changed

### Production Code
- `src/openkos/config.py` — added `set_type_tier()` pure text-surgery core
- `src/openkos/cli/main.py` — added `set-volatility` command + updated `suggest-volatility` hint (line 4519)

### Test Code
- `tests/unit/test_config.py` — 9 test cases for `set_type_tier()` (3 edit cases, idempotent, 6 fail-closed)
- `tests/unit/cli/test_set_volatility.py` — 12 test cases for CLI (mirroring `test_relate.py` structure)
- `tests/unit/cli/test_suggest_volatility.py` — 1 line modified (hint assertion)

### No Changes To
- `pyproject.toml`, `uv.lock` (no new dependencies)
- `read_config()`, volatility inference logic, type_tiers read semantics
- Any other CLI verbs or configuration paths

---

## PR Reference

**PR #167**: feat(cli): purge auto-commit + dangling-ref detection (#141) + vectors.db awareness (#142) (#155)

Merged to main on 2026-07-24. Git commit hash and branch info in upstream repository.

---

## Cycle Completion

**SDD Phases Completed**:
1. ✅ Proposal (#1888) — intent, scope, capabilities, success criteria
2. ✅ Spec (#1889) — 9 requirements, 16 scenarios, domains (volatility-config NEW, volatility-suggestion MODIFIED)
3. ✅ Design (#1890) — technical approach, text-surgery algorithm, CLI flow, open questions resolved
4. ✅ Tasks (#1891) — 4 implementation phases (core, CLI, hint, quality gate), all 20 tasks completed
5. ✅ Apply — implementation merged PR #167 to main
6. ✅ Verify (#1893) — PASS WITH WARNINGS (2 non-critical coverage gaps)
7. ✅ Archive — specs merged, artifacts archived

**Archive Status**: All delta specs successfully merged into main `openspec/specs/`. Change ready for new feature iteration or follow-up work (e.g., `suggest-volatility --apply` interactive walk, reusing extracted `set_type_tier()` core).

---

## Traceability

| Artifact | Observation ID | Type |
|----------|-------|------|
| Proposal | #1888 | proposal |
| Specification | #1889 | spec |
| Design | #1890 | design |
| Tasks | #1891 | tasks |
| Verification Report | #1893 | verify-report |
| Archive Report | (this file) | archive-report |

