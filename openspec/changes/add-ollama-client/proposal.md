# Proposal: `add-ollama-client` — local Ollama chat client (first real LLM call)

## Intent

`openkos query` (MVP-1) must generate a cited answer from a local model, but no
LLM code exists — `grep -ri "ollama" src/` matches only docstrings and help
text. This change lands the project's **first real network/LLM call**: a pure
library module that POSTs messages to a locally running Ollama and returns the
assistant text, with typed errors and a config-driven model/host. `ingest` today
is an explicit "null compiler" (`cli/main.py:153`); this is the client
`add-query-command` needs to answer. Coherent with `tech_stack.md:96` ("the
model is never hard-coded ... behind an `LLMBackend` interface").

## Scope

### In Scope

- New `src/openkos/llm/` — canonical seam, **no CLI command**, no workspace
  change. Its only consumer is future `add-query-command`.
- **`base.py`**: a `typing.Protocol` chat seam (arch `base.py`/`LLMBackend`,
  `architecture.md:46`,`:109`) — sketch `ChatClient.chat(messages) -> str`
  (design fixes the exact name/signature).
- **`ollama.py`**: concrete `OllamaClient` over stdlib `urllib.request` — **no
  new dependency** (only `frontmatter` is a runtime dep; `sqlite3` is stdlib).
- **Endpoint `/api/chat`**, `stream: false`, `think: false` (qwen3 is a thinking
  model — `think:false` yields clean `message.content`, no `<think>` tokens).
  Supports `system` + `user` roles.
- **Config wiring**: model tag from `config.read_config(root).model` (reused, not
  reparsed); base URL default `http://localhost:11434`, overridable via Ollama's
  `OLLAMA_HOST` convention and/or a minimal config key (design finalizes surface).
- **Typed error contract**: `OllamaUnavailable` (connection refused / timeout),
  `OllamaModelNotFound` (404 `model not found`), `OllamaError` (other non-200 /
  malformed) — over a common base so the consumer can catch + degrade.
- Configurable **timeout** with a generous default (inference is slow).

### Non-goals (deferred to MVP-2)

| Deferred | Note |
|---|---|
| Streaming NDJSON | `stream:false` buffered response only |
| Tool/function calling, embeddings | Single chat call |
| Retries / backoff | MVP-2 resilience; single-shot for now |
| `/api/generate`, multi-provider | One endpoint; `openai_compat.py` deferred |
| Any CLI command; changing `ingest` | Pure library; lifecycle untouched |

## Capabilities

### New Capabilities
- `llm-client`: a local Ollama chat client over `/api/chat`
  (`stream:false`/`think:false`), config-driven model/host, returning assistant
  text, behind a `ChatClient` Protocol, with a typed unavailable/not-found/error
  contract.

### Modified Capabilities
- None. `config.read_config` is reused read-only; `ingest`/`forget`/`fts-state`
  are untouched.

## Approach

- **stdlib POST.** Build the `/api/chat` JSON body (`model`, `messages`,
  `stream:false`, `think:false`), POST via `urllib.request` with a timeout,
  parse the buffered JSON, return `message.content`.
- **Map errors at the boundary.** `URLError`/connection-refused/timeout →
  `OllamaUnavailable`; HTTP 404 `model not found` → `OllamaModelNotFound`; other
  non-200 or malformed JSON → `OllamaError`. No raw `HTTPError`/`URLError` leaks.
- **Leaf-module discipline.** `llm/` receives the model tag + host as arguments
  from the CLI layer; it does not import `config` (mirrors `fsio`). Canonical
  direction: `retrieval` will import `llm` later, never the reverse.
  Docstring-per-function house style, strict TDD, 90% branch.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/llm/base.py` | New | `ChatClient` Protocol chat seam |
| `src/openkos/llm/ollama.py` | New | `OllamaClient`, typed exceptions, urllib POST |
| `src/openkos/llm/__init__.py` | New | Package marker |
| `src/openkos/config.py` | Reused | `read_config().model` read-only; possible optional host key (design) |
| `tests/unit/llm/test_ollama.py` | New | Success + error-mapping coverage, HTTP layer mocked |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Model-inference latency stalls callers | High | Configurable timeout, generous default; `OllamaUnavailable` on expiry |
| qwen3 emits `<think>` tokens polluting output | Med | `think:false` by default (verified clean this session) |
| Ollama down / model not pulled crashes consumer | Med | Typed exceptions let `add-query-command` degrade, never a raw traceback |
| Testable without a live Ollama? | Med | **Design injects/mocks the HTTP layer** (seam over `urllib.urlopen`) so unit tests need no running Ollama — call out at design time |
| Review size | Low | One client module + tests, no CLI/persistence — likely a single PR; forecast at `sdd-tasks` |

## Rollback Plan

Purely additive: `git revert` removes `src/openkos/llm/` and its tests. No
persisted state, no migration, no CLI surface, no config schema change (host key,
if added, stays optional with a default) — the module is dormant until
`add-query-command` calls it.

## Dependencies / Sequencing

- **Upstream: none new.** `config` already stores/validates the model tag.
  Functionally independent of `add-fts-state`'s internals — could build in
  parallel; ships second in the locked chain by choice.
- **Unblocks**: `add-query-command`, which hard-depends on this client's `chat()`
  surface and its typed error contract.

## Success Criteria

- [ ] `OllamaClient.chat([system, user])` POSTs `/api/chat` with
      `stream:false`/`think:false` and returns clean `message.content` text.
- [ ] Model tag comes from `config.read_config().model`; base URL defaults to
      `http://localhost:11434` and is overridable.
- [ ] Connection-refused/timeout → `OllamaUnavailable`; 404 model-not-found →
      `OllamaModelNotFound`; other non-200/malformed → `OllamaError`.
- [ ] No new third-party dependency; transport is stdlib `urllib.request`.
- [ ] No CLI command; `ingest`/`forget`/`fts-state` unchanged.
- [ ] Unit tests pass with the HTTP layer mocked (no live Ollama required);
      `uv run pytest` green at 90%+ branch; ruff/mypy clean.
