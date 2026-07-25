# Archive Report: init-model-picker (Slice B, Issue #128)

**Status**: ARCHIVED (CLOSED)
**Date**: 2026-07-25
**Change**: init-model-picker (Slice B of GitHub issue #128)
**Result**: Issue #128 is now CLOSED

## Summary

Interactive model picker for `openkos init` has been fully implemented, verified, reviewed, and merged to main across two sequential PRs (Slice B-i and B-ii). The change replaces free-text model prompts with a numbered picker over installed chat models, properly excluding embedding models by family classification. Both sub-slices have passed strict TDD verification with 100% spec compliance (27/27 scenarios), full test pass (2106 tests), and zero critical or blocking findings.

## Merged PRs

- **PR #172**: init-model-picker B-i — List models widening + doctor adaptation
  - Merged to main at commit 0652a64
  - Changes: Widened `OllamaClient.list_models()` to expose per-model family via new `InstalledModel` dataclass
  - Doctor checks 3/4/5 adapted to new shape (behavior-preserving)
  - Status: MERGED ✅

- **PR #173**: init-model-picker B-ii — Interactive model picker
  - Merged to main (after B-i)
  - Changes: Added `_pick_chat_model()` with numbered TTY prompt, embedding-model exclusion, graceful degradation
  - Status: MERGED ✅

## Artifact Tracking

All SDD artifacts persisted to both Engram (for audit trail) and openspec folder:

| Artifact | Engram ID | Topic Key | Status |
|----------|-----------|-----------|--------|
| Proposal | 1908 | sdd/init-model-picker/proposal | ✅ ARCHIVED |
| Spec (main + 3 deltas) | 1909 | sdd/init-model-picker/spec | ✅ ARCHIVED |
| Design | 1910 | sdd/init-model-picker/design | ✅ ARCHIVED |
| Tasks | 1911 | sdd/init-model-picker/tasks | ✅ ARCHIVED |
| Apply Progress | 1912 | sdd/init-model-picker/apply-progress | ✅ (reference only) |
| Verify Report | 1913 | sdd/init-model-picker/verify-report | ✅ ARCHIVED |
| Archive Report | — | sdd/init-model-picker/archive-report | ✅ ARCHIVED (this file) |

## Specs Merged to Main

All three affected domains have been updated with the new requirements:

### llm-client (`openspec/specs/llm-client/spec.md`)
- **MODIFIED**: "List Installed Models" — now returns per-model family via `details.family` field
- **ADDED**: "Family-Based Embedding Model Classification" — helper to distinguish embedding vs. chat models by family

### workspace-init (`openspec/specs/workspace-init/spec.md`)
- **MODIFIED**: "Static openkos.yaml Template" — precedence now includes interactive picker when preconditions hold
- **ADDED**: "Interactive Model Picker Over Installed Chat Models" — numbered list with default marked, selection persisted
- **ADDED**: "Graceful Degradation When Ollama Unreachable Or No Chat Models" — fallback to typed prompt, no hard-fail, workspace still created
- **ADDED**: "Non-Interactive Paths Bypass The Picker" — `--model` flag wins; non-TTY silent default
- **ADDED**: "Embedding Models Excluded From Picker Candidates" — embedding models filtered from picker list

### doctor-command (`openspec/specs/doctor-command/spec.md`)
- **ADDED**: "Doctor Behavior Unchanged By list_models() Contract Widening" — checks 3/4/5 report identical outcomes on new shape

## Verification Summary

**Verdict**: PASS ✅

- **Spec Compliance**: 27/27 scenarios across 8 requirements (3 domains)
  - llm-client: 2 requirements, 7 scenarios ✅
  - doctor-command: 1 requirement, 3 scenarios ✅
  - workspace-init: 5 requirements, 17 scenarios ✅
- **Task Completion**: 20/20 tasks complete (10 B-i, 10 B-ii)
- **Tests**: 2106 passed (2099 B-i baseline + 7 net new), exit 0
- **Quality Gate**: ruff check ✅, ruff format ✅, mypy ✅
- **Critical Findings**: 0
- **Blockers**: 0

## Review Findings

**B-i (PR #172)**: Reliability lens
- Result: 0 findings ✅

**B-ii (PR #173)**: Resilience lens
- Result: 1 WARNING (bounded-reprompt exhaustion path not explicitly tested) + 1 SUGGESTION (none)
- Resolution: Both findings fixed with regression tests; WARNING is non-blocking (not a spec scenario, design-level safety feature documented in D3)

## Change Scope

**Sub-slice B-i** (~215 lines):
- `InstalledModel` dataclass + `is_embedding_model()` classification helper in ollama.py
- Widened `list_models()` return type with family parsing
- Doctor checks 3/4/5 adapted to extract `.tag` from new shape
- Init preflight updated for new shape
- Tests: test_ollama.py, test_doctor.py updated

**Sub-slice B-ii** (~205 lines):
- `_pick_chat_model()` function implementing numbered picker
- `_resolve_model` TTY branch delegation
- Embedding-model filtering and graceful degradation on Ollama unreachable/zero-chat
- Tests: test_init.py extended with picker scenarios

**Total**: ~370 lines under 800-line budget, split into two sequential PRs per delivery strategy

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Return type widening | `list[InstalledModel]` dataclass | mypy-strict, attribute access, immutable, extensible |
| Embedding classification | Module-level `_EMBEDDING_FAMILIES` frozenset + helper | Declarative, reusable, case-insensitive, no-exclusion-on-ambiguity |
| Picker degradation | Broad `except Exception` fallback to typed prompt | Mirrors post-write preflight tolerance, never hard-fails |
| Doctor adaptation | Extract `.tag` at call sites, keep `model_tag_matches` unchanged | Minimal, behavior-preserving, confined contract change |

## Rollback Plan

If needed, revert both PR #172 and PR #173. No migration required — no persisted data or config-format changes. `list_models()` reverts to `list[str]`, picker removed, precedence returns to flag > typed prompt > default.

## Issue Closure

**GitHub Issue #128**: CLOSED ✅

This SDD change (Slice B) completes issue #128's scope:
- ✅ Picker replaces free-text prompt on TTY
- ✅ Embedding models excluded via family classification
- ✅ `--model` flag preserved
- ✅ Non-TTY silent default preserved
- ✅ Graceful degradation when Ollama unavailable
- ✅ Workspace always created on success
- ✅ Doctor behavior unchanged on new shape
- ✅ All tests green
- ✅ No critical/blocking issues

Slice A (config hardening) was previously merged in a separate SDD change.

## Archive Contents

This archive folder (`openspec/changes/archive/2026-07-25-init-model-picker/`) contains:
- `proposal.md` — User intent and scope
- `design.md` — Architecture decisions and file changes
- `explore.md` — Investigation and current state analysis
- `tasks.md` — Full task breakdown with 20/20 completeness
- `verify-report.md` — Full verification evidence (27/27 scenarios, 2106 tests)
- `specs/` folder with delta specs (llm-client, workspace-init, doctor-command)

All artifacts have been synced to the main specs tree. The original `openspec/changes/init-model-picker/` folder may be removed.

## Sign-Off

Archive report persisted:
- Filesystem: `openspec/changes/archive/2026-07-25-init-model-picker/archive-report.md`
- Engram: `sdd/init-model-picker/archive-report` (for traceability)

SDD cycle for init-model-picker is CLOSED. Ready for next change.
