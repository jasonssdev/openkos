# Tasks: Hybrid Retrieval — Embedder Seam + Ollama Embeddings (MVP-2, Slice 1)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~250-320 (prod ~90-120, tests ~140-180, docs ~15-20) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | auto-chain |
| Chain strategy | pending (not needed — single PR) |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

Four additive edits (`llm/base.py`, `llm/ollama.py`, `config.py`, `cli/main.py`) plus tests and two doc updates, no refactor, no persistence/vector-store work. Comparable in shape to `add-ollama-client` and `add-doctor-command` slices, both well under budget as single PRs.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `Embedder` Protocol + `OllamaClient.embed()` + config default + doctor check, fully tested | PR 1 (single PR) | `uv run pytest tests/unit/llm/test_ollama.py tests/unit/test_config.py tests/unit/cli/test_doctor.py` | `uv run openkos doctor` against a real/mocked local Ollama with `qwen3-embedding:0.6b` pulled | Revert PR 1; `embed()`/`Embedder`/`embedding_model` are unused by any consumer this slice, zero retrieval-path change |

## Phase 1: Embedder Protocol (`src/openkos/llm/base.py`)

- [x] 1.1 RED: `tests/unit/llm/test_ollama.py` (or new `tests/unit/llm/test_base.py`) — a minimal fake class exposing `embed(texts) -> list[list[float]]` satisfies `Embedder` structurally (duck-typing, no inheritance), mirroring the existing fake-`LLMBackend` pattern.
- [x] 1.2 GREEN: `src/openkos/llm/base.py` — add `EMBED_DIM = 1024` module constant and `class Embedder(Protocol): def embed(self, texts: Sequence[str]) -> list[list[float]]: ...  # pragma: no cover`, beside `LLMBackend`; keep module a config-free leaf (stdlib `typing` only).

## Phase 2: `OllamaClient.embed()` (`src/openkos/llm/ollama.py`)

- [x] 2.1 RED: `test_ollama.py` — `embed([])` returns `[]` with zero calls to `urlopen` (no network call, no request built).
- [x] 2.2 RED: `test_ollama.py` — `embed(["a", "b"])` POSTs `{"model": self._model, "input": ["a", "b"]}` to `{host}/api/embed` and returns one 1024-float list per input, order preserved (happy path, `embeddings` key).
- [x] 2.3 RED: `test_ollama.py` — response using the singular `embedding` key (one row, no batch) is parsed and wrapped into a one-item list (Ollama version-drift fallback).
- [x] 2.4 RED: `test_ollama.py` — a row with length != 1024 raises `OllamaError` (wrong-dim validation).
- [x] 2.5 RED: `test_ollama.py` — a row containing non-numeric values raises `OllamaError`.
- [x] 2.6 RED: `test_ollama.py` — malformed JSON body, and a body with neither `embeddings` nor `embedding` key (response-shape drift), each raise `OllamaError`.
- [x] 2.7 RED: `test_ollama.py` — connection-refused/timeout during connect, and a read-phase failure (timeout/reset/`IncompleteRead`) after connect, each raise `OllamaUnavailable`, no raw transport exception escapes (mirrors `chat()`'s ladder).
- [x] 2.8 RED: `test_ollama.py` — non-404 HTTP error raises `OllamaError`; 404 with a not-found body raises `OllamaModelNotFound` (reuses `_map_http_error`).
- [x] 2.9 GREEN: `src/openkos/llm/ollama.py` — implement `embed(self, texts: Sequence[str]) -> list[list[float]]`: import `EMBED_DIM` from `llm.base`; `if not texts: return []` short-circuit; reuse `chat()`'s connect/read try-except ladder and `_map_http_error`/`_unavailable`; defensive parse (`embeddings` first, fall back to singular `embedding` wrapped as one row); validate each row is exactly `EMBED_DIM` numeric floats, else `OllamaError`.

## Phase 3: `embedding_model` Config Default (`src/openkos/config.py`)

- [x] 3.1 RED: `tests/unit/test_config.py` — `read_config` returns `embedding_model` from `openkos.yaml` when present.
- [x] 3.2 RED: `test_config.py` — `read_config` falls back to `DEFAULT_EMBEDDING_MODEL` when `embedding_model` is absent or explicit YAML `null`, mirroring the existing `is not None` fallback tests for `model`.
- [x] 3.3 GREEN: `src/openkos/config.py` — add `DEFAULT_EMBEDDING_MODEL = "qwen3-embedding:0.6b"`; add `embedding_model: str` field to `Config`; resolve it in `read_config` via `raw.get("embedding_model")` checked `is not None`, else `DEFAULT_EMBEDDING_MODEL`. Do NOT modify `openkos.yaml.template` (default-only decision).

## Phase 4: Doctor Embedding-Model Check (`src/openkos/cli/main.py`)

- [x] 4.1 RED: `tests/unit/cli/test_doctor.py` — embedding-model check prints `[PASS]` when the resolved `embedding_model` tag matches an installed tag (via `model_tag_matches`), and does not affect exit code either way.
- [x] 4.2 RED: `test_doctor.py` — embedding-model check prints `[FAIL]` with `ollama pull <embedding_model>` remediation when the tag is not installed, and `doctor`'s exit code stays `0` (informational, not critical) even though this check fails.
- [x] 4.3 RED: `test_doctor.py` — embedding-model check prints `[SKIP]` (not `[FAIL]`) when Ollama is unreachable, reusing the existing `reachable` flag (no double-reporting the same root cause as check 3).
- [x] 4.4 RED: `test_doctor.py` — outside an initialized workspace, the check still runs against `DEFAULT_EMBEDDING_MODEL` and stays informational.
- [x] 4.5 GREEN: `src/openkos/cli/main.py` — add a 6th `CheckResult` (embedding-model-installed, `critical=False`) after the existing model-installed check, reusing the already-fetched `installed` list and `reachable` flag; tag from `cfg.embedding_model` when in a workspace else `config.DEFAULT_EMBEDDING_MODEL`; renumber the bundle-readable check's docstring/comment from `(5)` to `(6)`.

## Phase 5: Docs + Regression Sweep

- [x] 5.1 DOCS: `docs/cli.md` — update the `openkos doctor` section: "five checks" -> "six checks"; add the embedding-model-installed check to the list (informational, `[SKIP]` when unreachable) and confirm it is excluded from the exit-code sentence.
- [x] 5.2 DOCS: `docs/roadmap.md` — mark the Slice 1 embedding-seam line as delivered (Embedder interface + Ollama embeddings, default `qwen3-embedding:0.6b`), consistent with existing roadmap conventions.
- [x] 5.3 VERIFY: `tests/unit/llm/test_ollama.py::test_llm_modules_do_not_import_config` runs unmodified and stays green (leaf-discipline regression, spec requirement 5).
- [x] 5.4 VERIFY: `uv run pytest tests/unit/llm tests/unit/test_config.py tests/unit/cli/test_doctor.py` green; `uv run ruff check . && uv run ruff format --check .` and `uv run mypy .` clean on touched files.
- [x] 5.5 VERIFY: `uv run pytest` full suite green; `uv run pytest --cov` >= 90% branch, with explicit attention to the malformed-response, wrong-dim, response-shape-drift, empty-input, and daemon-unreachable branches in `embed()`.
