# Archive Report: discover-concept-ids

**Change**: discover-concept-ids | **Archived**: 2026-07-28 | **Status**: Complete and Shipped | **Repository**: openkos (main 1e2e9b3 after merged PRs)

This archive report closes the SDD cycle for the `discover-concept-ids` change. The feature implements issue #184 — a new `openkos list [TYPE]` read-only CLI verb for enumerating bundle objects by id, sensitivity, lifecycle status, and title in a single bundle walk. The implementation delivers a missing discovery counterpart to five write verbs (`forget`, `relate`, `merge`, `unmerge`, `set-sensitivity`) that previously had no way for users to obtain concept ids from the CLI alone except by browsing the filesystem directly. The change was delivered as two stacked PRs (#241 and #242), both now merged to `main`, with a bounded remediation cycle closing three gaps found during verification.

## Change Summary

**Purpose**: Close issue #184 by shipping the `list` verb promised in MVP-1, delivering searchable concept enumeration with type filtering, output bounding, and a single bundle walk.

**Scope**:
- New `list-command` capability spec in `openspec/specs/list-command/spec.md` (9 requirements, 15 scenarios)
- New `src/openkos/bundle/listing.py` module with `BundleObject` dataclass, `list_objects()` single-pass enumerator, and `resolve_link_dir()` vocabulary resolver
- New `@app.command("list")` on `list_objects_cmd()` in `src/openkos/cli/main.py` with refusal ladder (vocabulary → limit → workspace), in-memory filtering, and aligned tabular output
- New `tests/unit/bundle/test_listing.py` (47 tests) and `tests/unit/cli/test_list.py` (23 tests, including 5 remediation tests)
- Updated `docs/cli.md` with `### openkos list [TYPE]` reference section
- Column layout: `ID  SENSITIVITY  STATUS  TITLE`, alphabetically ordered, default limit 50 with truncation footer
- Confidential titles printed in full (no redaction, no gate, byte-identical output across sensitivity levels)
- Deprecated and superseded objects shown by default, marked, with no hide flag
- Exactly one `_iter_docs` bundle walk per invocation (enforced structurally and by test)
- Read-only, no `--json` or structured output (deferred to a follow-up)

**Key Architecture Decisions**:
- D1: Enumerator lives in `bundle/listing.py` (canonical layer, bundle-scoped, no derived-layer imports)
- D2: `BundleObject` frozen dataclass with structural id/link_dir derivation; sensitivity as a report field (not a gate); concept-id derivation duplication deliberately deferred
- D3: Single-walk enforcement via plain-function counting wrapper (call recorded at call time, not on iteration)
- D4: Status derived in-pass from `supersedes` edges; drift guard test cross-checks against `lifecycle.deprecated_concept_ids`
- D5: Fail-visible (not fail-closed): unparseable documents printed as rows with `(unreadable)` marker, id still filterable
- D6: Column formatting via `ljust` over shown rows only; long titles never truncated; newlines/tabs collapsed at row construction
- D7: Type-filter vocabulary: canonical `link_dir` (lowercase plural) + case-sensitive `REGISTRY.name` alias; error ladder precedes workspace check

**Spec Decisions Embedded**:
- Confidential titles printed in full: `sensitivity` governs LLM-send gating, not local terminal display; `--include-confidential` is exclusively an LLM-send gate and MUST NOT become a display gate; precedent is `duplicates` which already prints ids for confidential objects unconditionally; issue #240 (scope the confidential gate to non-local LLM backends) was filed during this cycle but is explicitly out of scope and does not change this requirement in either direction (verified in spec requirement text with explicit cross-ref)
- Argument-before-workspace ladder: matches `set-volatility` precedent; vocabulary/limit refusal happens before workspace check so users see the flag error, not an unrelated workspace error
- No ADR created: additive, read-only verb; `--json` deferred so no serialization contract frozen; no on-disk format change; revert is one commit (fails hard-to-reverse condition)

**Delivery History**:
- **PR #241** (merged, squash commit `6d8237b`): `src/openkos/bundle/listing.py` + `tests/unit/bundle/test_listing.py` (47 unit tests). Autonomous work unit covering single-pass enumerator, vocabulary resolver, single-walk enforcement, status/deprecation logic, and field derivation.
- **PR #242** (merged, squash commit `1e2e9b3`): CLI verb `list_objects_cmd()` + `tests/unit/cli/test_list.py` (23 tests) + `docs/cli.md` section + spec. Bounded remediation cycle (3 gaps from verify FAIL) added 5 tests; implementation corrected `--limit 0` refusal condition to match spec (spec was correct, implementation conformed).
- Final test count: 2484 passed, coverage 97.56% against 90% gate
- Quality gates: ruff clean, mypy strict clean, build smoke test passed
- Issue #184 is CLOSED
- No ADR was created (additive, read-only, hard to reverse is false)

