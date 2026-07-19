## Verification Report: surface-query-fts-llm-shortcircuit

**Mode**: Strict TDD, full artifact set (spec + design + tasks + apply-progress).

### Task Completeness
44/44 tasks checked `[x]` across 7 phases (RED/GREEN/REFACTOR for `answer.py`, RED/GREEN/REFACTOR for `main.py`, full-suite+docs phase). Task count verified by direct enumeration against tasks artifact. Matches git diff scope exactly (6 files: `src/openkos/retrieval/answer.py`, `src/openkos/cli/main.py`, `tests/unit/retrieval/test_answer.py`, `tests/unit/cli/test_query.py`, `docs/cli.md`, `docs/user-journey.md`).

### Build/Test Evidence
- `uv run pytest --cov=openkos --cov-report=term-missing -q`: **447 passed**, 0 failed. Coverage **98.73%** total (gate 90% — reached). `answer.py` 100%, `cli/main.py` 99% (misses are 3 pre-existing unrelated lines: 435, 542->544, 847->exit).
- `uv run ruff check` on 4 changed source/test files: **clean**, no issues.
- `uv run mypy` on `retrieval/answer.py` + `cli/main.py`: **clean**, no issues.
- `git diff --stat HEAD`: 6 files changed, 372 insertions(+), 22 deletions(-) = 394 lines — matches task claim exactly, within 400-line budget.

### Spec Requirement Compliance (source-inspected + test-covered)
| Requirement | Evidence | Status |
|---|---|---|
| `AnswerResult` carries `fts_hit_count`/`llm_invoked`/`no_match_cause`/`skip_notices` | `answer.py:54-77`; `test_answer_result_is_a_frozen_dataclass` | PASS |
| Successful answer sets `llm_invoked=True`, cause `none`/derived cited_count | `answer.py:151-159`; CLI test asserts `len(citations)` | PASS |
| Zero hits -> `zero_hits` cause, LLM not invoked | `_classify_no_match` (answer.py:117-126); `test_query_zero_hits_renders_zero_hits_message` | PASS |
| All hits unreadable -> `all_unreadable` cause | same helper; `test_query_all_unreadable_renders_corruption_message` | PASS |
| Empty/whitespace question -> `empty_query`, LLM never invoked | `_classify_no_match` checks `question.split()` before hits; `test_empty_question_never_invokes_llm_and_sets_empty_query_cause`, `test_query_empty_question_renders_prompt_message` | PASS |
| Skip notices carried regardless of match outcome | `answer()` reads `index.skipped` inside `with` block unconditionally (answer.py:139); `test_skip_notices_carried_on_matched_path`/`..._on_no_match_path` | PASS |
| `answer.py` stays config-free | `test_answer_module_does_not_import_config` (ast-based) present unmodified, green | PASS |
| Always-on stderr `retrieval:` summary, every run | `main.py:825-831`; all tests assert exact stderr string | PASS |
| STDOUT unchanged in shape on success | `test_query_matching_answer_renders_citations_in_hit_rank_order` asserts `result.stdout` equals exactly the pre-change answer+Citations shape, byte-for-byte, with summary isolated to stderr | PASS |
| 3 distinct, actionable no-match STDOUT messages | `_no_match_message` (main.py:724-743): zero_hits -> "try different wording / openkos status"; all_unreadable -> "may be corrupted, run openkos lint"; empty_query -> "provide a question" example. 3 dedicated tests assert exact text | PASS |
| Skip notices on stderr, worded as whole-bundle signal | `main.py:832-840`: "skipped while building the search index (whole-bundle, not this query's hits)"; tests for skip-notice behavior | PASS |

### Spec/Design Drift Judgment (flagged by apply-progress)
Spec artifact specifies hyphenated `no_match_cause` values (`"zero-hits"`/`"hits-unreadable"`/`"empty-query"`) and a separate stored `cited_count` field. Design and tasks lock in the underscored `Literal["none","empty_query","zero_hits","all_unreadable"]` and explicitly reject a stored `cited_count`. Implementation followed design/tasks.

**Judgment: IMMATERIAL to observable behavior — PASS, not a blocker.**
- `no_match_cause` is purely internal; user-facing text comes from `_no_match_message()`'s hardcoded prose.
- `cited_count` derived via `len(result.citations)` yields identical stderr output.
- All internal enum values consistent across answer.py and main.py.

### Issues
- CRITICAL: 0
- WARNING: 0
- SUGGESTION: 1 (reconcile spec artifact's hyphenated `no_match_cause` values / stored-`cited_count` wording for documentation accuracy)

### Verdict: **PASS**
All 44 tasks complete. 447/447 tests pass, 98.73% coverage (gate 90%). Every spec requirement satisfied with runtime test coverage. Docs accurately reflect behavior. Ready for `sdd-archive`.
