# Archive Report: improve-ollama-onboarding

**Change**: improve-ollama-onboarding (actionable Ollama errors on first run) | **Archived**: 2026-07-18 | **Status**: Complete | **Repository**: openkos (main 9a14d2c after merge of PR #34)

This archive report closes the SDD cycle for the `improve-ollama-onboarding` change. The feature makes MVP-1's `query` command genuinely user-testable by replacing generic error messages with actionable remediation. When Ollama is not running, users now see "Start it with `ollama serve`"; when a configured model is not installed, users see "Pull it with `ollama pull <model>`". This closes the #1 first-run friction identified in the proposal — users can now fix Ollama setup issues in-surface without consulting external docs. Touches cli/main.py, test_query.py, and docs/cli.md with strict TDD across 4 verification phases and achieves 98.76% project coverage (410 tests passing, 1/1 requirement modified, 4/4 scenarios verified).

## Change Summary

**Purpose**: Refine `query`'s Ollama error handling from a single generic message to three ordered type-specific handlers that guide users toward `ollama serve` and `ollama pull <model>`, removing first-run friction for users without Ollama already running or models pre-installed.

**Scope**:
- `cli/main.py` query command (`query` function ~L699-704): split the combined `except (FtsUnavailable, OllamaError)` block into three ORDERED handlers — `except OllamaUnavailable` (keep host context, append `ollama serve` remediation), `except OllamaModelNotFound` (print configured model name and `ollama pull <model>` command), and `except (FtsUnavailable, OllamaError)` as unchanged generic fallback. All still `typer.echo(..., err=True)` + `raise typer.Exit(code=1)`, no traceback.
- `tests/unit/cli/test_query.py`: extend existing `test_query_ollama_unavailable_maps_to_exit_one` to assert `ollama serve` message; add `test_query_model_not_found_maps_to_exit_one` for model-pull scenario; add `test_query_generic_ollama_error_maps_to_exit_one` for plain OllamaError fallback; add `test_query_specific_ollama_subclasses_do_not_fall_through_to_generic` to verify handler ordering (D1); confirm `test_query_fts_unavailable_maps_to_exit_one` unchanged.
- `docs/cli.md` L84: one-line clarification that query's Ollama error handling is now actionable (distinct messages for unreachable Ollama and not-installed model).

**Architecture Decisions**:
- **D1** Order handlers specific-before-general: `OllamaUnavailable` → `OllamaModelNotFound` → `(FtsUnavailable, OllamaError)`. Both specific classes subclass `OllamaError`; Python matches `except` top-down. If the generic tuple precedes the specific handlers, both subclasses are silently caught by the generic handler and revert to today's generic message. Order is load-bearing (verified by dedicated test `test_query_specific_ollama_subclasses_do_not_fall_through_to_generic`).
- **D2** Unavailable message KEEPS `{exc}` (host already visible) and APPENDS remediation sentence naming `ollama serve`. Avoids adding any new config plumbing; existing test assertions on `"Ollama not reachable"` stay green.
- **D3** Model-not-found message AUTHORS clean text from `cfg.model` (already in scope at `main.py:692/699`) and drops the raw exception. `OllamaModelNotFound`'s own message is `Model not found (404): {raw JSON}` — confusing and unhelpful. `cfg.model` is IDENTICAL to the tag passed to `OllamaClient(model=cfg.model)`, so pull command is exact with zero new plumbing.
- **D4** Generic `(FtsUnavailable, OllamaError)` fallback string unchanged. Out of scope; FTS and non-typed Ollama errors keep today's honest wording.
- Zero ADRs created (all decisions additive, fully revertible via `git revert`, matches zero-ADR precedent of add-query-command, add-fts-state, add-ollama-client, ingest-source-body).

**Leaf-module discipline preserved**: Remediation text lives ONLY in `cli/main.py` (where users see it). `llm/ollama.py` remains untouched (structural messages only). The existing AST test `test_llm_modules_do_not_import_config` stays green, confirming `llm/` does not depend on CLI-layer config vocabulary.

## Artifacts Archived

| Artifact | Location | Status |
|---|---|---|
| Proposal | `archive/2026-07-18-improve-ollama-onboarding/proposal.md` | Moved from change folder; summarizes intent (actionable first-run Ollama errors), scope (cli/main.py, test_query.py, docs/cli.md), approach (ordered handlers, reactive, leaf discipline), risks, rollback, dependencies |
| Specification | `archive/2026-07-18-improve-ollama-onboarding/specs/query-command/spec.md` | Delta MODIFIED requirement merged to main spec tree (`openspec/specs/query-command/spec.md`); moved to archive as historical record |
| Design | `archive/2026-07-18-improve-ollama-onboarding/design.md` | Moved from change folder; documents D1-D4 decisions, ordered handler logic, message shapes, leaf-module discipline (ast test), testing strategy, threat matrix (no routing/subprocess/shell execution), migration plan, open-questions resolution |
| Tasks | `archive/2026-07-18-improve-ollama-onboarding/tasks.md` | 15/15 checked across 4 phases (RED tests for exception ordering, subclass fall-through, message content → GREEN implementation of handler split and imports → docs update → verification gate). All implementation tasks complete. |
| Verification Report | `archive/2026-07-18-improve-ollama-onboarding/verify-report.md` | PASS (1/1 requirement modified, 4/4 scenarios verified, all design decisions verified, zero blockers/critical findings, 1 accepted non-blocking finding documented). Full test suite: 410 passed, 98.76% total coverage, 100% on main.py/test_query.py touched lines. Quality gates: ruff check, ruff format, mypy strict — all pass. AST test (leaf discipline) passes. D1 handler ordering verified by dedicated direct test. |

## Spec Merge Summary

| Action | Domain | Details |
|---|---|---|
| **MODIFIED** | `query-command` | Updated existing "LLM And Index Errors Map To Exit 1" requirement to specify split into three ordered type-specific handlers (OllamaUnavailable with `ollama serve`, OllamaModelNotFound with `ollama pull <model>`, generic fallback). Changed 4 existing scenarios (Ollama backend unreachable — refined to include host + serve message; FTS index unavailable — unchanged). Added 2 new scenarios (Configured model not installed — with pull message; Other Ollama error — unchanged generic). |
| Requirements at archive time | 6 query-command | Workspace Gate, Happy-Path Answer Rendering, No-Match Is Not An Error, `--limit` Option, LLM And Index Errors (MODIFIED), Citations Reflect Answer Exactly. This change modified 1 of 6 (17%). |
| Total scenarios at archive time | 9 | Workspace Gate (2), Happy-Path (1), No-Match (1), --limit (2), LLM And Index Errors (4, now split: unreachable, model-not-installed, other-error, fts-unavailable), Citations (1). This change added 1 scenario (model-not-installed) and refined 1 (unreachable with actionable guidance). |
| Source | Delta spec from change folder | `/openspec/changes/improve-ollama-onboarding/specs/query-command/spec.md` merged into `/openspec/specs/query-command/spec.md` |
| Merge mode | MODIFIED capability | `query-command` spec already existed. Merged delta MODIFIED 1 requirement (LLM And Index Errors), refined 2 existing scenarios (unreachable, fts-unavailable) with more detailed language, added 2 new scenarios (model-not-installed, other-error). Existing unrelated requirements untouched (Workspace Gate, Happy-Path, No-Match, --limit, Citations). |
| Divergence note | Archived historical copy | The archived delta copy at `openspec/changes/archive/2026-07-18-improve-ollama-onboarding/specs/query-command/spec.md` is left unchanged as the historical record; the canonical `openspec/specs/query-command/spec.md` is the source of truth going forward. |

## Verification Status

**Final Verdict**: PASS (all requirements and scenarios verified, all design decisions locked, zero blockers or critical findings)

**Evidence Summary**:
- All 4/4 spec scenarios covered by passing tests:
  - `test_query_ollama_unavailable_maps_to_exit_one` (Ollama backend unreachable — extended to assert `ollama serve` message)
  - `test_query_model_not_found_maps_to_exit_one` (Configured model not installed)
  - `test_query_generic_ollama_error_maps_to_exit_one` (Other Ollama error)
  - `test_query_fts_unavailable_maps_to_exit_one` (FTS index unavailable — unchanged)
- Design decision verification: D1 (handler ordering — direct test `test_query_specific_ollama_subclasses_do_not_fall_through_to_generic`), D2 (unavailable message keeps host + appends serve), D3 (model-not-found uses configured tag), D4 (generic fallback unchanged)
- Test execution: **410 passed, 0 failed, 0 skipped** (full project suite); **12 tests** in `test_query.py` (5 new/extended for this change, 7 pre-existing stayed green)
- Coverage: `src/openkos/cli/main.py` 99% (pre-existing uncovered lines 372, 479→481 unrelated), `tests/unit/cli/test_query.py` 100%; Project total **98.76%** (floor 90%, enforced)
- Quality gates:
  - `uv run ruff check .` pass (exit 0)
  - `uv run ruff format --check .` pass (44 files already formatted)
  - `uv run mypy .` pass (strict mode, 44 source files, no issues)
  - `uv run pytest -k test_llm_modules_do_not_import_config` pass (leaf-module discipline verified)
- D1 handler ordering verification: `test_query_specific_ollama_subclasses_do_not_fall_through_to_generic` directly asserts that `OllamaUnavailable` and `OllamaModelNotFound` reach their OWN messages (not the generic tuple), proving specific handlers precede generic (load-bearing)
- Review workload: 126 changed lines total (main.py +20/-1, tests +98/-5, docs +1/-1) — well under 400-line budget, single PR as forecast

## Delivery History

This change was delivered as a single PR after orchestrator approval:
- **PR #34** (merged to main, 2026-07-18): Complete `improve-ollama-onboarding` implementation — `cli/main.py` (+20, -1: split except block into 3 ordered handlers with remediation text, add imports), `tests/unit/cli/test_query.py` (+98, -5: new/extended tests for all 4 scenarios and D1 ordering verification), `docs/cli.md` (+1, -1: clarify actionable error guidance). Strict TDD across 4 phases: RED exception-ordering and message tests → GREEN handler split implementation → docs → verification gate. All 15 tasks marked complete during apply phase; verify-report confirms 1/1 requirement modified and 4/4 scenarios passing.

**Repository State**: main @ 9a14d2c (commit: "feat(cli): split query's Ollama errors into actionable handlers (ollama serve / ollama pull <model>) (#34, improve first-run UX, 15/15 tasks, 410 tests 98.76% coverage)" after approval)

## Review Gate & Closure

**Bounded Review History**:
Review lineage: `review-e392b0c29a4fac29` (HIGH tier, full 4R lens set: review-readability, review-reliability, review-resilience, review-risk). Approval obtained with zero blockers. 1 accepted non-blocking finding documented below.

**Current status**:
- PR #34 merged to main
- All 410 tests passing (12 tests in test_query.py: 5 new/extended for this change, 7 pre-existing green), 98.76% project coverage
- All 4 spec scenarios passing runtime tests
- All 4 architecture decisions verified in code
- Zero blockers remain; all strict TDD gates passed
- Change complete and archived

## Accepted Non-Blocking Follow-Up Finding

Confirmed present, explicitly NOT treated as a blocker per review instructions:

1. **No inline comment on D1 handler ordering** (`cli/main.py` lines 707-723): The specific-before-general ordering of `except` clauses is load-bearing (if generic tuple precedes specific subclasses, both subclasses are silently swallowed by the generic handler). Design.md and the dedicated test `test_query_specific_ollama_subclasses_do_not_fall_through_to_generic` prove the logic is correct and the ordering is enforced. No code-path comment exists. Recommend: add brief inline comment post-MVP-1 if future maintainers touch query error handling (test depth sufficient for MVP-1, code comment a polish item).

This is minor, test-only, no behavior/spec impact. **Recommended action**: Consolidate into a small dedicated query-polish change (post-MVP-1) to add inline comment. Does not block MVP-1 completion.

## MVP-1 Value Completion

This archive closes the #1 first-run friction point identified in the proposal. Prior to this change:
- `query` command existed (PR #29) and worked for users who already had Ollama running and models pre-installed
- Users without Ollama set up saw non-actionable errors like `openkos query: failed -- Ollama not reachable at http://localhost:11434: <urlopen error [Errno 61] Connection refused>`
- MVP-1 was technically complete but genuinely untestable by first-run users without external docs

After this change:
- `query` gives users actionable, in-surface guidance: "Start it with `ollama serve`" or "Pull it with `ollama pull <model>"`
- New users can fix Ollama setup without leaving the CLI
- MVP-1 is now genuinely testable by users in all configurations (Ollama running + models installed, or with missing pieces that the app now explains how to fix)
- First-run experience radically improved: no more silent failures or cryptic connection errors

## Implementation Details

**Modules modified**:
- `src/openkos/cli/main.py`: query function except-block split into 3 ordered handlers (L707-723); imports updated to include `OllamaError, OllamaModelNotFound, OllamaUnavailable` (L15)
- `tests/unit/cli/test_query.py`: extended `test_query_ollama_unavailable_maps_to_exit_one` to assert `ollama serve`; added `test_query_model_not_found_maps_to_exit_one`, `test_query_generic_ollama_error_maps_to_exit_one`, `test_query_specific_ollama_subclasses_do_not_fall_through_to_generic`; confirmed `test_query_fts_unavailable_maps_to_exit_one` unchanged
- `docs/cli.md`: L84 clause refined to mention actionable guidance (serves as historical record in archive; living docs remain canonical)
- `src/openkos/llm/ollama.py`: UNCHANGED (leaf discipline; AST test stays green)

**Key implementation patterns**:
- **Handler ordering (D1)**: `except OllamaUnavailable` → `except OllamaModelNotFound` → `except (FtsUnavailable, OllamaError)` — specific before general, load-bearing
- **Unavailable remediation (D2)**: `f"openkos query: failed -- {exc}. Start it with \`ollama serve\`, then try again."` — `{exc}` carries the host already, append keeps brevity
- **Model-not-found remediation (D3)**: `f"openkos query: failed -- model '{cfg.model}' is not installed. Pull it with \`ollama pull {cfg.model}\`, then try again."` — uses configured model name directly, no new plumbing
- **Generic fallback (D4)**: `f"openkos query: failed -- {exc}."` — unchanged from before
- **All three branches**: `typer.echo(..., err=True)` + `raise typer.Exit(code=1) from exc`, no traceback
- **Zero-change: llm/ollama.py** remains completely untouched; `test_llm_modules_do_not_import_config` AST test passes, verifying leaf discipline maintained

## Archival Actions Completed

**Filesystem**:
- [x] Main spec tree updated: `openspec/specs/query-command/spec.md` MODIFIED (existing 6 requirements, 1 of them modified in this delta with 4 scenarios refined/added)
- [x] Change folder moved to `openspec/changes/archive/2026-07-18-improve-ollama-onboarding/` (all artifacts: proposal, specs, design, tasks, verify-report)
- [x] All change artifacts archived in the dated folder
- [x] Canonical spec promoted to `openspec/specs/query-command/spec.md`

**Engram**:
- [x] Archive report will be saved with topic key `sdd/improve-ollama-onboarding/archive-report`

## Next Steps

**For the project**:
- Archive folder now permanently in `openspec/changes/archive/2026-07-18-improve-ollama-onboarding/`
- Main spec tree updated: `openspec/specs/query-command/spec.md` is canonical, reflects this delta
- MVP-1 first-run experience complete: new users get actionable guidance on Ollama setup

**Unblocked downstream work**:
- MVP-1 is now fully complete and genuinely user-testable with actionable onboarding
- No further MVP-1 changes needed on the query error path
- Future polish: consolidate the 1 accepted non-blocking finding into a dedicated query-polish change (post-MVP-1) to add inline comment on D1 ordering

**Documented non-blocking items**:
- 1 accepted finding (D1 handler ordering inline comment) — no residual blockers or critical findings — **recommended consolidated into future dedicated polish change, not blocking MVP-1 close**

## Risks & Mitigations

| Risk | Likelihood | Mitigation | Status |
|---|---|---|---|
| Handler ordering wrong (subclass caught by base first) | High | D1 specific-before-general ordering verified by dedicated test `test_query_specific_ollama_subclasses_do_not_fall_through_to_generic` — asserts each subclass reaches its own message, not generic tuple fallback | **MITIGATED** |
| Model name in pull message hardcoded / wrong | Med | D3 uses `cfg.model` (identical to `OllamaClient(model=cfg.model)` tag), test uses distinct configured tag (llama3.2:1b-openkos-test), not placeholder | **MITIGATED** |
| Host lost in Unavailable message | Low | D2 keeps `{exc}` which carries host from `OllamaUnavailable`'s own message; existing test assertions stay green | **MITIGATED** |
| Remediation text creeps into llm/ollama.py | Low | Non-goal explicit in proposal; existing AST test enforces llm/ no-config-import discipline; llm/ollama.py confirmed unchanged | **MITIGATED** |
| Review size exceeds budget | Low | 126 changed lines across 3 files, well under 400-line budget | **MITIGATED** |
| Leaf-module discipline broken | Low | AST test `test_llm_modules_do_not_import_config` passes; llm/ remains config-free | **MITIGATED** |

## Deferred/Out-of-Scope Items

**Explicitly deferred to future changes**:
- `openkos doctor` command (Shape B, new command outside MVP-1 six) — recommended as follow-up, deferred to separate future change
- Proactive preflight (no preflight round-trips, reactive-only approach chosen)
- New OllamaClient methods (e.g. list_models)
- Config `host` field (implicit from exception message)
- Inline comment on D1 handler ordering (polish, non-blocking, post-MVP-1)

**Accepted residual limitations**:
- 1 accepted non-blocking finding (handler ordering inline comment) — purely code-quality/polish, no spec impact — recommended consolidated into future dedicated query-polish change post-MVP-1

## Traceability

This archive report records the final state of the `improve-ollama-onboarding` change from proposal through implementation, strict TDD task phases, verification, and archival. The change has been:
- Fully specified (1 requirement MODIFIED with 4 scenarios refined/added — merged into existing `openspec/specs/query-command/spec.md`)
- Fully designed (4 architecture decisions D1-D4, handler ordering, message shapes, leaf-module discipline, testing strategy, threat matrix)
- Fully implemented (single PR #34, 3 modified files, 126 LOC, 5 new/extended tests, 98.76% project coverage, 410 total tests green)
- Fully verified (1/1 requirement modified and 4/4 scenarios passing tests, all 4 design decisions verified in code, 410 tests passing, zero blockers or critical findings, 1 accepted non-blocking finding documented)
- Fully delivered (PR #34 merged to main with approval obtained)

The SDD cycle is CLOSED. The change is archived. MVP-1 first-run experience is COMPLETE.

**Archive Date**: 2026-07-18 (ISO format)
**Repository Head**: 9a14d2c (main, after approval, PR #34 merged)
**Specifications**: `openspec/specs/query-command/spec.md` (canonical, merged from delta spec, 1 requirement modified with 4 scenarios refined/added, 6 total requirements)
**Verification Date**: 2026-07-18 (verify-report PASS)
**Archival Status**: COMPLETE
**MVP-1 Status**: COMPLETE AND USER-TESTABLE WITH ACTIONABLE ONBOARDING (first-run Ollama setup errors now guide users to `ollama serve` and `ollama pull <model>`)

---

**Observation Lineage** (Engram traceability):
- Proposal: sdd/improve-ollama-onboarding/proposal
- Specification: sdd/improve-ollama-onboarding/spec
- Design: sdd/improve-ollama-onboarding/design
- Tasks: sdd/improve-ollama-onboarding/tasks
- Verification: sdd/improve-ollama-onboarding/verify-report
- Archive Report: sdd/improve-ollama-onboarding/archive-report (this document)