## Artifacts Archived

| Artifact | Location | Status |
|---|---|---|
| Proposal | `archive/2026-07-28-discover-concept-ids/proposal.md` | Moved from change folder; documents intent, scope, decisions A-E, affected areas, risks, rollback plan, delivery forecast |
| Specification | `archive/2026-07-28-discover-concept-ids/specs/list-command/spec.md` | Promoted to main spec tree at `openspec/specs/list-command/spec.md` + moved to archive; 9 requirements, 15 scenarios |
| Design | `archive/2026-07-28-discover-concept-ids/design.md` | Moved from change folder; documents D1-D7 architecture decisions, data flow, testing strategy, file changes, ADR gate reasoning |
| Exploration | `archive/2026-07-28-discover-concept-ids/explore.md` | Moved from change folder; current-state analysis, reuse surface, design questions, interaction with sensitivity work, existing conventions, approaches and recommendation |
| Tasks | `archive/2026-07-28-discover-concept-ids/tasks.md` | Moved from change folder; 33/33 complete (14 phases across 2 PRs, 5 remediation subtasks) |
| Apply Progress | `archive/2026-07-28-discover-concept-ids/apply-progress.md` | Moved from change folder; three batches (PR1 Phases 1-7, PR2 Phases 8-14, remediation 3 gaps), complete TDD cycle evidence per batch |
| Verification Report | `archive/2026-07-28-discover-concept-ids/verify-report.md` | Moved from change folder; PASS verdict (supersedes prior FAIL after bounded remediation); 9/9 requirements, 15/15 scenarios, 33/33 tasks, 2484 tests, 97.56% coverage |

## Spec Merge Summary

| Action | Domain | Details |
|---|---|---|
| **NEW** | `list-command` | Created new capability spec at `openspec/specs/list-command/spec.md` (merged from delta, promoted to canonical) |
| Requirements at archive time | 9 | Workspace Presence Check (2 scenarios), Exactly One Bundle Walk (1), Type Filter Vocabulary (3), Output Bounding (3), Deprecated and Superseded Visibility (1), Column Layout (1), Confidential Titles Printed in Full (1), Empty Bundle/Unparseable Document Handling (2), Read-Only No Structured Output (1) |
| Total scenarios at archive time | 15 | Full coverage of workspace gating, single-walk enforcement, type filtering (canonical + alias), output bounding (default/limit/all), deprecated visibility, column layout, confidential title printing, empty bundles, unparseable documents, read-only invariant, and no mutation |
| Source | Delta spec from change folder | `openspec/changes/discover-concept-ids/specs/list-command/spec.md` promoted to `openspec/specs/list-command/spec.md` |
| Merge mode | NEW capability | The `list` capability did not exist before; this change establishes it. No requirements modified or removed; spec is ADDED-only. **Note: this spec requires no `(Previously: ...)` annotation** per issue #239 convention, because `list` is a brand-new verb that revises no prior behavior. |
| Divergence note | Archived historical copy | The archived delta copy at `openspec/changes/archive/2026-07-28-discover-concept-ids/specs/list-command/spec.md` is left unchanged as the historical record; the canonical `openspec/specs/list-command/spec.md` is the source of truth for this capability going forward. |

## Verification Status

**Final Verdict**: PASS (after bounded-remediation corrections: all three CRITICAL/WARNING issues fixed and approved)

**Evidence Summary**:
- All 15/15 spec scenarios covered by passing tests (2 on workspace checks, 1 on single walk, 3 on type filter, 3 on output bounding, 1 on deprecated visibility, 1 on column layout, 1 on confidential titles, 2 on empty bundle/unparseable docs, 1 on read-only/no-mutation)
- All 9/9 requirements satisfied with passing scenario coverage
- All 33/33 tasks complete (14 phases + 5 remediation subtasks)
- Design decision verification: D1-D7 all present and verified in code
- Test execution (final, post-remediation): **2484 passed, 0 failed, 0 skipped**
- Coverage: 97.56% line+branch (gate ≥90%, achieved)
- Quality gates:
  - `uv run ruff check .` pass (exit 0, all checks pass)
  - `uv run ruff format --check .` pass (all 146 files formatted)
  - `uv run mypy .` pass (strict mode, no issues)
  - `uv build` succeeded, wheel smoke test passed
- Byte-unchanged: no production code changes to `okf.py`, `types.py`, `lifecycle.py`, or `sensitivity.py` (per design D2 decision)

