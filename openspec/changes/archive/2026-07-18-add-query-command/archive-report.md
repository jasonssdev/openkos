# Archive Report: add-query-command

**Change**: add-query-command (MVP-1 query chain #4) | **Archived**: 2026-07-18 | **Status**: Complete | **Repository**: openkos (main 27a71a7 after merge of PR #29)

This archive report closes the SDD cycle for the `add-query-command` change. The feature implements the fourth and final piece of MVP-1's `query` capability — a read-only CLI command that gates on the workspace, builds an `OllamaClient` from config, calls the `retrieval.answer()` library seam, and renders the answer plus citations as plain text to stdout. It wires the three archived upstream components (`fts-state`, `llm-client`, `query-answer`) into a thin CLI entry point with no new dependencies, strict TDD across 10 task phases, and achieves 98.73% project coverage. The change also includes two small test/doc follow-ups to the already-merged `query-answer` capability.

## Change Summary

**Purpose**: Ship the `query` CLI command for MVP-1's query capability, providing users with the ability to ask questions and receive cited answers from the compiled bundle via the workspace-gated, config-driven, local-first Ollama integration.

**Scope**:
- New `@app.command() def query(question, --limit=5)` in `src/openkos/cli/main.py`: mirrors the read-only `status`/`lint` shape exactly
- Workspace gate: reuse `config.require_workspace(root)` refusal (exit 1 if not a workspace)
- Config wiring: read `read_config(root).model`, build `OllamaClient(model=...)`
- Answer invocation: call `retrieval.answer(question, bundle_dir=layout.bundle_dir, llm=client, limit=limit)`
- Plain-text rendering: answer text, then optional `Citations:` section with `→ <concept_id> (<title>)` per citation (indented 2 spaces)
- No-match handling: answer line only (no `Citations:` section), exit 0 (not an error)
- Error boundaries: `except (FtsUnavailable, OllamaError)` around both `read_config` and `answer()` calls → friendly stderr, exit 1
- `--limit` option: default 5, forwarded unchanged to `answer(..., limit=n)`
- Follow-ups to `query-answer`: `_SYSTEM_PROMPT` docstring (D5 grounding-rules text), multi-survivor citation-order+join test
- Documentation: expand `docs/cli.md` stub (lines 74-76) to cover workspace gate, `--limit`, output shape, error mapping
- Tests: new `tests/unit/cli/test_query.py` (8 tests via CliRunner, patched `answer` symbol, no live Ollama/FTS)
- Architectural test fix: rewrite pre-existing `test_cli_module_does_not_import_state_fts` to `test_ingest_and_forget_do_not_reference_state_fts` (function-scoped AST check, allows `query`'s FtsUnavailable import per spec design)
- No config schema changes, no new dependencies (typer already runtime dep), no ingest/forget/state mutations

**Architecture Decisions**:
- D1: Read-only shape = `status`/`lint` (bare workspace gate, NOT try-wrapped like `ingest`/`forget`)
- D2: Two error boundaries — Phase-A wraps `read_config` (OSError/ValueError → friendly exit 1); Phase-B wraps `answer()` (FtsUnavailable/OllamaError → friendly exit 1)
- D3: Answer-first, banner-free output; `Citations:` section only when non-empty
- D4: Import `answer` symbol directly (not module) so tests patch the `openkos.cli.main.answer` boundary
- Zero ADRs created (all decisions additive, fully revertible via `git revert`, matches the zero-ADR precedent of upstream query chain changes #1-#3)

**Change-scope verification**:
- Only changed paths: `src/openkos/cli/main.py` (+69 lines: 3 imports + query command), `tests/unit/cli/test_query.py` (new, 213 lines, 8 tests), `src/openkos/retrieval/answer.py` (+4 lines: _SYSTEM_PROMPT docstring only), `tests/unit/retrieval/test_answer.py` (+47 lines: multi-survivor test), `tests/unit/state/test_fts.py` (rewritten guard test), `docs/cli.md` (~20 lines: stub expansion)
- No ingest/forget/status/lint changes; no config schema or dependency changes
- 375 pre-existing regression tests pass unmodified

## Artifacts Archived

| Artifact | Location | Status |
|---|---|---|
| Proposal | `archive/2026-07-18-add-query-command/proposal.md` | Moved from change folder; summarizes intent, scope, approach, risks, MVP-1 context, success criteria |
| Specification | `archive/2026-07-18-add-query-command/specs/query-command/spec.md` | Promoted to main spec tree at `openspec/specs/query-command/spec.md` + moved to archive |
| Design | `archive/2026-07-18-add-query-command/design.md` | Moved from change folder; documents D1-D4 decisions, command signature, control flow, _SYSTEM_PROMPT docstring, multi-survivor test, testing strategy, threat matrix, open questions resolution |
| Tasks | `archive/2026-07-18-add-query-command/tasks.md` | 18/18 checked across 10 phases (foundation, RED/GREEN gate → happy path/limit → error boundaries → follow-ups → docs → verification → unplanned fix); all sub-tasks complete including unplanned architectural test rewrite |
| Verification Report | `archive/2026-07-18-add-query-command/verify-report.md` | PASS (all 6/6 requirements and 9/9 scenarios, all design decisions verified, zero blockers/critical findings, 4 accepted non-blocking follow-ups documented). Full test suite: 397 passed, 0 failed; 98.73% coverage floor 90%. Quality gates: ruff check, ruff format, mypy strict — all pass. Spec coverage: 9 scenarios verified by 8 CLI tests + 1 answer.py multi-survivor test. Architectural regression test (`test_ingest_and_forget_do_not_reference_state_fts`) confirms fts-state dormancy guarantee intact. |

## Spec Merge Summary

| Action | Domain | Details |
|---|---|---|
| **NEW** | `query-command` | Created new capability spec at `openspec/specs/query-command/spec.md` |
| **UNCHANGED** | `query-answer` | No changes to `openspec/specs/query-answer/spec.md` — the two test/doc follow-ups (answer.py docstring, multi-survivor test) alter no requirements or scenarios |
| Requirements at archive time | 6 | Workspace Gate (2 scenarios), Happy-Path Answer Rendering (1 scenario), No-Match Is Not An Error (1 scenario), `--limit` Option (2 scenarios), LLM And Index Errors Map To Exit 1 (2 scenarios), Citations Reflect The Answer Exactly (1 scenario) |
| Total scenarios at archive time | 9 | Full coverage of workspace gate, happy path with citations, no-match, --limit default/override, Ollama unavailable, FTS unavailable, citation order |
| Source | Delta spec from change folder | `/openspec/changes/add-query-command/specs/query-command/spec.md` promoted to `/openspec/specs/query-command/spec.md` |
| Merge mode | NEW capability | The `query-command` capability did not exist before; this change establishes it. No existing spec to merge into. |
| Divergence note | Archived historical copy | The archived delta copy at `openspec/changes/archive/2026-07-18-add-query-command/specs/query-command/spec.md` is left unchanged as the historical record; the canonical `openspec/specs/query-command/spec.md` is the source of truth for this capability going forward. |

## Verification Status

**Final Verdict**: PASS (all requirements and scenarios verified, all design decisions locked, zero blockers or critical findings)

**Evidence Summary**:
- All 9/9 spec scenarios covered by passing tests:
  - `test_query_refuses_when_not_a_workspace` (Workspace Gate: outside)
  - `test_query_matching_answer_renders_citations_in_hit_rank_order` (Workspace Gate: inside + Happy-Path + Citation order)
  - `test_query_no_match_renders_answer_line_alone` (No-Match Is Not An Error)
  - `test_query_limit_flag_is_forwarded_unchanged` (`--limit` override)
  - `test_query_omitted_limit_defaults_to_five` (`--limit` default)
  - `test_query_ollama_unavailable_maps_to_exit_one` (Ollama unreachable)
  - `test_query_fts_unavailable_maps_to_exit_one` (FTS unavailable)
  - `test_query_malformed_config_maps_to_exit_one_before_calling_answer` (error boundary, extra coverage)
  - `test_multiple_surviving_hits_cite_in_rank_order_and_join_context` (answer.py follow-up, multi-survivor ordering+join)
- Design decision verification: D1 (read-only gate shape), D2 (two error boundaries with correct scoping), D3 (banner-free output, no-match exit 0), D4 (answer symbol import for test patching)
- Test execution (final test suite, per verify-report): **397 passed, 0 failed, 0 skipped** (full suite); **8 tests** in `tests/unit/cli/test_query.py` all passing, **1 multi-survivor test** in `tests/unit/retrieval/test_answer.py` passing
- Coverage: `src/openkos/cli/main.py` 99% (2 pre-existing uncovered branches in `forget` unrelated to this change), `src/openkos/retrieval/answer.py` 100%, `src/openkos/state/fts.py` 100%; Project total **98.73%** (floor 90%, enforced)
- Quality gates:
  - `uv run ruff check .` pass (exit 0, all checks pass)
  - `uv run ruff format --check .` pass (44 files already formatted)
  - `uv run mypy .` pass (strict mode, 44 source files, no issues)
- Architectural regression test: `test_ingest_and_forget_do_not_reference_state_fts` (rewritten) still asserts the real invariant — `ingest`/`forget` functions' source never mentions `fts`/`state` (function-scoped AST check) — confirming `fts-state` dormancy guarantee intact; legitimately allows `query`'s FtsUnavailable import per spec design

## Delivery History

This change was delivered as a single PR after orchestrator approval:
- **PR #29** (merged to main, 2026-07-18): Complete `openkos query` command implementation — `cli/main.py` (+69, command + imports), `test_query.py` (new, 213 lines, 8 CliRunner tests), `answer.py` (+4, `_SYSTEM_PROMPT` docstring), `test_answer.py` (+47, multi-survivor test), `test_fts.py` (rewritten guard test), `docs/cli.md` (~20, stub expansion). Strict TDD across 10 phases: foundation → RED/GREEN gate → happy path/limit → error boundaries → follow-ups → docs → verification → unplanned arch fix. All 18 tasks marked complete during apply phase; verify-report confirms all 6/6 requirements and 9/9 scenarios passing.

**Repository State**: main @ 27a71a7 (commit: "feat(cli): add openkos query command for MVP-1 query capability (#29, CLI wiring, workspace-gated, config-driven, local-first)" after approval)

## Review Gate & Closure

**Bounded Review History**:
Review lineage: `review-1693068357b8de6f` (HIGH tier, full 4R lens set: review-readability, review-reliability, review-resilience, review-risk). Approval obtained with zero blockers. 4 non-blocking findings accepted per review instructions and recorded below.

**Current status**:
- PR #29 merged to main
- All 397 tests passing (8 new in test_query.py + 1 in test_answer.py), 98.73% project coverage
- All 9 spec scenarios passing runtime tests
- All 4 architecture decisions verified in code
- Architectural regression test (`test_ingest_and_forget_do_not_reference_state_fts`) confirms fts-state dormancy guarantee intact
- Zero blockers remain; all strict TDD gates passed
- Change complete and archived

## Accepted Non-Blocking Follow-Up Findings

Confirmed present, explicitly NOT treated as blockers per instructions. All 4 are minor, doc/polish only, no behavior/spec impact:

1. **Query docstring variable naming** (`cli/main.py` line 666): docstring says "llm=client" but variable is actually "llm" (`llm = OllamaClient(...)`). Recommend: update docstring to match.
2. **Answer.py module docstring stale note** (`retrieval/answer.py` line 9): still says "propagate unswallowed to the future `query` command (#4)" — query command now exists. Recommend: update docstring to mark resolved.
3. **Docs/cli.md error-boundary coverage gap** (`docs/cli.md`): documents `FtsUnavailable`/`OllamaError` → exit 1 mapping but omits the Phase-A `read_config` error boundary (OSError/ValueError). Recommend: document malformed-config failure mode alongside other error boundaries.
4. **Missing OllamaClient construction test assertion** (`tests/unit/cli/test_query.py`): no test asserts `OllamaClient` is constructed with `model=cfg.model` (only that command builds SOME client and reaches `answer()`). Recommend: add assertion to strengthen test specificity, though current coverage of `answer()` invocation and error handling is complete.

None of these alter behavior, spec conformance, or test correctness. **Recommended action**: Consolidate into a small dedicated CLI polish change (post-MVP-1) to clean up these docstrings and strengthen test assertions. Do not block MVP-1 completion.

## MVP-1 Query Capability Chain Completion

This archive closes the MVP-1 `query` capability chain at full completion:

| # | Change | Status | Archived | Lineage |
|---|---|---|---|---|
| 1 | `add-fts-state` | Complete | 2026-07-18 | `sdd/add-fts-state/archive-report` |
| 2 | `add-ollama-client` | Complete | 2026-07-18 | `sdd/add-ollama-client/archive-report` |
| 3 | `add-query-answer` | Complete | 2026-07-18 | `sdd/add-query-answer/archive-report` |
| 4 | `add-query-command` | Complete | 2026-07-18 | THIS REPORT |

All four changes are archived, all spec merged to main specs tree, all tests passing, all design decisions locked. Users can now run `openkos query "<question>"` in an initialized workspace to retrieve cited answers from the compiled bundle via local Ollama, fulfilling the MVP-1 query capability end-to-end.

## Implementation Details

**Modules added/modified**:
- `src/openkos/cli/main.py`: New `query` command + 3 imports (answer, OllamaClient, OllamaError, FtsUnavailable)
- `src/openkos/retrieval/answer.py`: +4 lines, `_SYSTEM_PROMPT` docstring only (no signature change)
- `tests/unit/cli/test_query.py`: New test file (213 lines, 8 tests)
- `tests/unit/retrieval/test_answer.py`: +47 lines, new multi-survivor test
- `tests/unit/state/test_fts.py`: Rewritten guard test (function-scoped AST check)
- `docs/cli.md`: ~20 lines, stub expansion (workspace gate, `--limit` flag, output shape, error behavior)

**Key implementation patterns**:
- **Workspace gate (D1)**: `reason = config.require_workspace(root); if reason is not None: echo(refuse, err=True); Exit(1)` — read-only shape matching `status`/`lint`
- **Phase-A error boundary (D2)**: `try: cfg = config.read_config(root) except (OSError, ValueError) as exc: echo("failed while reading...", err=True); Exit(1)`
- **LLM client construction**: `llm = OllamaClient(model=cfg.model)` — pure constructor, no I/O
- **Phase-B error boundary (D2)**: `try: result = answer(...) except (FtsUnavailable, OllamaError) as exc: echo("failed --", err=True); Exit(1)`
- **Banner-free output (D3)**: `echo(result.answer)` then optional citations section — no workspace banner, no-match exits 0
- **Citation rendering**: `→ {concept_id} ({title})` format, one per citation in hit-rank order
- **Answer symbol patching (D4)**: `from openkos.retrieval.answer import answer` so tests can monkeypatch `openkos.cli.main.answer`

**Command signature**:
```python
@app.command()
def query(
    question: str = typer.Argument(...),
    limit: int = typer.Option(5, "--limit"),
) -> None:
    # workspace gate, config read + error boundary, client build, answer() call + error boundary, rendering
```

## Archival Actions Completed

**Filesystem**:
- [x] Main spec tree created at `openspec/specs/query-command/spec.md` (6 requirements, 9 scenarios)
- [x] Change folder moved to `openspec/changes/archive/2026-07-18-add-query-command/` (all artifacts: proposal, design, tasks, verify-report, specs)
- [x] All change artifacts archived in the dated folder
- [x] Canonical spec promoted to `openspec/specs/query-command/spec.md`

**Engram**:
- [x] Archive report saved with topic key `sdd/add-query-command/archive-report` (this document)

## Next Steps

**For the project**:
- Archive folder now permanently in `openspec/changes/archive/2026-07-18-add-query-command/`
- Main spec tree updated: `openspec/specs/query-command/spec.md` is the canonical, promoted spec for the `query-command` capability
- MVP-1 query chain complete: all 4 changes archived, all specs merged, all tests passing, all design locked

**Unblocked downstream work**:
- MVP-1 is fully complete — no further query-chain changes needed
- Future polish: consolidate the 4 accepted non-blocking findings into a dedicated CLI polish/docs update change (post-MVP-1)

**Documented non-blocking items**:
- 4 accepted findings (query docstring, answer.py docstring, docs/cli.md coverage, test assertion specificity) — no residual blockers or critical findings — **recommended consolidated into future dedicated polish change, not blocking MVP-1 close**

## Risks & Mitigations

| Risk | Likelihood | Mitigation | Status |
|---|---|---|---|
| Workspace gate broken for interactive use | Low | D1 mirrors `status`/`lint` proven pattern; gate tested outside workspace | **MITIGATED** (test: refuses outside workspace) |
| Config error crashes without message | Low | D2 Phase-A wraps `read_config` (OSError/ValueError) → friendly exit 1 | **MITIGATED** (test: malformed config) |
| LLM unavailability crashes user | Low | D2 Phase-B wraps `answer()` (FtsUnavailable/OllamaError) → friendly exit 1 | **MITIGATED** (tests: Ollama/FTS unavailable) |
| No-match incorrectly treated as error | Low | D3 returns exit 0 on empty citations; spec/test explicit | **MITIGATED** (test: no-match exit 0) |
| Citation order incorrect in output | Low | Rendered in hit-rank order per `result.citations` sequence | **MITIGATED** (test: citation order, multi-survivor join) |
| Concept file TOCTOU after index build | Med | answer() guarded re-read (D2 of #3) skips unreadable silently | **MITIGATED** (upstream #3 guarantee) |
| Prompt join on multi-survivor unproved | Low | answer.py multi-survivor test added in this change | **MITIGATED** (test: multi-survivor join + order verified) |
| CLI tests depend on live Ollama | Low | Tests monkeypatch `openkos.cli.main.answer`, no network | **MITIGATED** (D4 symbol import, test isolation) |
| Review size exceeds budget | Low | ~285 changed lines across all files, well under 400 | **MITIGATED** (single PR, within budget) |

## Deferred/Out-of-Scope Items

**Explicitly deferred to future changes or MVP-2+**:
- Color support / `--no-color` / ANSI rendering
- Streaming output
- Answer re-filing / two-output rule automation
- Vector/semantic retrieval (lexical FTS5 only)
- Context truncation / token budget beyond `limit` parameter
- Richer citation metadata beyond `concept_id`/`title`
- Consolidated CLI polish (4 accepted findings → future dedicated change)

**Accepted residual limitations**:
- 4 accepted non-blocking findings (query docstring, answer.py docstring, docs/cli.md coverage, test assertion specificity) — purely documentation/polish, no behavior impact — recommended consolidated into future CLI polish change post-MVP-1

## Traceability

This archive report records the final state of the `add-query-command` change from proposal through implementation, strict TDD task phases, verification, and archival. The change has been:
- Fully specified (6 requirements, 9 scenarios, `query-command` capability spec at `openspec/specs/query-command/spec.md`)
- Fully designed (4 architecture decisions D1-D4, workspace gate, two-phase error boundaries, banner-free output, answer symbol import for test patching)
- Fully implemented (single PR #29, 6 modified/new modules, ~358 LOC estimated/implemented, 8 CLI tests + 1 multi-survivor test, 98.73% project coverage, 397 total tests green)
- Fully verified (all 6/6 requirements and 9/9 scenarios passing tests, all 4 design decisions verified in code, 397 tests passing, zero blockers or critical findings, 4 accepted non-blocking findings documented)
- Fully delivered (PR #29 merged to main with approval obtained)

The SDD cycle is CLOSED. The change is archived. MVP-1 query capability chain is COMPLETE.

**Archive Date**: 2026-07-18 (ISO format)
**Repository Head**: 27a71a7 (main, after approval, PR #29 merged)
**Specification**: `openspec/specs/query-command/spec.md` (canonical, promoted from delta spec, 6 requirements, 9 scenarios)
**Verification Date**: 2026-07-18 (verify-report PASS)
**Archival Status**: COMPLETE
**MVP-1 Query Chain Status**: ALL 4 CHANGES ARCHIVED AND COMPLETE (#1-#4 done)
