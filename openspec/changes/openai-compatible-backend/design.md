# Design: openai-compatible-backend — a second, local-first LLM backend behind the existing seams

Refs #1057. Inputs: `proposal.md`, Engram `sdd/openai-compatible-backend/explore`,
the code as of `032ba61`. ADR: `docs/adr/0031-openai-compatible-backend.md`
(Proposed).

## Technical Approach

Add a second concrete client, `llm/openai_compatible.py::OpenAICompatibleClient`,
that mirrors `OllamaClient`'s transport shape (stdlib `urllib.request`, injected
`urlopen`, the same connect/read/parse error ladder) and satisfies the existing
structural Protocols (`LLMBackend`, `Embedder`, `BackendDiagnostics`). Select it
through one application-layer resolver (`application/backends.py`) that owns
endpoint precedence, the environment-only API key, and backend dispatch, and
that every chat, embed and diagnostics construction goes through. The concrete
classes stay injected by the adapters (`cli/main.py`, `mcp/server.py`), so the
application layer still binds no concrete backend (ADR-0018).

Three corrections to the proposal's premises came out of reading the code, and
the design absorbs them rather than working around them:

1. **Most consumers catch the concrete `OllamaError`, not `BackendError`.**
   `resolution/{adjudication,contradiction,edge_typing,volatility_typing,decision_revision,decision_subject}.py`,
   `state/reindex.py`, `retrieval/answer.py`, `mcp/server.py`, `cli/main.py`
   and `cli/curate.py` import `OllamaError` / `OllamaUnavailable` /
   `OllamaModelNotFound` / `OllamaEmbeddingDimensionMismatch` from
   `llm/ollama.py` and catch them by name (about 55 `except`/`isinstance`
   sites). Only `extraction/concept.py` and `application/doctor.py` catch the
   neutral bases. An `OpenAICompatibleError(BackendError)` would escape every
   one of those handlers. The design adds three neutral mid-level bases to
   `llm/base.py` and migrates every catch site to the neutral names in its own
   slice, before any new error can be raised in production (Decision 3).
2. **The embedding identity has a second consumer.** The question-vector cache
   (`application/query.py` → `state/question_vectors.QuestionVectorStore`) is
   keyed by the bare `cfg.embedding_model`. A backend switch with the same
   model name would reuse cached question vectors from the other backend.
   The backend kind is folded into that key too (Decision 7).
3. **`llm/base.py`'s module docstring states that `classify_backend_host`
   "stayed in `ollama.py`"** because it parses Ollama's wire shapes. The move
   reverses that sentence; the docstring is rewritten in the same slice so the
   code does not contradict its own rationale.

Default-path invariant, pinned by tests in every slice: with no new key set and
`backend` absent, the kwargs passed to the Ollama factory, the request bodies,
the embedding tag, the question-vector key, every remediation string and every
golden are byte-identical to `032ba61`.

## Architecture Decisions

### Decision 1: Mirror `OllamaClient`; share only pure helpers through `llm/base.py`

