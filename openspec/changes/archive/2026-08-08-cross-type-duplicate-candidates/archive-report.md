# Archive Report: cross-type-duplicate-candidates

**Change**: cross-type-duplicate-candidates  
**Issue**: #437  
**Status**: CLOSED  
**Archived**: 2026-08-08  
**PR**: #477 (squash-merged to main as commit c7272df)  
**Mode**: hybrid (OpenSpec + Engram)

## Final State Authority

This archive report describes the state of the change AT CLOSE per the skill's Final-State Authority hierarchy. The change has been fully planned, implemented, verified, and merged. All intermediate snapshots (apply-progress, verify-report) are historical; this archive report is the terminal record.

### Change Merged

PR #477 squash-merged to main as commit `c7272df` on 2026-08-08.  
CI: fully green (build, quality ruff+mypy, tests 3.12/3.13/3.14, GitGuardian).  
Issue #437: CLOSED via the PR.  
Branch `feat/437-cross-type-duplicate-candidates`: deleted (local and remote).

### Review and Delivery Authority

Native review lineage `review-dc64c878713a37f7`: APPROVED with terminal receipt.  
Post-apply gates (pre-commit, pre-push, pre-pr): all ALLOW.  
Verification verdict: PASS, 16/16 delta-spec scenarios mapped to passing tests.  
Full test suite: 3912 passed, coverage 97.21%.

### Known Non-Blocking Follow-Ups

