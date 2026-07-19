# Archive Report: add-query-answer

**Change**: add-query-answer | **Archived**: 2026-07-18 | **Status**: Complete | **Repository**: openkos (main d9176d3 after merge of PR #27)

This archive report closes the SDD cycle for the `add-query-answer` change. The feature implements the third piece of MVP-1's `query` capability — a pure library seam that orchestrates lexical retrieval, concept assembly, and cited-answer generation. It wires `state.fts` (retrieve) → `model.okf` (re-read with guarded skipping) → `llm.chat` (answer) into a single `answer()` function returning `AnswerResult(answer, citations)` with zero LLM calls on empty retrieval and full coverage of typed exception propagation. The implementation adds a new `retrieval/answer.py` library module with no CLI surface, undergoes strict TDD across 10 task phases, and achieves 100% line+branch coverage on the new module with ~98.69% project coverage.

## Change Summary

**Purpose**: Ship the query-answer retrieval+answer orchestrator (`query-answer` capability) for MVP-1's `query` command, enabling cited answers from the compiled bundle via lexical retrieval and injected LLM.

**Scope**:
- New `src/openkos/retrieval/` library package (no CLI, no workspace effect, no config import) with `answer.py` implementing `answer(question, *, bundle_dir, llm, limit=5) -> AnswerResult`
- Data types: `Citation(concept_id: str, title: str)` and `AnswerResult(answer: str, citations: list[Citation])`
- Core flow: `fts.build_index(bundle_dir).search(question, limit)` → guarded per-hit re-read (skip on `OSError`/parse error) → assemble context blocks → `llm.chat(messages)` if context non-empty, else return canned no-match
- Leaf-module discipline: `retrieval/` receives injected `llm: LLMBackend` (Protocol), never imports `openkos.config`
- Typed exception propagation: `FtsUnavailable` and `OllamaError` family (`OllamaUnavailable`, `OllamaModelNotFound`) propagate unswallowed to the caller
- Tests: 13 tests in `tests/unit/retrieval/test_answer.py` covering retrieve→assemble→answer happy path, zero-hit/all-unreadable degradation, partial-skip guidance, title-fallback, guarded re-read exception safety, and typed exception propagation
- No config schema changes, no CLI command, no ingest/forget/state modifications

**Architecture Decisions**:
- D1: `answer()` owns the index lifecycle per-call via `with fts.build_index(bundle_dir) as index:` — matches FTS single-shot usage for MVP-1 and simplifies the public surface
- D2: Guarded per-hit re-read mirrors `fts.py:173-182` — `try: text = (bundle_dir / f"{cid}.md").read_text(); meta, body = okf.load_frontmatter(text)` on each hit, skip on `OSError`/`UnicodeDecodeError` or parse exception, continue with remaining readable concepts
- D3: Zero-context short-circuit returns canned `NO_MATCH` (`"No matching concepts were found in the compiled bundle for this question."`) without calling LLM — triggered by both zero FTS hits and all-hits-skipped (D2 emptied context), cheapest and avoids hallucination
- D4: Citations = every concept_id placed in context, one `Citation(concept_id, title)` each in hit-rank order; title from frontmatter `title` field, fallback to `concept_id` if missing/empty
- D5: Prompt = 2 messages (system grounding + user context+question); system text baked in; user text = labeled `[concept_id: {id} — {title}]` blocks per hit + `QUESTION:` + question
- Zero ADRs created (all decisions additive, fully revertible via `git revert`)

**Change-scope verification**:
- `git diff main -- src/openkos/config.py` → empty (leaf discipline confirmed: no config import)
- Only two changed paths: `src/openkos/retrieval/__init__.py` (new), `src/openkos/retrieval/answer.py` (new), `tests/unit/retrieval/__init__.py` (new), `tests/unit/retrieval/test_answer.py` (new)
- No ingest/forget/state/CLI changes; 375 pre-existing regression tests pass unmodified

## Artifacts Archived

| Artifact | Location | Status |
|---|---|---|
| Proposal | `archive/2026-07-18-add-query-answer/proposal.md` | Moved from change folder; summarizes intent, scope, approach, risks, and MVP-1 context |
| Specification | `archive/2026-07-18-add-query-answer/specs/query-answer/spec.md` | Promoted to main spec tree at `openspec/specs/query-answer/spec.md` + moved to archive |
| Design | `archive/2026-07-18-add-query-answer/design.md` | Moved from change folder; documents D1-D5 decisions, data flow, interfaces, testing strategy, threat matrix |
| Tasks | `archive/2026-07-18-add-query-answer/tasks.md` | 25/25 checked across 10 phases (foundation, RED/GREEN cycles for happy path → zero-hit → exception propagation → layering guard → verification gate); all sub-tasks complete |
| Verification Report | `archive/2026-07-18-add-query-answer/verify-report.md` | PASS (all 7/7 requirements and 9/9 scenarios, all design decisions verified, zero blockers/critical findings, 1 accepted non-blocking SUGGESTION). Verify ran 388 tests total, 13 in `test_answer.py`, retrieval module 100% line+branch coverage, project 98.69% coverage. |

## Spec Merge Summary

| Action | Domain | Details |
|---|---|---|
| **NEW** | `query-answer` | Created new capability spec at `openspec/specs/query-answer/spec.md` |
| Requirements at archive time | 7 | Lexical Retrieval Drives Answer Assembly (1 scenario), Default Retrieval Limit (1 scenario), Zero Hits Return A Canned No-Match Result (1 scenario), Guarded Re-Read Skips Unreadable Concepts (2 scenarios), Typed Exceptions Propagate Unswallowed (2 scenarios), Module Is Config-Free And Backend-Injected (1 scenario), Citations Reflect Only Context-Included Concepts (1 scenario) |
| Total scenarios at archive time | 9 | Full coverage of retrieve→answer, zero-hits, all-unreadable, partial-skip, exception propagation, no-config, citation integrity, title-fallback |
| Source | Delta spec from change folder | `/openspec/changes/add-query-answer/specs/query-answer/spec.md` promoted to `/openspec/specs/query-answer/spec.md` |
| Merge mode | NEW capability | The `query-answer` capability did not exist before; this change establishes it. No existing spec to merge into. |
| Divergence note | Archived historical copy | The archived delta copy at `openspec/changes/archive/2026-07-18-add-query-answer/specs/query-answer/spec.md` is left unchanged as the historical record; the canonical `openspec/specs/query-answer/spec.md` is the source of truth for this capability going forward. |

## Verification Status

**Final Verdict**: PASS (all requirements and scenarios verified, all design decisions locked, zero blockers or critical findings)

**Evidence Summary**:
- All 9/9 spec scenarios covered by passing tests: `test_matching_concepts_produce_a_cited_answer`, `test_caller_omits_limit_search_called_with_five`, `test_no_matching_concepts_returns_canned_no_match`, `test_one_hit_vanished_skips_it_and_still_answers_with_the_rest`, `test_all_hits_unreadable_degrades_to_no_match`, `test_fts_unavailable_propagates_unswallowed`, `test_llm_chat_error_propagates_unswallowed`, `test_answer_module_does_not_import_config` (ast-scan), `test_one_hit_vanished_skips_it_and_still_answers_with_the_rest` + `test_unparseable_frontmatter_hit_is_skipped` (citation integrity)
- Design decision verification: D1 (per-call index via `with`), D2 (guarded re-read with try/except scoping), D3 (zero-context short-circuit), D4 (citation construction and title fallback), D5 (2-message system+user prompt shape, exact NO_MATCH string, labeled context blocks)
- Test execution (final test suite, per verify-report): **388 passed, 0 failed, 0 skipped** (full suite); **13 tests** in `tests/unit/retrieval/test_answer.py` all passing
- Coverage: `src/openkos/retrieval/answer.py` **100% line + 100% branch**, `src/openkos/retrieval/__init__.py` **100%**; Project total **98.69%** (floor 90%, enforced)
- Quality gates:
  - `uv run ruff check .` pass (exit 0, all checks pass)
  - `uv run ruff format --check .` pass (all modified source files clean)
  - `uv run mypy .` pass (strict mode, 43 source files, no issues)
- Byte-unchanged: `git diff main -- src/openkos/config.py` → empty (leaf discipline verified)
- Regression suite: `tests/unit/cli` (98 tests incl. ingest/forget/lint/status) all pass unmodified, confirming no lifecycle changes

## Delivery History

This change was delivered as a single PR after orchestrator approval:
- **PR #27** (merged to main, 2026-07-18): Complete query-answer retrieval+assembly+cited-answer implementation — `retrieval/__init__.py` (package marker) + `retrieval/answer.py` (`answer()`, `AnswerResult`, `Citation`, prompt consts, `_assemble_context()`, `_build_messages()` helpers) + 13 unit tests. Strict TDD across 10 phases: foundation → RED/GREEN happy path → RED/GREEN zero-hit/degradation → RED/GREEN exception propagation → layering guard → verification gate. All 25 tasks marked complete during apply phase; verify-report confirms all 7/7 requirements and 9/9 scenarios passing.

**Repository State**: main @ d9176d3 (commit: "feat(retrieval): add query-answer orchestrator for MVP-1 query capability (#3, cited-answer library, no CLI)" after approval)

## Review Gate & Closure

**Bounded Review History**:
No bounded review lineages needed (strict TDD discipline during apply phase delivered a clean tree). Final verification run (verify-report date 2026-07-18): all 388 tests passing, all 7/7 requirements verified, all 9/9 scenarios passing, 100% line+branch coverage on new modules, 0 blockers, 0 critical findings.

**Current status**:
- PR #27 merged to main
- All 388 tests passing (13 in test_answer.py), 100% retrieval module coverage, ~98.69% project coverage
- All 9 spec scenarios passing runtime tests
- All 5 architecture decisions verified in code
- No blockers remain; all strict TDD gates passed
- Change complete and archived

## Implementation Details

**Modules added/modified**:
- `src/openkos/retrieval/__init__.py`: Package marker with module docstring
- `src/openkos/retrieval/answer.py`: `Citation` and `AnswerResult` frozen dataclasses, `NO_MATCH` const, `answer()` public function, `_assemble_context()` and `_build_messages()` helpers for guarded re-read + prompt construction
- `src/openkos/config.py`: Untouched (byte-unchanged, leaf discipline confirmed)
- `tests/unit/retrieval/__init__.py`: Test package marker (empty)
- `tests/unit/retrieval/test_answer.py`: 13 tests covering happy path/limit/zero-hit/unreadable/partial-skip/title-fallback/exception-propagation/layering-guard

**Key implementation patterns**:
- **Index lifecycle (D1)**: `with fts.build_index(bundle_dir) as index: hits = index.search(question, limit)` — index built and torn down within single call, no state leakage to caller
- **Guarded re-read (D2)**: Per-hit loop with nested try/except: outer catches `OSError`/`UnicodeDecodeError` on file read, inner catches broad `except Exception` on frontmatter parse; both skip the hit without raising
- **Zero-context short-circuit (D3)**: `if not context: return AnswerResult(NO_MATCH, [])` before message assembly; only calls LLM if at least one concept body is in context
- **Title resolution (D4)**: `title = str(meta.get("title") or "") or hit.concept_id` — ensures non-empty human label in every citation
- **Prompt assembly (D5)**: Build list of `[concept_id: {id} — {title}]\n{body}` blocks, join with `\n\n`, construct `Message(role="system", ...)` + `Message(role="user", ...)`, pass to `llm.chat()`

**API surfaces**:
```python
@dataclass(frozen=True)
class Citation:
    concept_id: str
    title: str

@dataclass(frozen=True)
class AnswerResult:
    answer: str
    citations: list[Citation]

def answer(question: str, *, bundle_dir: Path,
           llm: LLMBackend, limit: int = 5) -> AnswerResult:
    """Retrieve concepts, assemble context, call LLM, return cited answer."""
```

## Accepted Non-Blocking Follow-Up

**SUGGESTION (verified but deferred to follow-up task)**: Multi-survivor ordering/join coverage gap.
- No test exercises a scenario where 2+ concepts survive the guarded re-read together with the LLM called
- All multi-hit tests reduce to exactly one surviving concept in context (others vanished/unparseable)
- Consequently, the "citations in hit-rank order" guarantee (implemented via hit-iteration appends) and the multi-block prompt join (`"\n\n".join(context_blocks)`) are implemented but unproved by a multi-survivor test
- Classified SUGGESTION (not CRITICAL/WARNING) — per-hit logic is fully proved; this was explicitly flagged as an accepted follow-up in verification brief
- **Recommended action**: Add one 2-survivor test in `add-query-command` (change #4) or a small addendum task before CLI behavior relies on multi-survivor prompt-join

## Archival Actions Completed

**Filesystem**:
- [x] Main spec tree created at `openspec/specs/query-answer/spec.md` (7 requirements, 9 scenarios)
- [x] Change folder moved to `openspec/changes/archive/2026-07-18-add-query-answer/` (all artifacts: proposal, design, tasks, verify-report, specs)
- [x] All change artifacts archived in the dated folder
- [x] Canonical spec promoted to `openspec/specs/query-answer/spec.md`

**Engram**:
- [x] Archive report saved with topic key `sdd/add-query-answer/archive-report` (this document)

## Next Steps

**For the project**:
- Archive folder now permanently in `openspec/changes/archive/2026-07-18-add-query-answer/`
- Main spec tree updated: `openspec/specs/query-answer/spec.md` is the canonical, promoted spec for the `query-answer` capability
- No follow-up changes required for this change (MVP-1 query-answer orchestrator is complete)

**Unblocked downstream changes**:
- `add-query-command` (MVP-1 change #4) — unblocked, can now depend on `retrieval.answer()` for the `openkos query` CLI command

**Documented non-blocking items**:
- 1 accepted SUGGESTION (multi-survivor test coverage deferred to #4) — no residual blockers or critical findings

## Risks & Mitigations

| Risk | Likelihood | Mitigation | Status |
|---|---|---|---|
| Guarded re-read skips hit but LLM still called | Low | D2 scoping ensures OSError/parse exceptions don't affect LLM call; D3 checks if context empty before LLM | **MITIGATED** (tests: one-hit-vanished, partial-skip, all-unreadable) |
| Unbounded context bloats LLM prompt | Med | D1 bounds by `limit` concepts; token budget truncation is named MVP-1 non-goal | **MITIGATED** (limit parameter enforced in retrieve layer) |
| Concept file TOCTOU race (vanished after index build) | Med | D2 guarded re-read skips silently; caller never sees TOCTOU crash | **MITIGATED** (mirrors fts.py precedent) |
| Ungrounded LLM answer on zero hits | Med | D3 zero-context short-circuit returns canned message without LLM call | **MITIGATED** (tests: zero-hit, all-unreadable) |
| FtsUnavailable/OllamaError suppress | Low | Typed propagation (D5 does not catch) lets caller degrade | **MITIGATED** (tests: exception-propagation) |
| Title missing from citation | Low | D4 fallback to concept_id ensures non-empty | **MITIGATED** (tests: title-fallback) |
| Prompt join on multi-survivor unproved | Med | Per-hit logic proved; multi-survivor join unproved but implemented | **ACCEPTED** (SUGGESTION deferred to #4) |

## Deferred/Out-of-Scope Items

**Explicitly deferred to MVP-2 or later changes**:
- Context truncation / token budget beyond `limit` parameter
- Vector, semantic, or hybrid retrieval (lexical FTS5 only for MVP-1)
- Filing the answer back as a concept (two-output rule)
- Richer citation metadata (score, provenance, sensitivity beyond `concept_id`/`title`)
- CLI command (deferred to MVP-1 change #4, `add-query-command`)
- Config wiring and `openkos.config` integration (deferred to #4)

**Accepted residual limitations**:
- None beyond intentional MVP-1 deferrals and the one accepted SUGGESTION (multi-survivor test coverage in #4). The module achieves its scope: pure library query-answer orchestrator, no CLI, no config, injectable LLM seam for testability, full spec coverage, all design decisions locked and verified.

## Traceability

This archive report records the final state of the `add-query-answer` change from proposal through implementation, strict TDD task phases, verification, and archival. The change has been:
- Fully specified (7 requirements, 9 scenarios, `query-answer` capability spec at `openspec/specs/query-answer/spec.md`)
- Fully designed (5 architecture decisions D1-D5, guarded per-hit re-read, zero-context short-circuit, typed exception propagation, leaf-module discipline)
- Fully implemented (single PR #27, 4 new modules + 1 test module, ~370 LOC estimated/implemented, 13 tests, 100% retrieval module coverage, ~98.69% project coverage)
- Fully verified (all 7/7 requirements and 9/9 scenarios passing tests, all 5 design decisions verified in code, 388 tests passing, zero blockers or critical findings, 1 accepted non-blocking SUGGESTION)
- Fully delivered (PR #27 merged to main with approval obtained)

The SDD cycle is CLOSED. The change is archived and ready for downstream change `add-query-command` (MVP-1 #4) to build the `openkos query` CLI command.

**Archive Date**: 2026-07-18 (ISO format)
**Repository Head**: d9176d3 (main, after approval, PR #27 merged)
**Specification**: `openspec/specs/query-answer/spec.md` (canonical, promoted from delta spec, 7 requirements, 9 scenarios)
**Verification Date**: 2026-07-18 (verify-report PASS)
**Archival Status**: COMPLETE