**Choice**: `OpenAICompatibleClient` is a sibling class with its own transport
methods, error ladder and error hierarchy. Pure, backend-agnostic helpers move
to `llm/base.py` and are re-exported from `ollama.py`: `classify_backend_host`
and its private helpers (`_LOCAL_HOST_LITERALS`, `_UNPARSEABLE_DISPLAY`,
`_HEX_DIGITS`, `_plausible_bracketless_ipv6`, `_is_clean_hostport`,
`_is_loopback_ipv4_literal`), `_measured_counters` (renamed public
`measured_counters`, with `ollama._measured_counters` kept as an alias), and
`is_timeout_failure` (widened to `BackendUnavailable`, see Decision 3).
**Alternatives considered**: a shared `_HttpJsonClient` base class holding the
transport; importing the helpers from `llm/ollama.py` inside the new module.
**Rationale**: The Protocols are the contract (proposal "Mirror, do not
generalize"). A base class would couple two clients whose request shapes,
error mapping and retry semantics differ in detail, and would put `OllamaClient`
bytes at risk for no behavior gain. Importing a sibling concrete module would
make the new backend depend on Ollama's module, which is the coupling #995
removed for the types. The #995 PR 6 move-and-re-export precedent keeps every
existing `from openkos.llm.ollama import classify_backend_host` working.

### Decision 2: Client request/response mapping

**Choice**: The mapping below. Everything not listed is not sent.

| Concern | Ollama (unchanged) | OpenAI-compatible |
| --- | --- | --- |
| Chat endpoint | `POST {host}/api/chat` | `POST {root}/v1/chat/completions` |
| Streaming | `"stream": false` | `"stream": false` |
| Reasoning | `"think": false` | not sent (no portable field; documented limitation) |
| Generation ceiling | `options.num_predict` when set | top-level `max_tokens` when set |
| Temperature / seed | `options.temperature` / `options.seed`, `None` omits | top-level `temperature` / `seed`, `None` omits, `0.0` is a value |
| Context window | `options.num_ctx` when set | never sent; stored for OpenKOS's own prompt budgeting (advisory) |
| Reply text | `message.content` | `choices[0].message.content`; must be a `str` (a `null` content is `OpenAICompatibleError`) |
| Length stop | `done_reason == "length"` | `choices[0].finish_reason == "length"` |
| Counters | `prompt_eval_count` / `eval_count` | `usage.prompt_tokens` / `usage.completion_tokens`, both through `measured_counters`; missing or non-int means unmeasured (fail-open on the signal) |
| Embed endpoint | `POST {host}/api/embed` `{"model","input"}` | `POST {root}/v1/embeddings` `{"model","input","encoding_format":"float"}` |
| Embed rows | `embeddings[]` (or legacy `embedding`) | `data[]`, sorted by integer `index` when every entry carries one, else response order; count must equal input count |
| Row validation | `EMBED_DIM` length, numeric | same, raising `OpenAICompatibleEmbeddingDimensionMismatch`; then L2-normalized; a zero-norm row is a generic (retryable) `OpenAICompatibleError` |
| Model listing | `GET {host}/api/tags` | `GET {root}/v1/models`, `data[].id` → `InstalledModel(tag=id, family=None)` |
| Auth | none | `Authorization: Bearer <key>` only when a key was passed |
| Locality | `classify_backend_host(resolved_host)` | `classify_backend_host(resolved_base_url)` |

`{root}` is the configured `base_url` with trailing `/` stripped and one
trailing `/v1` stripped, so both `http://127.0.0.1:8080` and
`http://127.0.0.1:8080/v1` produce `…/v1/chat/completions`. Any other path
prefix is kept (reverse-proxy mounts). The client never reads the environment.

HTTP error mapping (`_map_http_error`): 404, or 400 whose body carries
`"model_not_found"`, whose detail mentions the model and `not found` or
`does not exist` → `OpenAICompatibleModelNotFound`; 401/403 →
`OpenAICompatibleError` whose message says authentication failed and names
`OPENKOS_OPENAI_API_KEY` (never its value); everything else →
`OpenAICompatibleError` with the status and the server's detail. Transport
failures → `OpenAICompatibleUnavailable`, naming `locality.display_host`
only (the #355 rule). The client's default transport never follows an HTTP
redirect (any status, any method) and instead raises a typed
`OpenAICompatibleError` naming the status and the redirect target with its
query string stripped, because `urllib`'s default redirect handling copies
the `Authorization` header onto the redirected request regardless of
destination host.

Generation-capped messages keep Ollama's three-way "which bound actually bound"
branching (#440/#829) but name the server's own context size, not
`context_window`, as the setting to raise when the window is what filled,
because the window is not sent: "raise the server's context size (`-c` for
llama.cpp, `--max-model-len` for vLLM) and set `context_window` to match".
**Alternatives considered**: sending `response_format` / JSON mode; sending a
non-standard `num_ctx`; trusting the server to normalize embeddings;
per-request `n_ctx` extensions.
**Rationale**: Only fields in the common OpenAI schema subset are portable
across llama.cpp, LM Studio, vLLM and LocalAI. Structured output stays on the
fail-closed extraction in `llm/parsing.py` for both backends (AGENTS.md).
`encoding_format: "float"` is sent explicitly because a server defaulting to
base64 would otherwise fail row validation. Client-side normalization is cheap,
idempotent on unit vectors, and is what `graph/proximity.py`'s
`sqrt(2 - 2·cos)` conversion requires.

### Decision 3: Neutral mid-level error bases, and every catch site migrated to them

**Choice**: `llm/base.py` gains `BackendModelNotFound(BackendError)`,
`BackendGenerationCapped(BackendError)` and
`BackendEmbeddingDimensionMismatch(BackendError)`. Ollama's classes gain them
as a second base, exactly as `OllamaUnavailable` gained `BackendUnavailable`
in #995:

```
BackendError
├── OllamaError ─────────────────────────────┐
│   ├── OllamaUnavailable        (+ BackendUnavailable)
│   ├── OllamaModelNotFound      (+ BackendModelNotFound)
│   ├── OllamaGenerationCapped   (+ BackendGenerationCapped)
│   └── OllamaEmbeddingDimensionMismatch (+ BackendEmbeddingDimensionMismatch)
└── OpenAICompatibleError
    ├── OpenAICompatibleUnavailable        (+ BackendUnavailable)
    ├── OpenAICompatibleModelNotFound      (+ BackendModelNotFound)
    ├── OpenAICompatibleGenerationCapped   (+ BackendGenerationCapped)
    └── OpenAICompatibleEmbeddingDimensionMismatch (+ BackendEmbeddingDimensionMismatch)
```

Every `except`/`isinstance` on a concrete Ollama class outside `llm/ollama.py`
is rewritten to the neutral name (`OllamaError`→`BackendError`,
`OllamaUnavailable`→`BackendUnavailable`, and so on), keeping each ladder's
order. `is_timeout_failure` tests `BackendUnavailable`. An AST guard test
(`tests/unit/llm/test_neutral_catch_sites.py`) fails if any module under
`src/openkos/` other than `llm/ollama.py` and `llm/openai_compatible.py` names
a concrete backend error class in an `except` clause or `isinstance` call.
**Alternatives considered**: add an `or`-branch naming both concrete classes
at every site (the proposal's assumption); rename Ollama's classes to neutral
names.
**Rationale**: The migration is behavior-neutral for the default path, because
no `BackendError` that is not an `OllamaError` is raised anywhere today, so the
widened clauses catch exactly the same exceptions until the new client ships.
Dual-name branches would double ~55 sites and rot with a third backend.
Renaming was rejected by owner decision 7 and would break every external
`except OllamaError`. The MRO is consistent (both parents share the single
root `BackendError`).

### Decision 4: One resolver in `application/backends.py`, fed by adapter-owned factories

**Choice**: `application/backends.py` owns backend selection, endpoint
resolution and API-key reading; the concrete classes arrive in a
`BackendFactories` value that each adapter builds from its own module globals
at call time.

- `cli/main.py::_backend_factories()` returns
  `BackendFactories(ollama=OllamaClient, openai_compatible=OpenAICompatibleClient)`
  read from `cli/main.py`'s globals, so the ~144 existing test patches of
  `openkos.cli.main.OllamaClient` keep intercepting.
- `cli/curate.py` no longer constructs a client: `CurateContext` gains a
  `backend_factories` field that `cli/main.py` fills, and the stage loop calls
  `application_backends.chat_client(ctx.cfg, factories=ctx.backend_factories, task=stage.task)`.
  This removes the "curate is the stated exception" note and brings curate's
  chat construction under the CLI's single seam (the four tests patching
  `openkos.cli.curate.OllamaClient` move to `openkos.cli.main.OllamaClient`).
- `mcp/server.py::_backend_factories()` is the MCP adapter's equivalent (it
  may not import `openkos.cli`).

The conftest autouse fixture patches both names in `openkos.cli.main` and in
`openkos.mcp.server` through one helper, from one tuple of binding modules.
A source-derived guard (`tests/unit/test_backend_construction_guard.py`) fails
if `OllamaClient(` or `OpenAICompatibleClient(` is called anywhere under
`src/openkos/` outside `llm/` and the `BackendFactories(...)` expressions — so
"every construction goes through the resolver" is enforced, not remembered.
**Alternatives considered**: (a) a single `openkos.llm.registry` module holding
both classes as the only patch point; (b) conftest patching the resolver
function itself; (c) keeping the direct embed constructions and patching two
names.
**Rationale**: (a) and (b) are literally one patch point, but they orphan every
existing `openkos.cli.main.OllamaClient` spy and recording double (about 144
patches across 20 test files), which is several hundred lines of churn with no
behavior gain, and (b) would silently bypass the per-test patches. Keeping the
adapter globals as the binding and enforcing "all construction via the
resolver" with a source guard gives the owner decision's guarantee (no test
that selects the new backend can reach the network; a third backend changes
the resolver plus one line per adapter) at a fraction of the churn. The
refinement of "one patch point" to "one binding per adapter, one fixture" is
recorded here and in the risks.

