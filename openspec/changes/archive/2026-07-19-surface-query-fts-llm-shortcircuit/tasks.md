# Tasks: Surface the FTS -\> LLM short-circuit in `openkos query`

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~180-260 (answer.py ~50, main.py ~70, test_answer.py ~60, test_query.py ~60, docs ~15) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | single-pr |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Full change: `answer.py` metadata + `main.py` rendering + tests + docs | PR 1 (size:exception if apply forecasts >400 after diff) | `uv run pytest tests/unit/retrieval/test_answer.py tests/unit/cli/test_query.py -q` | `openkos query "<question>"` in a real initialized workspace with a compiled bundle | Revert the single commit/PR; no other feature depends on these fields |

Actual changed lines (apply, via `git diff --stat`): 394 (372 insertions + 22 deletions across 6 files) -- within the 400-line budget, no size:exception needed.

## Phase 1: RED — `retrieval/answer.py` metadata (tests/unit/retrieval/test_answer.py)

- [x] 1.1 Add `.skipped: list[str]` attribute to `_RecordingIndex.__init__` (default `[]`, overridable) so tests can set build-time skip notices.
- [x] 1.2 Update `test_answer_result_is_a_frozen_dataclass` to construct `AnswerResult` with the new required fields (`fts_hit_count`, `llm_invoked`, `no_match_cause`, `skip_notices`) and assert each.
- [x] 1.3 Update every existing `AnswerResult(...)`/`answer()`-return assertion in the file (happy path, default limit, all no-match branches around lines 173/192) for the new fields; keep `NO_MATCH` string assertions unchanged.
- [x] 1.4 Add RED test: successful answer sets `llm_invoked=True`, `no_match_cause="none"`, `fts_hit_count == len(hits)`.
- [x] 1.5 Add RED test: zero FTS hits -> `fts_hit_count == 0`, `llm_invoked=False`, `no_match_cause="zero_hits"`, `llm.chat` never called.
- [x] 1.6 Add RED test: hits present but all unreadable/unparseable -> `fts_hit_count > 0`, `len(citations) == 0`, `llm_invoked=False`, `no_match_cause="all_unreadable"`.
- [x] 1.7 Add RED test: whitespace-only/empty `question` -> `llm.chat` never invoked, `no_match_cause="empty_query"`.
- [x] 1.8 Add RED test: `_RecordingIndex.skipped` non-empty on a matched path -> `AnswerResult.skip_notices` equals it.
- [x] 1.9 Add RED test: `_RecordingIndex.skipped` non-empty on a no-match path -> `AnswerResult.skip_notices` equals it.
- [x] 1.10 Confirm `test_answer_module_does_not_import_config` (ast-based) still exists unmodified and will stay green (Literal/no new imports).
- [x] 1.11 Run `uv run pytest tests/unit/retrieval/test_answer.py -q` and confirm all new/updated tests fail (RED) against unmodified `answer.py`. — confirmed: 7 failed, 11 passed against unmodified `answer.py`.

## Phase 2: GREEN — implement `retrieval/answer.py`

- [x] 2.1 Add `from typing import Literal` and define `NoMatchCause = Literal["none", "empty_query", "zero_hits", "all_unreadable"]`.
- [x] 2.2 Add `fts_hit_count: int`, `llm_invoked: bool`, `no_match_cause: NoMatchCause`, `skip_notices: list[str]` fields to `AnswerResult` (all required, no defaults).
- [x] 2.3 Add `_classify_no_match(question: str, hits: list[fts.FtsHit]) -> NoMatchCause` helper: `not question.split()` -> `"empty_query"`; `not hits` -> `"zero_hits"`; else -> `"all_unreadable"`.
- [x] 2.4 In `answer()`, read `index.skipped` inside the `with fts.build_index(...)` block and capture `fts_hit_count = len(hits)` alongside existing `hits`/`context_blocks`/`citations` computation.
- [x] 2.5 Wire the no-match return: `AnswerResult(answer=NO_MATCH, citations=[], fts_hit_count=..., llm_invoked=False, no_match_cause=_classify_no_match(question, hits), skip_notices=skip_notices)`.
- [x] 2.6 Wire the success return: `AnswerResult(answer=reply, citations=citations, fts_hit_count=..., llm_invoked=True, no_match_cause="none", skip_notices=skip_notices)`.
- [x] 2.7 Run `uv run pytest tests/unit/retrieval/test_answer.py -q` and confirm all tests pass (GREEN). — confirmed: 18 passed.

## Phase 3: REFACTOR — `retrieval/answer.py`

- [x] 3.1 Re-read `answer()` and `_classify_no_match` for duplication/clarity; extract nothing that breaks the config-free/render-free constraint. — no duplication found; kept as-is.
- [x] 3.2 Update the module docstring / `AnswerResult` field docstrings to describe the 4 new fields (mirrors existing docstring style on `answer`/`citations`). — done inline during Phase 2 implementation.
- [x] 3.3 Re-run `uv run pytest tests/unit/retrieval/test_answer.py -q` to confirm refactor kept GREEN. — confirmed: 18 passed.

## Phase 4: RED — `cli/main.py` query rendering (tests/unit/cli/test_query.py)