**Bounded Remediation**: Three gaps found in initial verify-report FAIL verdict were closed:
1. **CRITICAL**: No runtime-observable test for "No mutation on any run" scenario. FIXED: added `_workspace_snapshot` helper + 2 mutation tests (`test_list_mutates_nothing_on_a_run_that_produces_rows`, `test_list_mutates_nothing_on_a_run_that_truncates_output`) that snapshot filesystem before/after and assert identity.
2. **CRITICAL**: `--json` flag should be rejected (spec says so) but no test observed it. FIXED: added `test_list_json_flag_is_rejected_as_unknown_option` asserting Typer's "no such option" rejection path.
3. **WARNING**: `--limit 0 --all` spec said "reject unconditionally" but implementation had `not all_objects and limit <= 0`. FIXED: orchestrator resolved spec is correct, implementation conforms; changed condition to `limit <= 0` (unconditional). Added `test_list_limit_zero_with_all_still_refuses` and `test_list_limit_negative_with_all_still_refuses`, both run inside workspace to rule out false pass via workspace check.

All three fixes confirmed independent of the apply agent's RED evidence; loaded-bearing asserts verified by code inspection.

## Implementation Details

**Modules added/modified**:
- `src/openkos/bundle/listing.py`: `BundleObject` dataclass, `list_objects()` enumerator, `resolve_link_dir()`, helper maps and functions (~150 lines + docstrings)
- `src/openkos/cli/main.py`: `@app.command("list")` on `list_objects_cmd()`, refusal ladder, filtering/limiting/formatting (~100 lines)
- `tests/unit/bundle/test_listing.py`: 47 tests covering all field derivations, single-walk enforcement, status logic, vocabulary resolution, edge cases (~220 lines)
- `tests/unit/cli/test_list.py`: 23 tests covering CLI integration, refusals, filtering, output, confidential titles, empty bundles, unparseable docs, mutations (~250 lines, includes 5 remediation tests)
- `docs/cli.md`: new `### openkos list [TYPE]` reference section (~30 lines)
- No changes to `model/okf.py`, `model/types.py`, `lifecycle.py`, `sensitivity.py` (per design D2)

**Single-walk enforcement**:
- Structural: `list_objects` contains exactly one `for scan in okf._iter_docs(bundle_dir):` loop
- Tested: non-generator counting wrapper at PR1 and CLI level; lifecycle isolation guard (monkeypatch `lifecycle.deprecated_concept_ids` to raise, command still exits 0)
- Verified: `listing.py` imports only `okf`, `REGISTRY`, and stdlib; never calls `lifecycle` or derived-layer functions

**Concept-id derivation duplication (D2 decision)**:
- Current state: three spellings in `lifecycle.py:70`, `sensitivity.py:116`, `bundle/listing.py`
- Why deferred: extraction to `okf.concept_id_for()` would add public API surface (separate decision) and destroy clean-revert rollback plan (this change would no longer be purely additive)
- Follow-up trigger: a fourth call site, or the first time the derivation itself changes
- Interim: inline comment in `listing.py` naming both other spellings and the extraction trigger

**Fail-visible contract (D5 decision)**:
- Unparseable documents (read_error or parse_error set) are still listed as rows with `(unreadable)` title marker
- Differs from `sensitivity.sensitive_concept_ids` (fail-closed, because it gates LLM send — a leak is wrong)
- Matches `lifecycle`'s fail-safe direction but stronger: `lifecycle` skips uncertain docs, `listing` includes them with unknown fields because the row is the payload
- Test: `test_list_unparseable_document_still_prints_a_row_and_exits_zero` passes with both rows printed, `(unreadable)` marker, exit 0, no traceback

**Confidential title behavior (spec requirement with embedded rationale)**:
- Titles printed in full regardless of sensitivity; no redaction, no flag, no omitted rows
- Output byte-identical across sensitivity levels
- `sensitivity` governs what LEAVES the machine (LLM send), not what owner sees on local terminal
- `--include-confidential` is exclusively an LLM-send gate (`should_block` / `blocks_llm_send`) and MUST NOT become a display gate
- Precedent: `duplicates` already prints ids for confidential objects unconditionally
- Issue #240 (scope confidential gate to non-local LLM backends) was filed during this cycle but is explicitly scoped out of `list` and does not change this requirement in either direction

## Delivery History

This change was delivered as a two-PR stacked pair after orchestrator approval of `delivery_strategy: auto-chain`:
- **PR #241** (feat/list-enumerator, squash commit `6d8237b`): `bundle/listing.py` + 47 unit tests; autonomous work unit; passed review
- **PR #242** (feat/list-cli-verb, squash commit `1e2e9b3`): CLI verb + 23 tests + docs + spec; based on PR1's HEAD; targeted PR1's branch until PR1 merged, then retargeted to main; underwent bounded remediation after initial verify-report FAIL to close 3 gaps (no-mutation test, `--json` rejection test, `--limit 0 --all` spec alignment)

