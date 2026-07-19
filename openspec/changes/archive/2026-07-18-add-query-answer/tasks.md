# Tasks: `add-query-answer` — cited answer library (MVP-1 query chain #3, no CLI)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~370 (`retrieval/answer.py` ~95, `retrieval/__init__.py` ~5; `test_answer.py` ~260, test `__init__.py` ~5) |
| 400-line budget risk | Medium |
| Chained PRs recommended | No |
| Suggested split | Single PR — retrieve→assemble→answer is one cohesive seam over 3 already-archived modules; no natural split point |
| Delivery strategy | ask-on-risk |
| Chain strategy | size-exception |

Decision needed before apply: Yes
Chained PRs recommended: No
Chain strategy: size-exception
400-line budget risk: Medium

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `retrieval/answer.py` (`answer()`, `Citation`, `AnswerResult`, prompt consts) + tests, fake `LLMBackend`, `tmp_path` bundles | PR 1 (`size:exception` if diff > 400) | `uv run pytest tests/unit/retrieval/test_answer.py` | N/A — dormant library, no CLI consumer until `add-query-command`; manual smoke deferred to that change | `git revert`; `src/openkos/retrieval/` + its tests are additive-only, imported by nothing yet |

## Phase 1: Foundation
- [x] 1.1 `src/openkos/retrieval/__init__.py` — package marker, module docstring.
- [x] 1.2 `src/openkos/retrieval/answer.py` — `Citation`/`AnswerResult` frozen dataclasses, `NO_MATCH` + system-prompt consts, `answer()` signature stub.
- [x] 1.3 `tests/unit/retrieval/__init__.py` — test package marker.

## Phase 2: RED — happy path, default limit, prompt shape
- [x] 2.1 Test: matching concepts → `FtsIndex.search` called with resolved limit, `llm.chat` called once, `AnswerResult.answer` == LLM text (Scenario: Matching concepts produce a cited answer).
- [x] 2.2 Test: caller omits `limit` → `search` called with `limit=5` (Scenario: Caller omits limit).
- [x] 2.3 Test: prompt shape — system grounding text present, one labeled `[concept_id — title]` block per hit + `QUESTION:` (D5).

## Phase 3: GREEN — retrieve, assemble, answer core
- [x] 3.1 Implement D1: `with fts.build_index(bundle_dir) as index: hits = index.search(question, limit)`.
- [x] 3.2 Implement D2 guarded per-hit re-read: read + `okf.load_frontmatter`, skip on `OSError`/`UnicodeDecodeError`/parse error, append to context+citations.
- [x] 3.3 Implement D5 message assembly + `llm.chat(messages)` call, return `AnswerResult(reply, citations)`.

## Phase 4: RED — zero/degraded no-match
- [x] 4.1 Test: zero FTS hits → `NO_MATCH`, `citations==[]`, `llm.chat` never called (Scenario: No matching concepts found).
- [x] 4.2 Test: all hits unreadable → same zero-hit contract, `chat` never called (Scenario: All hits unreadable).
- [x] 4.3 Test: partial unreadable — one vanished + one valid → bad skipped, good cited, `chat` called with only readable context (Scenario: One hit vanished after indexing).

## Phase 5: GREEN — zero-context short-circuit
- [x] 5.1 Implement D3: `if not context: return AnswerResult(NO_MATCH, [])` before building messages.

## Phase 6: RED — citation integrity + title fallback
- [x] 6.1 Test: `citations` contains exactly one `Citation` per in-context concept, title from frontmatter (Scenario: Citation set matches context set exactly).
- [x] 6.2 Test: concept with missing/empty frontmatter `title` → citation title falls back to `concept_id`.

## Phase 7: GREEN — title resolution
- [x] 7.1 Implement D4: `title = str(meta.get("title") or "") or hit.concept_id`.

## Phase 8: RED — typed exception propagation
- [x] 8.1 Test: `FtsUnavailable` raised during index build propagates unswallowed (Scenario: FTS index unavailable).
- [x] 8.2 Test: `llm.chat` raising an `OllamaError`-family exception propagates unswallowed (Scenario: LLM backend fails).

## Phase 9: Layering guard
- [x] 9.1 RED — `ast`-scan test (mirrors `llm/` precedent) asserts `retrieval/answer.py` imports no `openkos.config`.
- [x] 9.2 GREEN — confirm guard passes with no production change (leaf discipline already satisfied by D1's `bundle_dir`/`llm`-arg injection).

## Phase 10: Verification Gate
- [x] 10.1 `uv run pytest --cov` — full suite green; `retrieval/` 100% line+branch (floor `fail_under=90`).
- [x] 10.2 `uv run ruff check .` && `uv run ruff format --check .` — clean.
- [x] 10.3 `uv run mypy .` — clean (strict).
- [x] 10.4 `git diff --stat -- src/openkos/config.py` — empty, confirming zero blast-radius on `config`.