- [x] 4.1 Update every fake `AnswerResult(...)` construction (happy path ~line 72, no-match fakes at ~118/142/170, and any others) to supply the 4 new required fields with values matching the scenario under test.
- [x] 4.2 Update `test_query_no_match_renders_answer_line_alone` (renamed `test_query_zero_hits_renders_zero_hits_message`) and equivalents to assert cause-specific stdout text instead of the bare `NO_MATCH` string, per `no_match_cause` used in that fixture.
- [x] 4.3 Add RED test: successful run — `CliRunner` `result.stdout` is exactly answer + `Citations:` block (unchanged shape); `result.stderr` contains one `retrieval: {n} FTS hit{s} → LLM invoked → {m} source{s} cited` line.
- [x] 4.4 Add RED test: `no_match_cause="zero_hits"` — stdout shows the zero-hits message, no citation lines, exit 0; stderr shows `retrieval: 0 FTS hits → LLM skipped → 0 sources cited`.
- [x] 4.5 Add RED test: `no_match_cause="all_unreadable"` — stdout shows the "found N, but unreadable, run `openkos lint`" message, exit 0; stderr shows `retrieval: {n} FTS hits → LLM skipped → 0 sources cited`.
- [x] 4.6 Add RED test: `no_match_cause="empty_query"` — stdout prompts for a question, exit 0; stderr shows `retrieval: 0 FTS hits → LLM skipped → 0 sources cited`.
- [x] 4.7 Add RED test: non-empty `skip_notices` on a successful run — stderr contains both the retrieval summary line and the skip-notice block (header + `{cid}.md: skipped ({reason})` lines), worded as a whole-bundle build diagnostic; stdout unaffected.
- [x] 4.8 Add RED test: empty `skip_notices` — stderr contains only the retrieval summary line, no skip-notice text.
- [x] 4.9 Run `uv run pytest tests/unit/cli/test_query.py -q` and confirm the new/updated tests fail (RED) against unmodified `main.py`. — confirmed: 6 failed, 10 passed against unmodified `main.py`.

## Phase 5: GREEN — implement `cli/main.py` query rendering

- [x] 5.1 Add a `_plural(n: int) -> str` helper (returns `""` if `n == 1` else `"s"`), placed near other small CLI helpers.
- [x] 5.2 In `query()`, after computing `result`, render the stderr retrieval summary: `retrieval: {n} FTS hit{s} → LLM {invoked|skipped} → {m} source{s} cited` via `typer.echo(..., err=True)`, using `result.fts_hit_count`, `result.llm_invoked`, `len(result.citations)`.
- [x] 5.3 When `result.skip_notices` is non-empty, render the skip-notice header (`index: {k} doc{s} skipped while building the search index (whole-bundle, not this query's hits):`) plus each notice line to stderr, immediately after the summary line.
- [x] 5.4 Replace the bare no-match `typer.echo(result.answer)` path with a cause-specific stdout message map (`_no_match_message` function, not a dict of Literal->callable, to avoid a new `Callable` import) for `"zero_hits"`, `"all_unreadable"`, `"empty_query"`; success path (`no_match_cause == "none"`) keeps existing `result.answer` + `Citations:` rendering unchanged.
- [x] 5.5 Ensure render order is: stderr summary, stderr skip notices (if any), then stdout answer/cause message — matches design's "stderr first" contract. Verified manually with a `CliRunner` smoke script (see apply-progress notes).
- [x] 5.6 Run `uv run pytest tests/unit/cli/test_query.py -q` and confirm all tests pass (GREEN). — confirmed: 16 passed.

## Phase 6: REFACTOR — `cli/main.py`

- [x] 6.1 Re-read the `query()` command for duplication between the summary/skip-notice/message blocks and existing helpers (e.g. compare with `lint`'s notice-line rendering); extract only if it does not blur the `openkos query:`-prefix error namespace vs. informational `retrieval:`/`index:` labels. — no extraction needed; the `retrieval:`/`index:` labels are intentionally distinct from `openkos query:` error prefix, kept separate per design D-decision.
- [x] 6.2 Update the `query()` docstring to describe the always-on stderr summary, skip-notice surfacing, and the 3 distinct no-match causes (replacing the "single no-match line" description).
- [x] 6.3 Re-run `uv run pytest tests/unit/cli/test_query.py -q` to confirm refactor kept GREEN. — confirmed: 16 passed.

## Phase 7: Full-suite verification and docs

- [x] 7.1 Run `uv run pytest tests/unit/retrieval/test_answer.py tests/unit/cli/test_query.py -q` together; confirm all pass. — confirmed: 34 passed.
- [x] 7.2 Run `uv run pytest -q` (full suite) to catch any other test importing `AnswerResult`/`answer()` that needs the new fields. — confirmed: 446 passed, no other test needed updates.
- [x] 7.3 Run `test_answer_module_does_not_import_config` explicitly and confirm still green (no `config` import introduced by `Literal`/new fields). — confirmed: 1 passed.
- [x] 7.4 Update `docs/cli.md` (~line 84, query section): replace the "single no-match line" description with the always-on stderr `retrieval:` summary, optional skip-notice block, and the 3 cause-specific stdout messages.
- [x] 7.5 Update `docs/user-journey.md` (~lines 134-146, query example): keep the existing stdout example unchanged; add a brief note that a `retrieval:` summary line prints to stderr on every run.

## Status: 100% complete (all phases done)

Full suite: 446 passed. Coverage: 98.86% total (gate 90% reached). Ruff + mypy clean on all 4 changed source/test files.