**Repository State**: main @ 1e2e9b3 (after both PRs merged with remediation applied)

## Review Gate & Closure

**Delivery review history**:
- PR #241: passed review (stacked first slice)
- PR #242: initial verify-report found 3 gaps; bounded remediation applied; final verify-report PASS

**Current status**:
- Both PRs merged to main
- All 2484 tests passing, 97.56% coverage
- All 9/9 spec requirements covered by 15/15 passing scenarios
- All 33/33 tasks complete
- No blockers remain; all CRITICAL/WARNING findings from verify FAIL are closed
- Issue #184 is CLOSED

## Deferred/Out-of-Scope Items

**Deliberately deferred, not forgotten**:
- The concept-id derivation is duplicated across `lifecycle.py:70`, `sensitivity.py:116`, and `bundle/listing.py` — extraction trigger is a fourth call site or a change to the derivation itself
- `bundle/listing.py` replicates `lifecycle`'s effective-deprecated rule, guarded by a cross-check drift test rather than shared code — shared extraction deferred for same reason as concept-id
- A Source's title in `list` output comes from the file slug (path) rather than from the document's H1 frontmatter, which makes Source rows read poorly. Pre-existing `ingest` behavior, not introduced here; documented as known limitation
- Issue #240 ("scope the confidential gate to non-local LLM backends") was filed during this cycle and is related but out of scope; spec explicitly notes it does not change the confidential-titles requirement in either direction

**Explicitly out of scope per proposal (deferred, not banned)**:
- `--json` or any structured output (ids are already consumable via `cut`/`awk`; serialization contract deferred until a second consumer appears)
- `--sensitivity` filter, `--fields`, full-text search over titles
- Recency ordering (alphabetical by id is free and matches `_iter_docs` sort)
- Changes to `status`, `duplicates`, `survey_bundle`, or id format
- MCP/API surfaces

## ADR Gate Assessment

**No ADR was created.** Evaluation per `openspec/config.yaml` rules:

1. *Does this decide a technology, pattern, interface, or trade-off?* Marginally yes — module placement (D1), fail-visible direction (D5), in-pass status derivation (D4) are design decisions.
2. *Is that decision hard to reverse?* **No.** The verb is additive and read-only. No on-disk format change, no persisted state, no serialization contract frozen (`--json` deferred precisely to keep nothing frozen). Rollback is `git revert` of one commit.

Both conditions must hold; only one does. Per config: "when in doubt, do not create one". The decisions are recorded in the design document instead, which is the correct home for reversible architectural choices.

## Traceability

This archive report records the final state of the `discover-concept-ids` change from proposal through implementation, verification, bounded remediation, and archival. The change has been:
- Fully proposed (issue #184, intent, scope, capabilities, 5 key decisions A-E, affected areas, risks, rollback plan)
- Fully explored (9 design questions answered, reuse surface identified, approaches evaluated, 3 risks surfaced)
- Fully designed (7 architecture decisions D1-D7, data flow, testing strategy, file changes, ADR gate reasoning, delivery forecast ~860 lines)
- Fully specified (9 requirements, 15 scenarios, `list-command` spec at `openspec/specs/list-command/spec.md`, no `(Previously: ...)` annotation needed because brand-new capability)
- Fully implemented (two autonomous PRs, 70 production lines + 470 test lines, 47+23=70 tests, strict TDD RED-GREEN cycles)
- Fully verified (PASS verdict after bounded remediation, 9/9 requirements, 15/15 scenarios, 33/33 tasks, 2484 tests, 97.56% coverage, all gates clean)
- Fully delivered (PRs merged to main, CI green, issue closed)
- Fully archived (change folder moved to dated archive, delta spec promoted to canonical spec, archive report written)

The SDD cycle is CLOSED. The change is archived and ready for the next change.

**Archive Date**: 2026-07-28 (ISO format)
**Repository Head**: 1e2e9b3 (main, after both PRs merged with remediation)
**Specification**: `openspec/specs/list-command/spec.md` (canonical, promoted from delta spec, 9 requirements, 15 scenarios, no `(Previously: ...)` annotation)
**Verification Date**: 2026-07-28 (verify-report PASS, post-remediation)
**Issue Status**: #184 CLOSED
**Archival Status**: COMPLETE

## Artifact Observation IDs (Engram Traceability)

| Artifact | Observation ID |
|---|---|
| Proposal | #2098 |
| Specification | #2100 |
| Design | #2101 |
| Tasks | #2102 |
| Verification Report | #2104 |
| Archive Report | (this document) |

All SDD artifacts are traceable via Engram topic keys: `sdd/discover-concept-ids/{proposal,spec,design,tasks,verify-report,archive-report}`.