Two WARNING follow-ups remain open (documented in PR #477 notes):
1. Cross-type prompt note can overstate type spread when filtering strips a type
2. Cross-type N>2 groups reaching adjudicate --apply's N>2 skip are untested

These are improvements to specification/testing coverage, not blocking issues.

## Artifact IDs (Engram traceability)

| Artifact | ID | Created | Topic Key |
|----------|----|---------|----|
| proposal | 2560 | 2026-08-08 09:29:29 | sdd/cross-type-duplicate-candidates/proposal |
| spec | 2561 | 2026-08-08 09:31:56 | sdd/cross-type-duplicate-candidates/spec |
| design | 2562 | 2026-08-08 09:33:13 | sdd/cross-type-duplicate-candidates/design |
| tasks | 2563 | 2026-08-08 09:38:27 | sdd/cross-type-duplicate-candidates/tasks |
| verify-report | 2565 | 2026-08-08 10:00:47 | sdd/cross-type-duplicate-candidates/verify-report |

## Implementation Summary

### Changed Specifications

Three domain specs updated to reflect the merged change:

#### 1. entity-resolution/spec.md

- **MODIFIED**: "Strict Per-Type Blocking" — narrowed to ACRONYM/LOW tiers only; HIGH (exact-title) tier now exempted and subject to cross-type bucketing
  - Added Scenario: "Cross-type similar-but-not-identical titles produce no candidate"
  - Added Scenario: "Cross-type acronym match produces no candidate"
  
- **ADDED**: "Cross-Type Exact-Title Bucketing (HIGH Tier)" — requirement for cross-type bucketing in both `find_candidates` and `find_exact_title_groups`
  - 4 scenarios covering HIGH-tier cross-type grouping, equivalence, near-match calls, and ACRONYM/LOW byte-identity
  
- **ADDED**: "`CandidateGroup.member_types` Field" — new tuple field index-aligned with member_ids, defaulting to `(okf_type,) * len(member_ids)`
  - 4 scenarios covering same-type defaults, cross-type indexing, okf_type display label, and cap-rank stability

#### 2. entity-resolution-adjudication/spec.md

- **ADDED**: "Cross-Type Prompt Honesty" — requirement that adjudication prompts honestly represent cross-type groups without false single-type claims
  - 4 scenarios covering single-type byte-identity, cross-type naming/tagging, verdict schema, and --json payload keys unchanged

#### 3. entity-resolution-merge/spec.md

- **MODIFIED**: "Frontmatter-Conflict Resolution" — explicit documentation that survivor's `type` wins on cross-type merge
  - Added Scenario: "Survivor's type wins on a cross-type merge"
  - Previous behavior was implicit; now explicit, tested, and documented

### Test Coverage and Verification

**Verification Verdict**: PASS  
**All 27 implementation tasks**: checked complete  
**Spec scenario coverage**: 16/16 scenarios mapped to passing tests  
**Full test suite**: 3912 passed in 124.35s, coverage 97.21%  
**Quality gates**: ruff check/format, mypy (all 175 source files) — clean

Per the verify-report (observation #2565), all load-bearing tests passed without tautologies or ghost loops. Notable tests:
- `test_cross_type_identical_normalized_title_forms_one_high_group` — confirms inverse of pre-change behavior
- `test_blocked_member_cross_type_prompt_still_tags_correctly` — ensures concept_id-keyed tagging survives filtered subsets
- `test_find_exact_title_groups_equals_the_high_slice_in_order` (extended) — confirms equivalence contract holds for cross-type groups

### Code Changes

Per the design (observation #2562):
- **candidates.py** (~115 lines): D1 split (flat prelude + pure partition), `member_types` field, `_type_label()`, docstrings
- **adjudication.py** (~35 lines): D4 prompt rendering with `member_types_by_id` concept_id-keyed lookup
- **okf.py** (~6 lines): D5 docstring-only; survivor-type behavior made explicit
- **test_candidates.py** (~275 lines): Replace `:167` inverse; extend `:636`/`:660`/`:688`/`:1075` fixtures; new invariant tests
- **test_adjudication.py**: Prompt-bytes regression tests (single-type unchanged, cross-type named/tagged)
- **test_okf.py**: Survivor-type pin for cross-type merge
- **test_adjudicate.py** (+37 lines): pins that a cross-type group's
  adjudication payload carries no `member_types` key

Total: 616 changed lines across 7 files, well within the 2000-line session
review budget.

## Specs Synced to Main

| Domain | Action | Details |
|--------|--------|---------|
| entity-resolution | MODIFIED + ADDED | "Strict Per-Type Blocking" narrowed to ACRONYM/LOW; 2 ADDED requirements (Cross-Type HIGH Bucketing, member_types field) |
| entity-resolution-adjudication | ADDED | 1 ADDED requirement (Cross-Type Prompt Honesty) |
| entity-resolution-merge | MODIFIED | "Frontmatter-Conflict Resolution" clarified for cross-type merges with new scenario |

All changes merged into openspec/specs/ with existing requirements preserved and new scenarios added.

## Archive Contents

- ✅ proposal.md — Intent, decision, scope, capabilities, risks, rollback plan, success criteria
- ✅ design.md — Technical approach, architecture decisions (D1-D5), file changes, testing strategy, size forecast
- ✅ tasks.md — Review workload forecast, 27 implementation tasks (all checked complete across 6 phases)
- ✅ exploration.md — Current state, blast radius analysis, approaches (A1-A4, B1-B4), cost verification, recommendations
- ✅ specs/ — Three delta specs for entity-resolution, entity-resolution-adjudication, entity-resolution-merge

Archive location: `openspec/changes/archive/2026-08-08-cross-type-duplicate-candidates/`

## SDD Cycle Complete

The cross-type-duplicate-candidates change has successfully completed the full SDD cycle:

1. **Explore** ✅ — Mapped the problem, identified blast radius, evaluated approaches
2. **Propose** ✅ — Defined intent, scope, capabilities, risks, and rollback plan
3. **Spec** ✅ — Wrote delta specs for three domains with 16 new scenarios
4. **Design** ✅ — Detailed technical approach with five architecture decisions (D1-D5)
5. **Tasks** ✅ — Broke down work into 27 implementation tasks across 6 phases
6. **Apply** ✅ — Implemented all tasks; PR #477 squash-merged to main as c7272df
7. **Verify** ✅ — All 27 tasks complete; 16/16 spec scenarios mapped to passing tests; full suite 3912 passed, coverage 97.21%
8. **Archive** ✅ — Merged delta specs into main specs; archived change folder; recorded final state

Ready for the next change.

## Key Decisions Recorded

1. **HIGH-tier-only cross-type for phase 1** — ACRONYM/LOW kept per-type to preserve O(n^2)-per-type cost ceiling; measured cost profile required before expanding to lower tiers
2. **Additive `member_types` field** (Approach A.3) — Lowest-blast-radius option; only new cross-type code path reads it; all 76 existing construction sites remain untouched
3. **Survivor-type implicit-to-explicit** (Approach B.1) — Zero behavior change; survivor's type always wins on cross-type merge; no prompt-schema change; LLM-gated merge contract unchanged
4. **Concept_id-keyed tagging** — Prompt tagging by concept_id, not position, ensures correctness when `_load_members` filters unreadable docs or adjudication filters blocked ids

## Non-Goals and Future Work

Explicitly out of scope for this change (documented in proposal and exploration):
- Cross-type ACRONYM/LOW matching (needs fresh cost measurement)
- Embedding-proximity tier
- Adjudicator-returned type (defensible fast-follow)
- Reading `type_alternative` (would break documented invariant; revisit if survivor-wins proves inadequate)

## Risks and Mitigations

| Risk | Likelihood | Mitigation | Status |
|------|-----------|-----------|--------|
| One entry point changed without the other | Med | Equivalence tests (`:636`/`:660`/`:1075`) fail loudly if drift occurs; tasks paired them in same commit | Proven by test suite PASS |
| `member_types` drifts from `okf_type` | Med | `__post_init__` derives it; length invariant raises ValueError | Tested, length mismatch raises ValueError per scenario |
| Cross-type groups crowd real duplicates out of 50-group cap | Low | Same `groups` list before `_cap_rank_key`; truncation notice already surfaces it; new scenario pin added | Verified by cap-rank scenario test |
| Joined `okf_type` label misread as real type | Low | Ephemeral display only, never persisted; spec + docstring name it explicitly | Spec documents "ephemeral display label only" |

## Rollback and Reversibility

Rollback is via `git revert` of commit c7272df. Since candidates are ephemeral (no bundle/state mutation), a revert only restores per-type blocking. Merges already applied stay valid: the survivor-type rule is today's behavior made explicit, not new behavior.

No data loss risk.
