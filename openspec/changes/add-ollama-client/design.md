# Design: `add-ollama-client` — local Ollama chat client (first real LLM call)

## Technical Approach

A pure library package `src/openkos/llm/` (`__init__.py` marker + `base.py` +
`ollama.py`), **no CLI**. `base.py` defines the `LLMBackend` `typing.Protocol`
(the canonical seam named in `architecture.md:46`,`:109` and `tech_stack.md:96`)
plus a `Message` `TypedDict`. `ollama.py` defines `OllamaClient`, a concrete
`LLMBackend` over stdlib `urllib.request` — **no new dependency**. `chat()`
builds the `/api/chat` JSON body (`model`, `messages`, `stream:false`,
`think:false`), POSTs it with a generous timeout, parses the buffered JSON, and
returns `message.content`. Every boundary failure maps to one of three typed
exceptions over a common base, so no raw `HTTPError`/`URLError` ever leaks.
Leaf-module discipline (mirrors `fsio`): `llm/` never imports `config` — the CLI
layer resolves `config.read_config(root).model` and passes the tag in as an
argument, exactly as `ingest`/`forget`/`lint` already pass config fields down.

## Architecture Decisions

| # | Decision | Alternatives rejected | Rationale |
|---|---|---|---|
| **D1** | **Seam is `LLMBackend`** (Protocol), method `chat(self, messages: Sequence[Message]) -> str`. `Message` is a `TypedDict {"role": str, "content": str}`. | Proposal's sketch name `ChatClient`; a `@dataclass Message`. | `LLMBackend` is the canonical name in `architecture.md`/`tech_stack.md` — consistency beats the proposal's placeholder. A `TypedDict` drops **straight** into the JSON body's `messages` array (no `asdict` conversion), and is the ergonomic literal shape the future `query` command already builds. |
| **D2** | **`OllamaClient(model, *, host=None, timeout=120.0, urlopen=urllib.request.urlopen)`.** `model` required. Host precedence: explicit arg > `OLLAMA_HOST` env > packaged `http://localhost:11434`; a bare `host:port` is normalized by prepending `http://`. **No `openkos.yaml` host key added.** | Add an optional `host:` config key; make `model` default internally. | Minimal config surface: reusing Ollama's own `OLLAMA_HOST` convention needs zero schema change, keeping rollback purely additive (proposal). `model` stays required because the CLI already owns `read_config().model`; the leaf must not reach back into `config`. |
| **D3** | **Testability seam = injected `urlopen` callable** (constructor arg, module default `urllib.request.urlopen`). | Module-level `urlopen` tests monkeypatch (fts precedent); a `_post` method tests override. | The stdlib boundary the proposal names is exactly `urlopen`; injecting it lets a test stub **return** a fake response (`.read()` → bytes) or **raise** a real `HTTPError`/`URLError`, covering success and every error branch with **no live Ollama**. Explicit injection matches `fsio`/`config` arg-passing over fts's monkeypatch, while the default keeps production call sites clean. |
| **D4** | **Exception hierarchy: `OllamaError(Exception)` base; `OllamaUnavailable` and `OllamaModelNotFound` subclass it.** `OllamaError` is also thrown directly for the generic case. | Three flat siblings; a common base that is never itself raised. | A single base lets `add-query-command` `except OllamaError` to degrade broadly, or catch the two specifics to tailor a message — the proposal's stated need. |
| **D5** | **Error mapping, in catch order:** `HTTPError` → read `.code` + body; `404` with body error containing `"not found"` → `OllamaModelNotFound`, else `OllamaError`. Then `(URLError, TimeoutError)` → `OllamaUnavailable`. On success: `json.loads` failure or missing/non-str `message.content` → `OllamaError`. | Catching `URLError` before `HTTPError`; treating `done:false` specially. | `HTTPError` **is** a `URLError` subclass, so it must be caught first or a 404 would be misread as "unavailable". `socket.timeout` is an alias of `TimeoutError`; connection-refused surfaces as `URLError`. With `stream:false` the response is always a single `done:true` object, so no streaming/`done:false` handling is needed. |
| **D6** | **Request body minimal:** `{"model", "messages", "stream": false, "think": false}`. **No `options`** (temperature/num_predict) for MVP-1. Timeout default **120.0s**, configurable. | Passing an `options` passthrough now; a short timeout. | `think:false` yields clean `message.content` on the qwen3 thinking model (verified this session). Inference is slow, so a generous 120s default avoids premature `OllamaUnavailable`; `options` is deferred to MVP-2 to keep the surface tight. |

