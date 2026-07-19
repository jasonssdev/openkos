# Tasks: `add-query-command` — the `openkos query` CLI command (MVP-1 query chain #4)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~285 (`cli/main.py` ~45 [3 imports + ~40 command]; `test_query.py` ~160 new; `answer.py` ~10 docstring; `test_answer.py` ~50 new test; `docs/cli.md` ~20) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR — one command wired through one already-archived seam, plus two small doc/test follow-ups |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `query` command (`cli/main.py`) + `test_query.py` + `answer.py` docstring + `test_answer.py` multi-survivor test + `docs/cli.md` | PR 1 (single PR, under budget) | `uv run pytest tests/unit/cli/test_query.py tests/unit/retrieval/test_answer.py` | N/A — CLI tests patch `openkos.cli.main.answer`, no live Ollama/FTS; manual smoke against a real Ollama deferred to user acceptance | `git revert`; additive command + docstring + tests + docs, no schema/config change, nothing else depends on `query` yet |

## Phase 1: Foundation
- [x] 1.1 `src/openkos/cli/main.py` — add imports only: `from openkos.retrieval.answer import answer`, `from openkos.llm.ollama import OllamaClient, OllamaError`, `from openkos.state.fts import FtsUnavailable`.
- [x] 1.2 `tests/unit/cli/test_query.py` — new file: module docstring, `CliRunner`, reuse the `_init_workspace(tmp_path, monkeypatch)` pattern from `tests/unit/cli/test_lint.py`.

## Phase 2: RED — workspace gate
- [x] 2.1 Test: `query "q"` outside a workspace → exit 1, stderr `openkos query: refusing to run -- ...`, `openkos.cli.main.answer` (patched) never called (Scenario: Run outside a workspace).

## Phase 3: GREEN — command skeleton + D1 gate
- [x] 3.1 Implement `query(question, --limit=5)` signature + bare D1 workspace gate (`require_workspace` → refuse/exit 1) in `cli/main.py`.

## Phase 4: RED — happy path, no-match, limit forwarding
- [x] 4.1 Test: fake `answer` → `AnswerResult("reply", [c1, c2])`; stdout = reply, blank line, `Citations:`, `  → id (title)` per citation in hit-rank order (Scenarios: Matching answer with citations; Citation order matches the answer; Run inside a workspace).
- [x] 4.2 Test: fake `answer` → `AnswerResult(NO_MATCH, [])`; stdout == NO_MATCH line only, no `Citations:` section, exit 0 (Scenario: Zero matching concepts).
- [x] 4.3 Test: recording fake — `--limit 3` → `limit=3`; omitted → `limit=5` (Scenarios: Caller overrides the default limit; Caller omits `--limit`).

## Phase 5: GREEN — config, client, answer(), rendering
- [x] 5.1 Implement D2 Phase-A `read_config` guard (`except (OSError, ValueError)` → exit 1), `OllamaClient(model=cfg.model)`, the `answer()` call, and D3 answer/citations rendering per design's literal output.

## Phase 6: RED — error boundaries
- [x] 6.1 Test: fake `answer` raises `OllamaUnavailable` → exit 1, stderr `openkos query: failed -- ...`, no traceback (Scenario: Ollama backend unreachable).
- [x] 6.2 Test: fake `answer` raises `FtsUnavailable` → exit 1, same stderr shape, no traceback (Scenario: FTS index unavailable).

## Phase 7: GREEN — error boundary catch
- [x] 7.1 Implement D2 Phase-B `except (FtsUnavailable, OllamaError)` around the `answer()` call.

## Phase 8: Follow-ups (deferred from `add-query-answer`)
- [x] 8.1 `src/openkos/retrieval/answer.py` — add `_SYSTEM_PROMPT` docstring (D5 grounding-rules text); no signature change.
- [x] 8.2 RED/GREEN: add `test_multiple_surviving_hits_cite_in_rank_order_and_join_context` to `tests/unit/retrieval/test_answer.py` — two readable concepts, asserts citation ORDER and `\n\n`-joined user-context blocks (design's "Multi-survivor test follow-up"); already-passing since production code is unchanged, confirms existing behavior.

## Phase 9: Docs
- [x] 9.1 `docs/cli.md` — expand the `openkos query` stub (lines 74-76): read-only, workspace gate, requires local Ollama serving the configured model, `--limit <n>` flag table, output shape (answer + `→`-bulleted `Citations:`), no-match/error behavior.

## Phase 10: Verification Gate
- [x] 10.1 `uv run pytest --cov` — full suite green; ≥90% branch on changed lines.
- [x] 10.2 `uv run ruff check .` && `uv run ruff format --check .` — clean.
- [x] 10.3 `uv run mypy .` — clean (strict).

## Unplanned Fix (discovered during apply)
- [x] 10.4 `tests/unit/state/test_fts.py::test_cli_module_does_not_import_state_fts` — this pre-existing `add-fts-state` architectural test asserted `cli/main.py` never imports anything with "state" in the name. It was written when no CLI command used `state.fts` yet; its own spec text (`fts-state/spec.md`, "No CLI Surface, No Lifecycle Change") explicitly scopes the guarantee to `ingest`/`forget` and says the module stays dormant only "until a future command calls it" — `query` is that future command (D2 explicitly imports `FtsUnavailable`). Rewrote the test to `test_ingest_and_forget_do_not_reference_state_fts`, which asserts the two named commands' source never mentions `fts`/`state` — the actual invariant the requirement protects — instead of banning the import repo-wide.
