# Tasks: `add-ollama-client` — local Ollama chat client

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~420 (`llm/base.py` ~25, `llm/ollama.py` ~110, `llm/__init__.py` ~5; `test_ollama.py` ~270, test `__init__.py` ~5) |
| 400-line budget risk | Medium |
| Chained PRs recommended | No |
| Suggested split | Single PR — client + errors + tests are one cohesive seam; no natural split point |
| Delivery strategy | ask-on-risk |
| Chain strategy | size-exception |

Decision needed before apply: Yes
Chained PRs recommended: No
Chain strategy: size-exception
400-line budget risk: Medium

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `llm/base.py` + `llm/ollama.py` (client, errors, host precedence) + tests, mocked `urlopen` | PR 1 (`size:exception` if diff > 400) | `uv run pytest tests/unit/llm/test_ollama.py` | `OllamaClient("qwen3").chat([{"role":"user","content":"hi"}])` against a real local `ollama serve` (manual smoke, not CI) | `git revert`; `src/openkos/llm/` + its tests are additive-only, dormant until `add-query-command` imports them |

## Phase 1: Foundation
- [x] 1.1 `src/openkos/llm/__init__.py` — package marker, module docstring.
- [x] 1.2 `src/openkos/llm/base.py` — `Message` TypedDict (`role`, `content`), `LLMBackend` Protocol (`chat`), docstring-per-member (D1).
- [x] 1.3 `tests/unit/llm/__init__.py` — test package marker.

## Phase 2: RED — success + request shape
- [x] 2.1 `chat()` success: 200 body `{"message":{"content":...}}` → returns the content string.
- [x] 2.2 Request body contains `stream:false` and `think:false`.
- [x] 2.3 System + user messages sent as `{"role","content"}` entries, original order preserved.
- [x] 2.4 Model tag + full `messages` array present in the POSTed JSON body; URL targets `{host}/api/chat`.

## Phase 3: GREEN — client happy path
- [x] 3.1 `llm/ollama.py`: `OllamaClient.__init__(model, *, host=None, timeout=120.0, urlopen=urllib.request.urlopen)`; `DEFAULT_HOST`/`DEFAULT_TIMEOUT` (D2, D6).
- [x] 3.2 `chat()`: build JSON body, POST via injected `urlopen`, `json.loads` response, return `message.content` (D5, D6).

## Phase 4: RED — error mapping
- [x] 4.1 `URLError` (connection refused) → `OllamaUnavailable`; no raw `URLError` escapes.
- [x] 4.2 Timeout (`TimeoutError`) → `OllamaUnavailable`; no hang.
- [x] 4.3 `HTTPError` 404 body `"model '<tag>' not found"` → `OllamaModelNotFound`.
- [x] 4.4 `HTTPError` non-404 → `OllamaError` carrying server detail.
- [x] 4.5 200 body not valid JSON → `OllamaError`, no unhandled parse exception.
- [x] 4.6 200 body missing/non-str `message.content` → `OllamaError`.

## Phase 5: GREEN — error mapping
- [x] 5.1 `llm/ollama.py`: `OllamaError(Exception)` base; `OllamaUnavailable`/`OllamaModelNotFound` subclass it (D4).
- [x] 5.2 Catch `HTTPError` before `URLError` (subclass order); 404 "not found" → `OllamaModelNotFound`, else `OllamaError`; then `(URLError, TimeoutError)` → `OllamaUnavailable`; JSON/key errors → `OllamaError` (D5).

## Phase 6: RED — host configuration
- [x] 6.1 No override → request targets `http://localhost:11434/api/chat`.
- [x] 6.2 Explicit `host` arg overrides `OLLAMA_HOST` env and default.
- [x] 6.3 `OLLAMA_HOST` env overrides default when no arg given (`monkeypatch.setenv`).

## Phase 7: GREEN — host precedence
- [x] 7.1 `llm/ollama.py`: resolve host as arg > `OLLAMA_HOST` env > `DEFAULT_HOST`; normalize a bare `host:port` by prepending `http://` (D2).

## Phase 8: Layering guard
- [x] 8.1 RED — `ast`-scan (mirrors `test_cli_module_does_not_import_state_fts`) asserts no module under `src/openkos/llm/` imports `openkos.config`.
- [x] 8.2 GREEN — confirm guard passes with no production change (leaf discipline already satisfied by D2's arg-passed `model`).

## Phase 9: Verification Gate
- [x] 9.1 `uv run pytest --cov` — full suite green; new `llm/` modules 100% line+branch (floor `fail_under=90`).
- [x] 9.2 `uv run ruff check .` && `uv run ruff format --check .` — clean.
- [x] 9.3 `uv run mypy .` — clean (strict).
- [x] 9.4 `git diff --stat -- src/openkos/config.py` — empty, confirming zero blast-radius on `config`.
