# Verify Report: `add-query-command`

## Change
MVP-1 query chain #4 — the `openkos query "<question>"` CLI command, plus
two test/doc-only follow-ups to the already-archived `query-answer`
capability (`answer.py` `_SYSTEM_PROMPT` docstring, a multi-survivor
citation-ordering test).

## Verdict: PASS

Blockers: 0. Critical findings: 0. Warnings: 0 (4 previously accepted
non-blocking review findings noted below, unchanged).

## Spec coverage

Requirements: **6/6** implemented and tested.
Scenarios: **9/9** covered by tests. (Note: the task brief said "12
scenarios" — the actual `specs/query-command/spec.md` contains 9
`#### Scenario:` headers across its 6 requirements; verified by direct
count. This is a documentation-count discrepancy in the task brief, not a
gap in the spec or its coverage.)

| # | Requirement | Scenario | Test |
|---|---|---|---|
| 1 | Workspace Gate | Run outside a workspace | `test_query_refuses_when_not_a_workspace` |
| 1 | Workspace Gate | Run inside a workspace | `test_query_matching_answer_renders_citations_in_hit_rank_order` (combined) |
| 2 | Happy-Path Answer Rendering | Matching answer with citations | `test_query_matching_answer_renders_citations_in_hit_rank_order` (combined) |
| 3 | No-Match Is Not An Error | Zero matching concepts | `test_query_no_match_renders_answer_line_alone` |
| 4 | `--limit` Option | Caller overrides the default limit | `test_query_limit_flag_is_forwarded_unchanged` |
| 4 | `--limit` Option | Caller omits `--limit` | `test_query_omitted_limit_defaults_to_five` |
| 5 | LLM And Index Errors Map To Exit 1 | Ollama backend unreachable | `test_query_ollama_unavailable_maps_to_exit_one` |
| 5 | LLM And Index Errors Map To Exit 1 | FTS index unavailable | `test_query_fts_unavailable_maps_to_exit_one` |
| 6 | Citations Reflect The Answer Exactly | Citation order matches the answer | `test_query_matching_answer_renders_citations_in_hit_rank_order` (combined) |

An 8th CLI test, `test_query_malformed_config_maps_to_exit_one_before_calling_answer`,
covers the D2 Phase-A `read_config` error boundary. This branch has no
corresponding spec scenario (the spec's error-boundary requirement only
names `FtsUnavailable`/`OllamaError`), so it is extra coverage beyond the
spec, not a gap.

`answer.py` follow-ups: `_SYSTEM_PROMPT` docstring added (D5 text, no
signature change); `test_multiple_surviving_hits_cite_in_rank_order_and_join_context`
added to `test_answer.py`, asserting citation-list ORDER (exact sequence
equality, not just membership) and that the joined user-context message
contains both `[concept_id: ... — ...]` blocks `\n\n`-joined in rank order,
verified via `content.index(stoicism_block) < content.index(epictetus_block)`.
Not vacuous — confirmed by reading the assertions directly.

## Test suite / gate results

- `uv run pytest --cov=openkos --cov-report=term-missing`: **397 passed, 0
  failed**, exit 0. Total coverage 98.73% (required 90%). `cli/main.py`
  99% (2 pre-existing uncovered branches in `forget`'s
  `_resolve_concept_path`, unrelated to this change). `retrieval/answer.py`
  100%. `state/fts.py` 100%.
- `uv run ruff check .`: All checks passed, exit 0.
- `uv run ruff format --check .`: 44 files already formatted, exit 0.
- `uv run mypy .`: Success, no issues found in 44 source files (strict),
  exit 0.

## Error boundaries confirmed

- Malformed `openkos.yaml` (Phase-A `read_config` guard, D2) → exit 1, no
  traceback, `answer()` never called
  (`test_query_malformed_config_maps_to_exit_one_before_calling_answer`).
- `FtsUnavailable` / `OllamaError` family raised from `answer()` (Phase-B
  guard, D2) → exit 1, no traceback
  (`test_query_fts_unavailable_maps_to_exit_one`,
  `test_query_ollama_unavailable_maps_to_exit_one`).
- No-match (`AnswerResult(NO_MATCH, [])`) → exit 0, no `Citations:` section
  (`test_query_no_match_renders_answer_line_alone`).

All three verified directly by reading the assertions, not merely by
exit-code inspection.

## Architectural/regression checks

- `tests/unit/state/test_fts.py::test_ingest_and_forget_do_not_reference_state_fts`
  (rewritten from `test_cli_module_does_not_import_state_fts`) still asserts
  a real, failing-capable guard: it AST-parses `cli/main.py`, isolates the
  source of `ingest` and `forget` specifically, and asserts neither
  function's source contains `fts` or `state`. It legitimately allows
  `query`'s `FtsUnavailable` import (function-scoped, not module-scoped) and
  would still fail if `ingest`/`forget` regressed to reference `fts`/`state`.
  Confirmed not gutted — the invariant the `fts-state` spec actually
  protects ("dormant... until a future command calls it") is intact.
- `cli` importing `openkos.state.fts.FtsUnavailable` does not violate
  `docs/architecture.md`'s layering convention (`model`/`bundle`/`state` =
  canonical, does not depend on `retrieval`/`graph`/`memory` = derived;
  derived depends on canonical, never the reverse). `cli/` is the thin
  top-layer entry point over the engine, not part of the canonical/derived
  dependency chain itself — it already imports from both layers elsewhere
  (`bundle`, `model.okf` = canonical; `retrieval.answer` = derived). No
  violation.

## Accepted non-blocking findings (from lineage review-1693068357b8de6f)

Confirmed present, explicitly NOT treated as blockers per instructions:

1. `query`'s docstring (line 666, `cli/main.py`) says "`llm=client`" where
   the actual variable is `llm` (`llm = OllamaClient(model=cfg.model)`,
   line 695).
2. `retrieval/answer.py`'s module docstring (line 9) still says "propagate
   unswallowed to the future `query` command (#4)" — `query` now exists.
3. `docs/cli.md`'s `openkos query` section documents the `FtsUnavailable`/
   `OllamaError` → exit 1 mapping but omits the malformed-config
   (`read_config` OSError/ValueError) failure mode.
4. No test asserts `OllamaClient` is constructed with `model=cfg.model`
   (only that the command builds SOME client and reaches `answer()`).

None of these alter behavior, spec conformance, or test correctness.

## Tasks vs code state

All 17 planned tasks + 1 unplanned fix (18/18) in `tasks.md` are marked
`[x]`, matching apply-progress (`sdd/add-query-command/apply-progress`,
Engram #979). File-by-file cross-check against the apply-progress table
confirms every listed file change is present and matches the described
content: `cli/main.py` (+69, `query` command), `test_query.py` (new, 213
lines, 8 tests), `answer.py` (+4, docstring only), `test_answer.py` (+47,
multi-survivor test), `test_fts.py` (rewritten guard test), `docs/cli.md`
(expanded stub).

## Next recommended

`sdd-archive`, after PR merge.
