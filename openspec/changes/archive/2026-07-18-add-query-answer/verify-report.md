```yaml
schema: gentle-ai.verify-result/v1
verdict: pass
blockers: 0
critical_findings: 0
requirements: 7/7
scenarios: 9/9
test_command: uv run pytest --cov=openkos --cov-report=term-missing
test_exit_code: 0
test_output_hash: sha256:267aadddb884a3544fd2c4f80f8e523175af7155cafb9fdfa46b7f68a9dc61b6
build_command: uv run mypy .
build_exit_code: 0
build_output_hash: sha256:355a435c33a29b8f3f0db0815b88731b7ae35c080fe47fdbc27e9330572711aa
```

# Verify Report: add-query-answer

## Change
`add-query-answer` (change #3 of MVP-1 `query` capability chain) — pure library seam: `retrieval/answer.py`'s `answer()` retrieves lexical hits via `state.fts.build_index(...).search(...)`, assembles a guarded per-hit re-read into an LLM context, calls an injected `LLMBackend` exactly once, and returns a cited `AnswerResult`. No CLI surface, dormant until `add-query-command` consumes it.

## Mode
Strict TDD. Full artifact set present: spec, design, tasks, apply-progress. All 25/25 tasks (10 phases) marked `[x]` in `tasks.md` — confirmed via direct read (`rg -c "\[ \]" tasks.md` returned zero matches).

## Note on scenario count
The verification brief stated "7 requirements, 10 scenarios." Direct count against `specs/query-answer/spec.md` (`rg -c "^#### Scenario"`) yields **9 scenarios**, not 10. This is a discrepancy in the brief, not in the artifact — the spec itself is internally consistent (7 requirements, 9 scenarios, all covered). Reporting the actual count below.

## Independent Gate Re-run (exact numbers, this session)

| Command | Exit | Result |
|---|---|---|
| `uv run pytest --cov=openkos --cov-report=term-missing` | 0 | **388 passed**, 0 failed. `src/openkos/retrieval/answer.py`: 47 stmts / 4 branches, **100% line, 100% branch, 0 missing**. `src/openkos/retrieval/__init__.py`: 100%. Project TOTAL: 916 stmts, 228 branches, **98.69% coverage** — above the enforced 90% floor. |
| `uv run ruff check .` | 0 | All checks passed! |
| `uv run ruff format --check .` | 0 | 43 files already formatted |
| `uv run mypy .` | 0 | Success: no issues found in 43 source files |

All numbers independently reproduced this session and match `apply-progress` (388/388, 100% line+branch on `retrieval/answer.py`) exactly.

## Task Completeness
25/25 tasks across 10 phases checked `[x]`. Every task maps to passing tests in `tests/unit/retrieval/test_answer.py` (13 tests, independently counted, matches apply-progress claim exactly).

## Spec Conformance Matrix (7 requirements / 9 scenarios, 9/9 covered)

| Requirement | Scenario | Covering test | Status |
|---|---|---|---|
| Lexical Retrieval Drives Answer Assembly | Matching concepts produce a cited answer | `test_matching_concepts_produce_a_cited_answer` | PASS |
| Default Retrieval Limit | Caller omits limit | `test_caller_omits_limit_search_called_with_five` | PASS |
| Zero Hits Return A Canned No-Match Result | No matching concepts found | `test_no_matching_concepts_returns_canned_no_match` | PASS |
| Guarded Re-Read Skips Unreadable Concepts | One hit vanished after indexing | `test_one_hit_vanished_skips_it_and_still_answers_with_the_rest` | PASS |
| Guarded Re-Read Skips Unreadable Concepts | All hits unreadable | `test_all_hits_unreadable_degrades_to_no_match` | PASS |
| Typed Exceptions Propagate Unswallowed | FTS index unavailable | `test_fts_unavailable_propagates_unswallowed` | PASS |
| Typed Exceptions Propagate Unswallowed | LLM backend fails | `test_llm_chat_error_propagates_unswallowed` | PASS |
| Module Is Config-Free And Backend-Injected | Module has no config dependency | `test_answer_module_does_not_import_config` (ast-scan) | PASS |
| Citations Reflect Only Context-Included Concepts | Citation set matches context set exactly | `test_one_hit_vanished_skips_it_and_still_answers_with_the_rest`, `test_unparseable_frontmatter_hit_is_skipped` | PASS |

Prompt-shape (D5) is additionally covered by `test_prompt_shape_has_system_grounding_and_labeled_context_blocks`, and title-fallback (D4) by `test_missing_title_falls_back_to_concept_id` — both beyond the minimum required scenario set.

## Locked-Decision Conformance (D1-D5)
All 5 hold: D1 per-call index lifecycle (`with fts.build_index(bundle_dir) as index:` — confirmed in source, index never leaks past the `with`); D2 guarded per-hit re-read (two separate `try/except` blocks: `OSError`/`UnicodeDecodeError` on read, broad `except Exception` — `# noqa: S112` justified — on frontmatter parse); D3 zero-context short-circuit before message assembly, no LLM call; D4 title fallback `str(meta.get("title") or "") or hit.concept_id`; D5 exact 2-message system+user prompt shape, exact `NO_MATCH` string, exact `[concept_id: {id} — {title}]` block label — all verified against both source and passing tests.

## Architectural Constraints
- **Config-free**: `src/openkos/retrieval/answer.py` imports only `openkos.llm.base`, `openkos.model.okf`, `openkos.state.fts` (confirmed by direct read of the import block and by the passing `ast`-scan test). No `openkos.config` import anywhere in `retrieval/`.
- **Layering**: retrieval depends only on canonical layers (`state.fts`, `model.okf`) + `llm.base` (Protocol/TypedDict, no concrete backend construction) — matches design.md's stated dependency graph.
- **Synchronous core**: `answer()` and all helpers (`_assemble_context`, `_build_messages`) are plain `def`, no `async`/coroutines.
- **Zero blast-radius on config**: `git diff --stat -- src/openkos/config.py` against main is empty.

## Typed Exception Propagation
Confirmed not swallowed: `_assemble_context`'s only `try/except` blocks scope narrowly around the per-file read (`OSError`, `UnicodeDecodeError`) and the frontmatter parse (`except Exception`) — neither wraps `fts.build_index(...)` (outside any try in `answer()`) nor `llm.chat(...)` (outside any try in `answer()`). Both `test_fts_unavailable_propagates_unswallowed` and `test_llm_chat_error_propagates_unswallowed` pass, using `pytest.raises(fts.FtsUnavailable)` and `pytest.raises(OllamaUnavailable)` respectively against real exception instances raised by fakes — not mocks asserting call-was-made.

## Non-Goals + Layering Respected
No CLI added. `retrieval/` package is additive-only (2 new files: `__init__.py`, `answer.py`), imported by nothing else in the repo yet (dormant, matches design.md's stated rollout). No context truncation/token-budget logic beyond `limit`. No vector/semantic retrieval. Citation metadata limited to `concept_id`/`title` as specified.

## Regression Check
Full suite 388 passed, 0 failed — up from the pre-change 375 baseline recorded in apply-progress (13 new tests, all passing, zero pre-existing test broke).

## Known Non-Blocking Follow-Up (accepted, not a verify blocker)
No test exercises a multi-survivor scenario (2+ concepts surviving the guarded re-read together with the LLM called). All passing multi-hit tests (`test_one_hit_vanished_skips_it_and_still_answers_with_the_rest`, `test_unparseable_frontmatter_hit_is_skipped`) reduce to exactly one surviving concept in context. Consequently:
- The "citations in hit-rank order" guarantee (`answer.py:50`/design D4) is implemented (citations appended in hit-iteration order, same loop that reads `hits`) but unproved by a test with 2+ ordered survivors.
- The multi-block prompt join (`answer.py:82`, `"\n\n".join(context_blocks)`) is implemented and exercised by `test_prompt_shape_has_system_grounding_and_labeled_context_blocks`, but only with a single block — the `"\n\n"` separator between two-or-more blocks is unproved.

This is a genuine coverage gap in scenario breadth (not in line/branch coverage, which is 100%), consistent with what a mutation-testing pass on multi-hit ordering would likely catch. Classified **SUGGESTION**, not CRITICAL/WARNING — the guarded-re-read and citation-construction logic is otherwise fully proved per-hit, and this was explicitly flagged as an accepted follow-up rather than a blocker in the verification brief. Recommend a follow-up task (in `add-query-command` or a small addendum) adding one 2-survivor test before this prompt-join code path is relied on by production CLI behavior.

## Assertion Quality: 0 CRITICAL, 0 WARNING
No tautologies, ghost loops, or smoke-test-only patterns found across the 13 tests in `test_answer.py`. Fakes (`_FakeLLM`, `_RecordingIndex`) are structural (record real call arguments/counts), not blanket mocks; exception tests assert real typed exceptions propagate via `pytest.raises`, not call-count assertions.

## Issues
CRITICAL: None. WARNING: None. SUGGESTION: 1 (multi-survivor ordering/join coverage gap, disclosed above, non-blocking, pre-accepted by the verification brief).

## Final Verdict: PASS
Requirements: 7/7. Scenarios: 9/9. Blockers: 0. Critical findings: 0.