**ADR gate — zero created.** Every decision is additive and `git revert`-able
(new dormant package, no persisted state, no migration, no CLI, no config schema
change). Matches the `add-fts-state` precedent. **Zero ADRs.**

## Data Flow

```
CLI layer:  cfg = config.read_config(root)          # llm never imports config
            client = OllamaClient(cfg.model)         # host from OLLAMA_HOST / default

client.chat([{"role":"system",...},{"role":"user",...}])
  ├─ url  = f"{host.rstrip('/')}/api/chat"
  ├─ body = json.dumps({"model","messages","stream":False,"think":False}).encode()
  ├─ req  = Request(url, data=body, headers={"Content-Type":"application/json"}, method="POST")
  ├─ resp = urlopen(req, timeout=timeout)            # HTTPError → 404? not-found : OllamaError
  │                                                  # URLError/TimeoutError → OllamaUnavailable
  ├─ data = json.loads(resp.read())                  # decode/JSON fail → OllamaError
  └─ return data["message"]["content"]               # missing/non-str → OllamaError
```

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/llm/__init__.py` | New | Package marker |
| `src/openkos/llm/base.py` | New | `LLMBackend` Protocol, `Message` TypedDict |
| `src/openkos/llm/ollama.py` | New | `OllamaClient`, `OllamaError`/`OllamaUnavailable`/`OllamaModelNotFound`, urllib POST + error mapping |
| `src/openkos/config.py` | Reused | `read_config().model` read-only at the CLI layer — **no change** |
| `tests/unit/llm/test_ollama.py` | New | Success + full error-mapping coverage, `urlopen` stubbed |

## Interfaces

```python
# llm/base.py — leaf: stdlib typing only
class Message(TypedDict):
    role: str        # "system" | "user" | "assistant"
    content: str

class LLMBackend(Protocol):
    def chat(self, messages: Sequence[Message]) -> str: ...

# llm/ollama.py
class OllamaError(Exception): ...              # base + generic non-200/malformed
class OllamaUnavailable(OllamaError): ...      # connection refused / timeout
class OllamaModelNotFound(OllamaError): ...    # 404 "model not found"

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_TIMEOUT = 120.0

class OllamaClient:                            # implements LLMBackend
    def __init__(self, model: str, *, host: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT,
                 urlopen: Callable[..., Any] = urllib.request.urlopen) -> None: ...
    def chat(self, messages: Sequence[Message]) -> str: ...
```

## Testing Strategy (strict TDD, ≥90% branch, no network)

| Layer | What | How |
|---|---|---|
| Unit | `chat` success → returns `message.content`; body has `stream:false`/`think:false` | stub `urlopen` returns fake response with `.read()` |
| Unit | `URLError` + `TimeoutError`/`socket.timeout` → `OllamaUnavailable` | stub `urlopen` raises |
| Unit | 404 `"model not found"` → `OllamaModelNotFound`; other `HTTPError`/non-404 → `OllamaError` | stub raises `HTTPError` with `.code`/body |
| Unit | malformed JSON, missing/non-str `message.content` → `OllamaError` | stub returns bad bytes |
| Unit | host precedence: arg > `OLLAMA_HOST` env > default; bare `host:port` normalized | `monkeypatch.setenv` |
| Unit | subclass contract: both specifics are `OllamaError`; no CLI imports `llm` | `issubclass` + `ast` scan (fts precedent) |

## Threat Matrix

**N/A** — no shell, no subprocess, no routing, no VCS/PR automation, no
executable-file classification. Transport is stdlib `urllib` to a
loopback/user-configured host. Injection-adjacent surfaces are contained: the
body is built with `json.dumps` (never string concatenation); the `model` tag is
already allowlist-validated by `config.validate_model`; `host` is trusted
user/env config (`OLLAMA_HOST`), normalized to a scheme, never derived from
document content.

## Migration / Rollout

No migration. Purely additive; the package is dormant until `add-query-command`
constructs an `OllamaClient`. `git revert` removes `src/openkos/llm/` and its
tests. No config schema change.

## Open Questions

- [ ] None blocking. Seam/name (D1), construction/host (D2), test seam (D3),
      hierarchy (D4), error mapping (D5), body/timeout (D6) all resolved.
      `options` passthrough, retries, and `/api/generate` remain deferred to
      MVP-2 per the proposal non-goals.