### Decision 5: Endpoint precedence and source reporting

**Choice**: `resolve_endpoint(cfg, purpose, environ=os.environ) -> Endpoint(url, source)`:

| backend | purpose | precedence (first non-empty wins) | `url` passed to the client |
| --- | --- | --- | --- |
| `ollama` | chat | `OLLAMA_HOST` > `base_url` > default | `None` when from `OLLAMA_HOST` or default (the client resolves it exactly as today); `base_url` when from config |
| `ollama` | embed | `OLLAMA_HOST` > `embedding_base_url` > `base_url` > default | same rule |
| `openai-compatible` | chat | `base_url` (required) | `base_url` |
| `openai-compatible` | embed | `embedding_base_url` > `base_url` | the winner |

`source` is one of `"OLLAMA_HOST"`, `"embedding_base_url"`, `"base_url"`,
`"default"`. An explicit constructor argument still beats everything, because
the resolver only passes `host=` when config supplied it; direct constructions
(evals, tests) keep `arg > OLLAMA_HOST > default`. `OLLAMA_HOST` is not
consulted for `openai-compatible`; it names Ollama.

The Ollama factory receives `host=` **only** when `source` is a config key, so
the default-path kwargs stay byte-identical (`test_backends_delegation.py` and
`test_chat_timeout_wiring.py` assert the exact kwargs).

`doctor` shows the effective chat endpoint and its source in the reachable
check's detail, and the embedding endpoint when it differs:
`12 models at 127.0.0.1:8080 (from base_url)`. When `source == "default"` the
detail is unchanged (`12 models`), preserving the default-path bytes.
**Alternatives considered**: `base_url` overriding `OLLAMA_HOST`; a default
`base_url` for `openai-compatible` (`http://localhost:8080`).
**Rationale**: Environment-over-file is conventional, keeps a user who only
exports `OLLAMA_HOST` on the same endpoint, and keeps
`evals/run_self_tests.py`'s poisoned-`OLLAMA_HOST` guarantee effective for any
CLI subprocess an eval runs inside a workspace (proposal constraint, O3).
There is no common default port across OpenAI-compatible servers (llama.cpp
8080, LM Studio 1234, vLLM 8000), so guessing one would produce confusing
"unreachable" errors; requiring `base_url` fails at config read with a clear
message instead.

### Decision 6: Config surface, validation and rollback tolerance

**Choice**: Three optional top-level keys, appended to `Config` with defaults so
every existing `Config(...)` construction in tests keeps compiling:
`backend: str = "ollama"`, `base_url: str | None = None`,
`embedding_base_url: str | None = None`. `read_config` validates:

- `backend`: a string in `SELECTABLE_BACKENDS`. Until the enabling slice that
  set is `{"ollama"}`; `openai-compatible` is refused with
  `openkos.yaml: 'backend: openai-compatible' is not available in this version; supported: ollama`.
  Any other value is refused listing the supported values. `null`/absent →
  `ollama`.
- `base_url` / `embedding_base_url`: a string starting with `http://` or
  `https://`, non-empty host, no whitespace, no userinfo (`@` in the
  authority is refused with a message pointing at `OPENKOS_OPENAI_API_KEY`),
  no query or fragment. Stored with the trailing `/` stripped.
- `backend: openai-compatible` without `base_url` is refused.
- The API key is never a config key. If `openkos.yaml` contains `api_key`,
  `openai_api_key` or `OPENKOS_OPENAI_API_KEY`, `read_config` refuses it with a
  message saying the key is read only from the environment, because `init`
  autocommits `openkos.yaml`.

Unknown top-level keys stay ignored, as today (`read_config` reads by
`raw.get`; only the `models:` sub-mapping rejects unknown keys). That is the
rollback story: a binary older than the config slice ignores `backend`,
`base_url` and `embedding_base_url` and runs Ollama at `OLLAMA_HOST`/default,
which is local by default; its embedding tag does not match a stored
backend-qualified tag, so it re-embeds rather than reusing foreign vectors.
**Alternatives considered**: a strict unknown-key policy; an `api_key` config
key with a warning; accepting bare `host:port` like `OLLAMA_HOST` does.
**Rationale**: Strict unknown keys would break every rollback and every hand
edit, and nothing in the current config does it. A declared endpoint should be
unambiguous in a git-diffable file, so it must be a full URL; credentials in a
URL or a key are refused because the file is committed. Refusing a
misplaced key loudly is safer than ignoring it, which would leave the user's
secret in their repository believing it was used.

### Decision 7: Embedding identity carries the backend kind, legacy reads as `ollama`

**Choice**: In `state/reindex.py`:

