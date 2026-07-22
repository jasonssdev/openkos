# Tasks: Reindex Embedding Resilience

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~450-650 (5 prod files ~150-200; tests ~250-350; docs already done) |
| 400-line budget risk | Medium |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | auto-forecast |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Medium

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | bge-m3 default + retry + per-doc isolation + query degrade + tests, single slice | PR 1 | `uv run pytest -q tests/unit/llm/test_ollama.py tests/unit/state/test_reindex.py tests/unit/cli/test_reindex_cmd.py tests/unit/retrieval/test_answer.py tests/unit/test_config.py` | Real `reindex` over a temp bundle with a fake embedder raising transient `OllamaError` on one doc — survivors commit, exit 0 | Revert restores prior batch-embed/atomic-abort; no schema touched; default revert re-triggers the model-tag gate |

## Phase 1: Robust Default Embedder (bge-m3)

- [ ] 1.1 RED: `test_config.py:513` — assertion → `DEFAULT_EMBEDDING_MODEL == "bge-m3"`.
- [ ] 1.2 RED: `test_reindex_cmd.py:152` — assertion → `"bge-m3" in result.stdout`.
- [ ] 1.3 GREEN: `config.py:23` — `DEFAULT_EMBEDDING_MODEL = "bge-m3"` + docstring (ADR-0006).
- [ ] 1.4 GREEN: `cli/main.py:2487` — `reindex` docstring default → `bge-m3`.
- [ ] 1.5 Verify: `test_doctor.py` reads the constant (no hardcode) — still green, no edit.

## Phase 2: Retry-With-Backoff in `OllamaClient.embed` (D1/D3)

- [ ] 2.1 RED: `test_ollama.py` — transient generic `OllamaError` fails attempt 1, succeeds attempt 2; injected `sleep` spy proves no real sleep.
- [ ] 2.2 RED: exhausts default 3 attempts on persistent `OllamaError` → raises; sleep called exactly 2x, exponential.
- [ ] 2.3 RED: `OllamaModelNotFound` never retried — raised immediately, sleep never called.
- [ ] 2.4 RED: `OllamaUnavailable` may retry but raises once exhausted.
- [ ] 2.5 GREEN: `llm/ollama.py:~120` — injectable attempts/backoff/sleep ctor params; retry loop around `embed()`'s HTTP call, catching generic `OllamaError` only, re-raising `OllamaModelNotFound` immediately; update docstring.
- [ ] 2.6 REFACTOR: clean naming/docstrings; no scope creep into `chat()`.

## Phase 3: Per-Doc Embed Isolation In `reindex()` (D2)

- [ ] 3.1 RED: `test_reindex.py` — poison doc (embed raises, retries exhausted) → `embed_failed==1`, `skipped==0`, survivors committed, no raise.
- [ ] 3.2 RED: every doc transiently fails → `embed_failed==N`, no crash.
- [ ] 3.3 RED: `OllamaUnavailable` mid-loop → re-raised, NOT counted as `embed_failed`.
- [ ] 3.4 RED: `OllamaModelNotFound` mid-loop → re-raised, NOT counted as `embed_failed`.
- [ ] 3.5 RED: tag-persist gate withheld when `embed_failed>0` even if `skipped==0`.
- [ ] 3.6 RED: line 695 `embedder.call_count` (`embedded==2`) → `2`; verify lines 149/169 (`==1`, single doc) and 565 (unrelated counter) pass unmodified.
- [ ] 3.7 GREEN: `state/reindex.py:~58` `ReindexReport` — add `embed_failed: int = 0` field + docstring.
- [ ] 3.8 GREEN: `state/reindex.py:~221-231` — single-batch `embedder.embed([...])` → per-doc loop; catch order `except (OllamaUnavailable, OllamaModelNotFound): raise` BEFORE `except OllamaError: embed_failed += 1; continue`; keep `upsert_many` + single commit.
- [ ] 3.9 GREEN: `state/reindex.py:~254` — gate → `model_changed and skipped == 0 and embed_failed == 0`.
- [ ] 3.10 REFACTOR: update `reindex()` docstrings (per-doc loop replaces "ONE batch call").

## Phase 4: Actionable Re-Run Notice (CLI, keys on `embed_failed`)

- [ ] 4.1 RED: `test_reindex_cmd.py` — `embed_failed>0` prints a re-run notice on stderr, distinct from the permanent-skip diagnostic.
- [ ] 4.2 RED: `skipped>0` with `embed_failed==0` does NOT print the notice.
- [ ] 4.3 RED: model-switch run with partial `embed_failed>0` also fires the notice.
- [ ] 4.4 GREEN: `cli/main.py` reindex (~2519-2620) — read `report.embed_failed`, print notice iff `>0`; fatal ladder still exits before the summary.

## Phase 5: Query-Side Dense Degrade On `OllamaError` (D4)

- [ ] 5.1 RED: `test_answer.py` — `_dense_search` question-embed raising `OllamaError` returns `([], True)`, `dense_degraded=True`, no raise.
- [ ] 5.2 RED: regression — `llm.chat`'s `OllamaError` still propagates unswallowed elsewhere.
- [ ] 5.3 GREEN: `retrieval/answer.py:~236` — import `OllamaError`, add to `_dense_search`'s except tuple.

## Phase 6: Integration, Docs Verify, Full Gate

- [ ] 6.1 Integration RED→GREEN: real `reindex()` over a temp bundle, one doc transiently fails, others succeed — exit 0, survivors queryable, `embed_failed` reflected.
- [ ] 6.2 Verify: `docs/adr/0006-default-embedding-model.md` and `docs/tech_stack.md` already reflect bge-m3 — confirm only.
- [ ] 6.3 `uv run pytest -q` full suite green.
- [ ] 6.4 `uv run mypy .` repo-wide, zero new errors.
- [ ] 6.5 `uv run ruff check .` clean.