```python
def embedding_tag(model: str, backend: str = config.DEFAULT_BACKEND) -> str:
    tag = f"{model}#{EMBED_COMPOSITION_TAG}"
    return tag if backend == config.DEFAULT_BACKEND else f"{tag}#backend={backend}"

@dataclass(frozen=True, slots=True)
class EmbeddingTagParts:
    model: str
    composition: str   # "" for a pre-composition legacy tag
    backend: str       # config.DEFAULT_BACKEND when the tag has no backend part

def parse_embedding_tag(tag: str) -> EmbeddingTagParts: ...
```

`parse_embedding_tag` splits on `#`: the first field is the model, the second
the composition, and each further `key=value` field is an attribute; only
`backend=` is read, and unknown attributes are ignored (forward-compatible).
`#` and `=` cannot occur in a model token (`config._MODEL_TOKEN_RE` allows
only `[A-Za-z0-9._:/-]`), so the encoding cannot be confused with a model name
containing `:` or `/`.

`reindex(...)` gains `embedding_backend: str = config.DEFAULT_BACKEND`;
`_effective_model_tag(model_tag, backend)` delegates to `embedding_tag`. Every
`reindex(model_tag=...)` caller (`_embed_after_ingest` at `main.py:4058`, whose
callers pass `cfg.embedding_model` at `main.py:4184` and `main.py:5724`, and
`reindex` at `main.py:15523`) and `application/revisions.py:314` pass
`cfg.backend`. For `ollama` the tag is `<model>#chunk-v1`, byte-identical,
so an existing store does not re-embed.

The question-vector cache key uses the same rule without the composition part:
`question_vectors.cache_key(model, backend)` returns `model` for `ollama`
(existing rows stay valid) and `f"{model}#backend={backend}"` otherwise;
`application/query.py:786` uses it.

`cli/main.py::_reembed_trigger_wording` parses both tags with
`parse_embedding_tag` and discloses, in order: no previous tag; **backend
changed** (`embedding backend changed (ollama -> openai-compatible)`); model
changed; composition changed. The four branches are mutually exclusive, with
no appended model clause when both changed; this follows the
`reindex-command` delta spec, which wins over the earlier appended-clause
sketch. A legacy tag with no backend part is read
as `ollama`, so it is never reported as a backend change.
**Alternatives considered**: tag by model only; include the `base_url`; a prefix
form (`openai-compatible::<model>#chunk-v1`); a separate `embedding_backend`
meta row in `vectors.db`.
**Rationale**: Model-only reuses incompatible vectors across a backend switch
(the proposal's high-likelihood risk). The URL forces a re-embed on any port
change and writes host details into `vectors.db` (decision 12, O2). A prefix
changes the first `#` field, which every existing partition-based reader
treats as the model. A second meta row would need a second gate and a schema
change; the existing single gate already does the job (`EMBED_COMPOSITION_TAG`
docstring: "find it and use it; do not invent a parallel one"). Residual risk:
switching servers within one backend kind with the same model name keeps old
vectors; the remedy is `openkos reindex --force`, documented.

### Decision 8: Environment-only API key, read in the application layer, warned over plain HTTP

**Choice**: `application/backends.py` reads `OPENKOS_OPENAI_API_KEY` from an
injectable `environ` (default `os.environ`), strips it, treats empty as absent,
and passes it as `api_key=` to the `openai-compatible` factory only. `Config`
never holds it, so no `repr(cfg)` can print it. The client stores it privately,
sends it only as the `Authorization` header, never includes it in an exception
message, and defines no `__repr__` that includes it. `doctor` reports only
`API key: set` / `not set` for `openai-compatible`.

`insecure_key_warning(cfg, environ) -> str | None` (pure) returns a warning
when a key is set, the backend is `openai-compatible`, and an effective
endpoint is `http://` and classifies non-local. The CLI prints it to stderr at
most once per process from the `_chat_client` / `_embed_client` delegators; the
MCP server prints it once to stderr at startup; `doctor` shows it as the detail
of the reachable check. It never blocks.

`OPENAI_API_KEY` is deliberately **not** read.
**Alternatives considered**: a YAML key; reading `OPENAI_API_KEY` as a
fallback; refusing to send a key over `http://` to a remote host.
**Rationale**: `init` autocommits `openkos.yaml` (owner decision 2). A
fallback to `OPENAI_API_KEY` would forward a user's cloud credential to
whatever `base_url` names. Refusing outright would break TLS-terminating
setups on a trusted LAN; the principle is "never silently", so it warns. The
`llm` package stays config-free and environment-free (the `llm-client` spec).

### Decision 9: Backend-conditional remediation text as pure functions

**Choice**: `application/backends.py` gains pure wording functions, each
returning today's exact Ollama bytes for `backend == "ollama"`:

| Function | `ollama` (byte-identical) | `openai-compatible` |
| --- | --- | --- |
| `start_hint(cfg)` | ``Start it with `ollama serve` `` | `Start your OpenAI-compatible server at <display_host>` |
| `install_hint(cfg, model)` | ``Pull it with `ollama pull <model>` `` | `Make sure your OpenAI-compatible server serves '<model>' (run `openkos doctor` to see the models it reports)` |
| `endpoint_label(cfg, purpose)` | the resolved `source` (`OLLAMA_HOST` in the only case a non-local Ollama endpoint exists today) | `base_url` / `embedding_base_url` |
| `backend_label(cfg)` | `Ollama` | `OpenAI-compatible server` |

`doctor` keeps its own command-form strings (`ollama serve`,
`ollama pull <model>`, the `shutil.which("ollama")` branch) for `ollama` and
gains `openai-compatible` forms: remediation `start your OpenAI-compatible
server at <display_host>`; model-missing remediation naming the listed ids
(`the server reports: a, b, c -- set model: to one of them`); labels
`OpenAI-compatible server reachable` and `blocked: server unreachable`.
The two stderr advisories (`_warn_if_nonlocal_embed_host`,
`_warn_withheld_from_embedding`) take the `endpoint_label` instead of the
literal `OLLAMA_HOST`. Each CLI/curate handler keeps its surrounding sentence
and calls the helper for the backend-specific clause.
**Alternatives considered**: a generic backend-neutral wording for both
backends; wording owned by the exception classes.
**Rationale**: Generic wording would break the pinned Ollama goldens and
lose the actionable command Ollama users rely on. Exceptions live in the
config-free `llm` package and cannot know which config key set the endpoint.
Pure functions over `cfg` are testable without a CLI run and shared by the
CLI, curate and doctor.

### Decision 10: `openai-compatible` becomes selectable only in the enabling slice

**Choice**: `SELECTABLE_BACKENDS` is `{"ollama"}` until slice 14, which adds
`"openai-compatible"` together with the template comments and the end-to-end
tests through the offline double. Every earlier slice is inert on `main`.
**Alternatives considered**: a hidden environment flag to unlock early.
**Rationale**: Stacked-to-main means every slice is a releasable `main`. A
state where the value is accepted but a handler only knows Ollama's classes,
or the tag ignores the backend, must never exist (proposal decision 11). A
hidden flag would be one more untested surface.

## Data Flow

Chat construction (every chat verb, CLI and MCP):

```
cli verb / mcp tool
   │  _chat_client(cfg, task)            (cli/main.py delegator; mcp: _make_llm)
   ▼
application.backends.chat_client(cfg, factories=_backend_factories(), task)
   │ 1 model   = config.resolve_task_model(cfg, task)
   │ 2 endpoint = resolve_endpoint(cfg, "chat", os.environ)   ─► Endpoint(url, source)
   │ 3 key      = read_api_key(os.environ)   (openai-compatible only)
   │ 4 factory  = factories.ollama | factories.openai_compatible   (by cfg.backend)
   ▼
OllamaClient(model, timeout, max_generation_tokens, context_window, temperature, seed[, host])
OpenAICompatibleClient(model, base_url, timeout, max_generation_tokens, context_window,
                       temperature, seed, api_key)
   │
   ├─► .locality ─► classify_backend_host(resolved url)  (llm/base.py, literal loopback only)
   │        └─► resolve_local_exemption(client, cfg)  ─► sensitivity.should_block
   └─► .chat(messages) ─► HTTP ─► str | BackendError family
```

Embedding and the tag gate (`ingest` post-embed, `reindex`):

```
cli: embedder = _embed_client(cfg)  ─► backends.embed_client(cfg, factories)
         │                                   (endpoint purpose="embed")
         ├─► _warn_if_nonlocal_embed_host(cmd, embedder.locality, endpoint_label)
         ▼
state.reindex.reindex(..., model_tag=cfg.embedding_model, embedding_backend=cfg.backend)
         │  stored = db.read_model_tag()
         │  effective = embedding_tag(model, backend)
         │  stored != effective ─► force full re-embed ─► write effective tag
         ▼
cli: _reembed_trigger_wording(parse_embedding_tag(stored), parse_embedding_tag(effective))
```

Sequence for a backend switch on an existing Ollama store:

```
user          openkos.yaml         reindex            vectors.db         server
 │ backend: openai-compatible, base_url: http://127.0.0.1:8080
 │──────────────►│
 │ openkos reindex                  │
 │─────────────────────────────────►│ read_model_tag ──►│ "bge-m3#chunk-v1"
 │                                  │ effective = "bge-m3#chunk-v1#backend=openai-compatible"
 │                                  │ mismatch: queue every doc
 │                                  │ POST /v1/embeddings ───────────────────────►│
 │                                  │◄──────────── data[] (normalized client-side)│
 │                                  │ write vectors + tag ─►│
 │◄─ "embedding backend changed (ollama -> openai-compatible)" ─│
```

## Construction sites routed through the resolver

| Site (as of `032ba61`) | Kind | Becomes |
| --- | --- | --- |
| `cli/main.py:171` `_chat_client` | chat | `chat_client(cfg, factories=_backend_factories(), task=task)` |
| `cli/main.py:317` `_probe_installed_models` (init picker) | diagnostics | `diagnostics_client(None, model=DEFAULT_MODEL, timeout=_PREFLIGHT_TIMEOUT, factories=…)` — Ollama default, O1 |
| `cli/main.py:1748` init preflight | diagnostics | same, `model=resolved_model` — Ollama default, O1 |
| `cli/main.py:4166` `_refresh_derived_after_write` | embed | `_embed_client(cfg)` |
| `cli/main.py:4734` `_ingest_batch` embed-host advisory | embed (locality only) | `_embed_client(cfg).locality` plus `endpoint_label` |
| `cli/main.py:5720` `_ingest_single` | embed | `_embed_client(cfg)` |
| `cli/main.py:14793` `query` | embed | `_embed_client(cfg)` |
| `cli/main.py:15508` `reindex` | embed | `_embed_client(cfg)` |
| `cli/main.py:15890` `doctor` `_build_client` | diagnostics | `lambda cfg, model: diagnostics_client(cfg, model=model, timeout=_PREFLIGHT_TIMEOUT, factories=…)` |
| `cli/main.py:16339` `mcp_cmd` | embed | `_embed_client(cfg)` |
| `cli/curate.py:2066` stage chat | chat | `chat_client(ctx.cfg, factories=ctx.backend_factories, task=stage.task)` |
| `mcp/server.py:192` `_make_llm` | chat | `chat_client(cfg, factories=_backend_factories())` |
| `mcp/server.py:196` `_make_embedder` | embed | `embed_client(cfg, factories=_backend_factories())` |

`_embed_client(cfg)` is a new one-line CLI delegator mirroring `_chat_client`.
Embedding clients keep the transport default timeout (no `chat_timeout`), as
today. Liveness probes keep `_PREFLIGHT_TIMEOUT`.

## File Changes

| File | Action | Description |
| --- | --- | --- |
| `src/openkos/llm/base.py` | Modify | receive `classify_backend_host` + helpers, `measured_counters`, `is_timeout_failure`; add `BackendModelNotFound`, `BackendGenerationCapped`, `BackendEmbeddingDimensionMismatch`; add `ConfiguredClient` Protocol (chat + embed + list_models + locality + context_window + max_generation_tokens) as the resolver's return type; rewrite module docstring |
| `src/openkos/llm/ollama.py` | Modify | re-export moved names; Ollama error classes gain neutral second bases; no request/response change |
| `src/openkos/llm/openai_compatible.py` | Create | `OpenAICompatibleClient`, error hierarchy, `_map_http_error`, `_validate_and_normalize_row` |
| `src/openkos/config.py` | Modify | `DEFAULT_BACKEND`, `SELECTABLE_BACKENDS`, `Config.backend/base_url/embedding_base_url`, validation, misplaced-key refusal |
| `src/openkos/templates/openkos.yaml.template` | Modify | commented `# backend:`, `# base_url:`, `# embedding_base_url:` entries and the env-key note (enabling slice only) |
| `src/openkos/application/backends.py` | Modify | `BackendFactories`, `Endpoint`, `resolve_endpoint`, `chat_client` dispatch, `embed_client`, `diagnostics_client`, API-key read, `insecure_key_warning`, wording functions; module docstring loses the curate exception |
| `src/openkos/application/doctor.py` | Modify | `build_client(cfg, model)`; backend-conditional labels/remediation; endpoint + source detail; key set/not-set; separate embedding-endpoint listing when it differs |
| `src/openkos/application/revisions.py` | Modify | `embedding_tag(model, cfg.backend)` |
| `src/openkos/application/query.py` | Modify | question-cache key via `question_vectors.cache_key`; docstring names neutral errors |
| `src/openkos/application/ingest.py` | Modify | neutral error names where concrete ones are referenced |
| `src/openkos/state/reindex.py` | Modify | `embedding_tag(model, backend)`, `EmbeddingTagParts`, `parse_embedding_tag`, `embedding_backend` param, neutral catches |
| `src/openkos/state/question_vectors.py` | Modify | `cache_key(model, backend)` |
| `src/openkos/retrieval/answer.py` | Modify | neutral catches and fatal tuple |
| `src/openkos/resolution/{adjudication,contradiction,edge_typing,volatility_typing,decision_revision,decision_subject}.py` | Modify | `except BackendError` |
| `src/openkos/extraction/judge.py`, `extraction/concept.py` | Modify | neutral names where concrete ones remain |
| `src/openkos/cli/main.py` | Modify | `_backend_factories`, `_embed_client`, construction sites, neutral catches, wording helpers, `_reembed_trigger_wording` via parser, advisories take the endpoint label, once-per-process key warning |
| `src/openkos/cli/curate.py` | Modify | `CurateContext.backend_factories`, resolver construction, neutral catches, wording helpers |
| `src/openkos/mcp/server.py` | Modify | `_backend_factories`, embed via resolver, neutral catches, startup key warning |
| `tests/unit/conftest.py` | Modify | `OfflineOpenAICompatible`; patch both classes in both binding modules through one helper; `delenv("OPENKOS_OPENAI_API_KEY")` |
| `tests/unit/test_network_guard.py` | Modify | the source-derived coverage check parametrized over `(OllamaClient, OfflineOllama)` and `(OpenAICompatibleClient, OfflineOpenAICompatible)`; seam-installed pins for the new name |
| `tests/unit/test_backend_construction_guard.py` | Create | no direct client construction outside `llm/` and `BackendFactories(...)` |
| `tests/unit/llm/test_neutral_catch_sites.py` | Create | no concrete backend error named in `except`/`isinstance` outside the two client modules |
| `tests/unit/llm/test_openai_compatible*.py` | Create | fake-`urlopen` tests: mapping, ladder, capped branching, embeddings, listing, locality, key header, thread-safety AST guard |
| `tests/unit/llm/test_base_locality.py` (or existing locality tests) | Modify | import path follows the move; re-export identity pinned |
| `tests/unit/application/test_backends*.py`, `tests/unit/test_config.py`, `tests/unit/state/test_reindex*.py`, `tests/unit/cli/*` | Modify/Create | resolver, config, tag, wording, e2e through the offline double |
| `docs/adr/0031-openai-compatible-backend.md`, `docs/adr/README.md` | Create/Modify | ADR-0031 (Proposed) and its index row |
| `AGENTS.md` | Modify | add `llm` to the commit-scope list (slice 1) |
| `docs/tech_stack.md`, `docs/architecture.md`, `docs/cli.md` | Modify | shape-level mention of the second backend and its known limitations (slice 15) |

## Interfaces / Contracts

```python
# llm/openai_compatible.py  (leaf: stdlib + openkos.llm.base only; no env, no config)
class OpenAICompatibleClient:
    def __init__(
        self,
        model: str,
        *,
        base_url: str,
        timeout: float = DEFAULT_TIMEOUT,            # same 600.0 floor as Ollama
        max_generation_tokens: int | None = None,
        temperature: float | None = None,
        seed: int | None = None,
        context_window: int | None = None,           # advisory: never sent
        api_key: str | None = None,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        embed_retry_attempts: int = DEFAULT_EMBED_RETRY_ATTEMPTS,
        embed_retry_backoff_base: float = DEFAULT_EMBED_RETRY_BACKOFF_BASE,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None: ...
    @property
    def resolved_base_url(self) -> str: ...
    @property
    def locality(self) -> BackendHostLocality: ...
    @property
    def context_window(self) -> int | None: ...       # advisory; docstring says so
    @property
    def max_generation_tokens(self) -> int | None: ...
    def chat(self, messages: Sequence[Message]) -> str: ...        # thread-safe: locals only
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...  # unit-norm EMBED_DIM rows
    def list_models(self) -> list[InstalledModel]: ...

# application/backends.py  (imports config, llm.base, stdlib only)
BACKEND_OLLAMA: Final = "ollama"
BACKEND_OPENAI_COMPATIBLE: Final = "openai-compatible"
API_KEY_ENV: Final = "OPENKOS_OPENAI_API_KEY"
EndpointSource = Literal["OLLAMA_HOST", "embedding_base_url", "base_url", "default"]

@dataclass(frozen=True, slots=True)
class Endpoint:
    url: str | None
    source: EndpointSource

@dataclass(frozen=True, slots=True)
class BackendFactories:
    ollama: Callable[..., ConfiguredClient]
    openai_compatible: Callable[..., ConfiguredClient]

def resolve_endpoint(cfg: config.Config, *, purpose: Literal["chat", "embed"],
                     environ: Mapping[str, str] = os.environ) -> Endpoint: ...
def chat_client(cfg: config.Config, *, factories: BackendFactories,
                task: str | None = None) -> ConfiguredClient: ...
def embed_client(cfg: config.Config, *, factories: BackendFactories) -> ConfiguredClient: ...
def diagnostics_client(cfg: config.Config | None, *, model: str, timeout: float,
                       factories: BackendFactories,
                       purpose: Literal["chat", "embed"] = "chat") -> BackendDiagnostics: ...
def insecure_key_warning(cfg: config.Config, *,
                         environ: Mapping[str, str] = os.environ) -> str | None: ...
def start_hint(cfg: config.Config | None) -> str: ...
def install_hint(cfg: config.Config | None, model: str) -> str: ...
def endpoint_label(cfg: config.Config, *, purpose: Literal["chat", "embed"]) -> str: ...
def backend_label(cfg: config.Config | None) -> str: ...

# application/doctor.py
def run_diagnostics(root: Path, *,
                    build_client: Callable[[config.Config | None, str], BackendDiagnostics],
                    ...) -> tuple[CheckResult, ...]: ...

# state/reindex.py
def embedding_tag(model: str, backend: str = config.DEFAULT_BACKEND) -> str: ...
def parse_embedding_tag(tag: str) -> EmbeddingTagParts: ...
def reindex(..., model_tag: str | None = None,
            embedding_backend: str = config.DEFAULT_BACKEND, ...) -> ReindexReport: ...

# state/question_vectors.py
def cache_key(model: str, backend: str) -> str: ...
```

`chat_client`'s parameter changes from `factory=` to `factories=`. The `mcp`
spec requirement ("Chat-Client Construction … Behind An Injected Factory")
still holds: the concrete classes are injected, never bound in
`application/`.

## Testing Strategy

Strict TDD (`uv run pytest`): every slice starts with an observed RED.

| Layer | What to Test | Approach |
| --- | --- | --- |
| Unit — llm | locality move is behavior-identical; re-exports are the same objects | the existing `classify_backend_host` table run against both import paths; `ollama.classify_backend_host is base.classify_backend_host` |
| Unit — llm | neutral bases: every Ollama class is an instance of its neutral base; MRO | `issubclass` table |
| Unit — llm | new client chat mapping: body keys present/absent per `None`; `max_tokens`; no `num_ctx`/`think`; header only with key | fake `urlopen` capturing `Request` |
| Unit — llm | ladder: connect error, read error, `IncompleteRead`, HTTPError 404/400 model-not-found, 401/403, 500, malformed JSON, `null` content | fake `urlopen`, one test per rung (mirrors `test_ollama.py`) |
| Unit — llm | capped branching: ceiling bound, window bound, neither bound, no counters, no ceiling; `bool` counters rejected | parametrized over `usage` shapes |
| Unit — llm | embeddings: `index` reordering, count mismatch, wrong length → mismatch (not retried), non-unit row → unit norm (`abs(norm-1) < 1e-9`), zero row → generic error (retried), `encoding_format` sent | fake `urlopen` + spy `sleep` |
| Unit — llm | `chat` writes no instance attribute | AST guard copied from `test_ollama.py` (#748) |
| Unit — llm | key never in `str(exc)` for any rung | parametrized ladder with a sentinel key |
| Unit — llm | catch sites neutral; no direct construction | the two source guards |
| Unit — config | defaults; each validation refusal; `openai-compatible` refused before slice 14 and accepted after; misplaced key refused; unknown keys ignored | `tmp_path` workspaces |
| Unit — application | precedence table (every row, with an injected `environ`); default-path kwargs identical; dispatch by backend; key read, stripped, empty-as-absent, never for Ollama; `insecure_key_warning` matrix (key × scheme × locality) | recording factories |
| Unit — application | wording functions return today's Ollama bytes | literal comparison against the pre-change strings |
| Unit — state | `embedding_tag` Ollama bytes unchanged; round-trip parse; legacy/bare/pre-composition tags; unknown attribute ignored; backend switch forces re-embed; Ollama store after upgrade does not | `VectorStore` in `tmp_path`, fake embedder |
| Unit — cli | disclosure four branches; advisories name the endpoint source; handlers per backend; once-per-process key warning; doctor labels, endpoint/source detail, key set/not set, no key value anywhere in output | `CliRunner` with the offline doubles; the sentinel key asserted absent from stdout+stderr |
| Unit — network guard | stub coverage derived from each client's source; both seams installed by default, both lifted by `live_backend` | extend `test_network_guard.py` |
| E2E (unit suite, offline) | `backend: openai-compatible` workspace: `ingest`, `query`, `reindex`, `doctor`, MCP `query` construct the new client, reach only the offline double, report local for a loopback `base_url`, withhold confidential for a remote one | slice 14, through `OfflineOpenAICompatible` |
| Evals | self-test sweep stays model-free | unchanged `evals/run_self_tests.py`; no eval selects the new backend (F1) |

No live-server test is added. A `@pytest.mark.live_backend` pin like
`test_ollama_embed_norm.py` is unnecessary for normalization, because the
client normalizes by construction.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary. Every row of the reference
matrix (documentation-like paths, git repository selection, commit state, push
state, PR commands) is N/A: the change adds an HTTP client and config keys,
and touches no shell or git path.

The change does cross a **data-egress boundary**, which is covered by these
design requirements (each carried to tasks as RED tests):

| Boundary | Adversarial case | Required behavior | RED test |
| --- | --- | --- | --- |
| Locality | `base_url` of `http://localhost.evil.com`, `http://127.1`, `http://[::1`, `http://user:pw@127.0.0.1`, IPv6 expanded loopback | classified by the moved `classify_backend_host` exactly as for Ollama; non-local on anything not literally loopback; userinfo refused at config read | locality table run through `OpenAICompatibleClient.locality` |
| Confidential exemption | remote `base_url` or remote `embedding_base_url` with `confidential_local_exemption: true` | exemption false; confidential withheld from chat and embed independently | exemption test per purpose |
| Secret in workspace | `api_key:` in `openkos.yaml`; `base_url` with userinfo | refused at `read_config` | config tests |
| Secret disclosure | key set; every error rung; `doctor`; advisories | key value never in stdout, stderr, exception text, or `repr` | sentinel-key assertions |
| Secret over plain HTTP | key + `http://` + non-loopback | one stderr warning per process; request still sent | warning matrix |
| Credential confusion | `OPENAI_API_KEY` exported | never read, never sent | environ-injection test |

## Migration / Rollout

No data migration. Existing Ollama stores keep their tag and their
question-vector rows. A switch to `openai-compatible` re-embeds once through
the existing tag gate.

Slice plan (auto-chain, stacked to main, dependency order). Forecasts are
authored additions plus deletions, scaled by the observed ~1.95x
forecast-to-actual ratio, excluding delta specs. Each slice is green and inert
on `main` until slice 14.

| # | Slice | Contents | Scaled forecast | Depends on |
| --- | --- | --- | --- | --- |
| 1 | Shared helpers move + scope | `classify_backend_host` + helpers, `measured_counters`, `is_timeout_failure` to `llm/base.py`, re-exported; base docstring; `llm` scope in AGENTS.md | ~300–400 | — |
| 2 | Neutral error bases + catch sites | three neutral bases; Ollama second bases; every concrete catch migrated; `test_neutral_catch_sites.py` (split by module into 2a non-CLI / 2b CLI+curate+MCP if it exceeds 400) | ~350–450 | 1 |
| 3 | Config surface | `backend` (only `ollama` selectable), `base_url`, `embedding_base_url`, validation, misplaced-key refusal | ~300–450 | — |
| 4 | Client: chat core | error hierarchy, transport, `/v1/chat/completions`, ladder, key header | ~350–450 | 1, 2 |
| 5 | Client: chat bounds | `max_tokens`, `finish_reason`, `usage`, capped branching, advisory window, temperature/seed | ~250–400 | 4 |
| 6 | Client: embeddings | `/v1/embeddings`, ordering, `EMBED_DIM`, normalization, retry | ~300–400 | 4 |
| 7 | Client: diagnostics | `/v1/models`, locality, cross-backend exemption test | ~200–300 | 4 |
| 8 | Offline double + network guard | `OfflineOpenAICompatible`, derived coverage for both clients | ~200–300 | 4–7 |
| 9 | Resolver seam | `BackendFactories`, `resolve_endpoint`, `chat_client` dispatch, `embed_client`, `diagnostics_client`, key read, conftest via one helper, construction guard (chat + curate + MCP chat sites) | ~350–450 | 3, 8 |
| 10 | Embed-site migration | the seven embed constructions and three probe sites to the resolver | ~250–400 | 9 |
| 11 | Backend-aware identity | `embedding_tag`/`parse_embedding_tag`, `embedding_backend` param, question-vector key, fourth disclosure statement, `revisions.py` | ~250–400 | 9 |
| 12 | Wording: doctor | labels, remediation, endpoint + source, key set/not set, `build_client(cfg, model)` | ~300–450 | 9 |
| 13 | Wording: CLI + curate + MCP | wording helpers at every handler, advisories take the endpoint label, once-per-process key warning (split by module if over budget) | ~300–450 | 9 |
| 14 | **Enable** | `SELECTABLE_BACKENDS` gains `openai-compatible`; template comments; offline end-to-end tests | ~150–300 | 10–13 |
| 15 | Docs | tech_stack, architecture, cli: shape-level mention; known limitations (reasoning output, advisory window, llama.cpp embedding batch size) | ~100–250 | 14 |

Total ~4,000–5,850. Slice 14 is the only slice that makes the new backend
reachable from `openkos.yaml`. ADR-0031 lands with the design commit.

Rollback: revert in reverse order; each slice is green alone. Reverting slice
14 makes `openai-compatible` refused again. Reverting slice 11 after a user
embedded with the new backend forces one full re-embed on the next `reindex`
(self-healing). Reverting slice 3 leaves the new keys ignored (unknown keys
are ignored), so the workspace falls back to Ollama.

## Open Questions

- [x] **Spec alignment on the disclosure wording.** Resolved: the
      `reindex-command` delta spec's four mutually exclusive branches win,
      so there is no appended model clause.
- [x] **Endpoint precedence for `openai-compatible`.** Resolved: the
      `backend-selection` spec now matches this design. `OLLAMA_HOST` is
      never consulted for the OpenAI-compatible backend.
- [ ] **Doctor model check for llama.cpp.** `llama-server` ignores the request
      `model` and lists the GGUF path (or `--alias`) in `/v1/models`, so a
      critical "model not installed" can be a false alarm there. Recommended:
      keep it critical (vLLM and LM Studio route by name), and make the
      remediation name the listed ids and `--alias`. Not blocking.
- [ ] **"One patch point" refinement.** Decision 4 keeps one binding per
      adapter and one fixture instead of a single global name, to avoid
      migrating ~144 test patches. Recommended as designed; the owner may
      prefer the single `llm.registry` binding at the cost of that churn.
- [ ] **Slice count.** The plan has 15 slices (the neutral-error slice is new,
      forced by the catch-site finding). Recommended as designed; merging it
      into slice 1 would exceed the review budget.
