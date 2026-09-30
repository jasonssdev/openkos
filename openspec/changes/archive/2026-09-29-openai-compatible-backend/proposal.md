# Proposal: openai-compatible-backend — a second, local-first LLM backend behind the existing seams

## Intent

Refs #1057. Exploration: Engram `sdd/openai-compatible-backend/explore`.

OpenKOS talks to exactly one model server: Ollama, through `OllamaClient`
(`src/openkos/llm/ollama.py`). Many people who run models locally do so
through a server that speaks the OpenAI-compatible HTTP API instead:
llama.cpp's `llama-server`, LM Studio, vLLM, LocalAI. Today those users
cannot use OpenKOS without also installing Ollama, even though their server
already runs on the same machine and exposes the same capabilities (chat and
embeddings).

The codebase is already most of the way there. `llm/base.py` defines the
backend-neutral Protocols (`LLMBackend`, `Embedder`, `BackendDiagnostics`) and
the neutral error bases (`BackendError`, `BackendUnavailable`); most consumers
catch the generic bases; chat construction is centralized in
`application/backends.py::chat_client` behind an injected factory; `doctor`
takes a client factory; `resolve_local_exemption` is structural. What is
missing is a second concrete client, a way to select it, one construction
seam for embeddings, and backend-neutral remediation text.

Why now: the neutral seams landed with #995, so a second backend no longer
means rewriting consumers. Every new direct `OllamaClient(...)` call site
added from here on makes the eventual change larger.

Success: a user sets `backend: openai-compatible` and `base_url:` in
`openkos.yaml`, points it at a local OpenAI-compatible server, and every
verb that chats or embeds works against it. Locality and the confidential
exemption are decided by the same literal-loopback rule as today. Switching
the embedding backend forces a full re-embed. A user who never touches the
new keys sees no behavior change and no re-embed.

## Scope

### In Scope

1. **Shared locality classifier.** Move `classify_backend_host` and its
   private helpers from `llm/ollama.py` to `llm/base.py`; `ollama.py`
   re-exports it. No behavior change. Literal-loopback only, no DNS,
   fail-closed on anything unparseable.
2. **Config surface.** `backend: ollama | openai-compatible` (default
   `ollama`), a shared `base_url` used by whichever backend is selected,
   and an optional `embedding_base_url` (embeddings are often served by a
   separate process or port). The API key is read only from an environment
   variable (for example `OPENKOS_OPENAI_API_KEY`), never from
   `openkos.yaml`.
3. **New client `llm/openai_compatible.py`.** Same `urllib.request`
   transport shape as `OllamaClient` with an injectable `urlopen`; no new
   HTTP dependency.
   - Chat through `/v1/chat/completions`, non-streaming. `max_tokens` is
     sent per request; `finish_reason == "length"` maps to a
     generation-capped error; `usage` counters are read with the same
     fail-open-on-missing discipline Ollama's counters use; `temperature`
     and `seed` are top-level fields with the same `None`-means-omit rule.
     `context_window` is advisory only: used for OpenKOS's own prompt
     budgeting, never sent (OpenAI-compatible servers fix the context size
     at server start).
   - Embeddings through `/v1/embeddings`, parsed from `data[].embedding` in
     `index` order, with `EMBED_DIM` row validation and client-side L2
     normalization of every returned vector.
   - Diagnostics: `list_models` through `/v1/models` (`family` is always
     `None`), and `locality` through the shared classifier.
   - Errors mirroring Ollama's hierarchy one-to-one under the existing
     `BackendError`/`BackendUnavailable` bases.
4. **One resolver seam.** `application/backends.py` dispatches chat
   construction by `cfg.backend` and gains `embed_client()`. Every chat and
   every embed construction site (the seven direct
   `OllamaClient(model=cfg.embedding_model)` sites in `cli/main.py`,
   `mcp/server.py` and `cli/curate.py`) goes through it, so tests have one
   patch point.
5. **Test infrastructure.** An offline stub for the new client, the
   `test_network_guard.py` source-derived coverage check extended to it, and
   the `tests/unit/conftest.py` autouse fixture patched at the single
   resolver seam instead of the hardcoded `openkos.cli.main.OllamaClient`.
6. **Backend-aware embedding tag.** The reindex embedding tag identifies the
   embedding backend as well as the model name, so a backend switch forces
   a full re-embed. A stored tag without a backend part is read as
   `ollama`, so existing stores see no re-embed.
7. **Backend-aware remediation and advisories.** `doctor`, the `init`
   preflight, and the CLI/curate error handlers keep their current Ollama
   wording for the `ollama` backend and gain accurate wording for the
   `openai-compatible` backend. The non-local-host advisories name the
   configured endpoint rather than `OLLAMA_HOST`.
8. **Governance.** ADR-0031 (written in design). Add `llm` to the
   commit-scope list in `AGENTS.md`.
9. **Docs**, once the shape changes: `docs/tech_stack.md`,
   `docs/architecture.md`, `docs/cli.md` where the backend is named, and the
   commented keys in `openkos.yaml.template`.

### Out of Scope

- Re-benchmarking any eval on the new backend, and backend selection in the
  eval harnesses (`evals/*.py` keep constructing `OllamaClient` directly).
- Streaming responses.
- `response_format`, JSON mode or grammar-constrained output. Structured
  replies keep going through the fail-closed extraction in
  `llm/parsing.py`, for both backends.
- Suppressing reasoning output. Servers expose reasoning inconsistently
  (inline `<think>` tags, a `reasoning_content` field, or nothing). This is
  a documented known limitation of the new backend.
- A remote or cloud default. `ollama` on loopback stays the default.
- An `init --backend` flag or a backend picker in `init` (see open
  decision O1).
- Changing `OllamaClient`'s embedding output (no normalization added there;
  its bytes stay identical).

## Decisions

Decisions 1–8 are owner decisions (final). Decisions 9–12 are this
proposal's resolutions of details the owner decisions leave open.

| # | Decision | Chosen | Rejected | Why |
| --- | --- | --- | --- | --- |
| 1 | Config shape (owner) | `backend: ollama \| openai-compatible` (default `ollama`), one shared `base_url` for both backends, optional `embedding_base_url` | A `base_url` only for the new backend; per-backend key namespaces | A declared, git-diffable endpoint is more discoverable than the ambient `OLLAMA_HOST`, and it removes the asymmetry where only one backend has one. `OLLAMA_HOST` keeps working (decision 9). |
| 2 | API key (owner) | Optional, environment variable only; never an `openkos.yaml` key; never printed by `doctor` or `init`; its absence never gates startup | A YAML key; a mandatory key | `init` autocommits `openkos.yaml`, so a key written there would be committed to the user's repository. AGENTS.md forbids mandatory keys. |
| 3 | Embeddings (owner) | In scope; the new client L2-normalizes every returned vector; `EMBED_DIM` validation kept | Chat only; trust the server to normalize | `graph/proximity.py` converts cosine to Euclidean distance assuming unit vectors. That property is pinned only for Ollama with `bge-m3`. Normalizing a normalized vector is a no-op, so the step is cheap and safe. |
| 4 | Construction seam (owner) | One resolver in `application/backends.py` (`chat_client` dispatch plus a new `embed_client()`) used by every chat and embed site | Teach the conftest fixture to patch two class names | One patch point means no test that selects the new backend can reach the network, and a future third backend changes one function. |
| 5 | Commit scope (owner) | Add `llm` to AGENTS.md's scope list | Use `config` or `cli` for client work | The client module is neither. The list is documented as growing when code lands. |
| 6 | ADR (owner) | ADR-0031, written in design | No ADR | A second backend family, the neutral error naming, the env-only secret and the backend-qualified embedding tag are hard to reverse once users configure them. |
| 7 | Error names (owner) | `OpenAICompatibleError`, `...Unavailable`, `...ModelNotFound`, `...GenerationCapped`, `...EmbeddingDimensionMismatch`, mirroring Ollama's classes under the same bases | A new neutral hierarchy that renames Ollama's classes | Every existing `except BackendError`/`BackendUnavailable` site keeps working unchanged; only the few sites that catch a concrete Ollama class for remediation text need a second branch. |
| 8 | Embedding tag (owner) | The tag identifies the embedding backend kind; an absent backend part reads as `ollama` | Tag by model name only; tag by full `base_url` | A different server can return different vectors for the same model name. Reading the legacy shape as `ollama` means existing Ollama users see no re-embed. See decision 12 for server identity. |
| 9 | `base_url` vs `OLLAMA_HOST` precedence | Design picks, under two hard constraints: a user who sets only `OLLAMA_HOST` sees unchanged behavior, and `evals/run_self_tests.py`'s poisoned-`OLLAMA_HOST` guarantee stays intact. Recommended: explicit constructor argument > `OLLAMA_HOST` > `base_url` > default, with `doctor` showing the effective endpoint and where it came from | `base_url` silently overriding an exported `OLLAMA_HOST` | Environment-over-file is the conventional order and keeps the eval poisoning effective for anything that resolves through config. Showing the source in `doctor` makes the override visible instead of surprising. |
| 10 | Where the API key is read | In the application-layer resolver, passed to the client as an argument | Read inside `llm/openai_compatible.py` | The `llm-client` spec requires the `llm` package to stay config-free; the client stays testable with an explicit argument. |
| 11 | When `openai-compatible` becomes selectable | Only in the last functional slice, after remediation wording lands | Accept the value as soon as config parsing lands | On the stacked-to-main path, main must never contain a state where the value is accepted but silently ignored, or where the new errors reach a handler that only knows Ollama's classes. Until the enabling slice, `read_config` rejects the value with a clear message. |
| 12 | Server identity in the tag | Backend kind only; not the host or port | Include `base_url` | Including the URL would force a re-embed on every port change and write host details into `vectors.db`. Swapping servers within one backend kind while keeping the model name is a residual risk, documented, with `reindex --force` as the remedy (see open decision O2). |

## Capabilities

### New Capabilities

- `openai-compatible-client`: the new client's contract — chat request and
  response mapping, advisory `context_window`, generation-capped and usage
  handling, embeddings with index ordering, `EMBED_DIM` validation and L2
  normalization, `/v1/models` listing, locality, the error hierarchy, the
  optional bearer key, and testability without a live server.
- `backend-selection`: the `backend`, `base_url` and `embedding_base_url`
  keys, endpoint precedence including `OLLAMA_HOST`, the environment-only
  API key and its non-disclosure, and the single resolver seam that every
  chat and embed construction goes through.

### Modified Capabilities

Checked against `openspec/specs/` for this proposal:

- `llm-client`: "Model And Base URL Are Configurable" gains the config-key
  source and its precedence with `OLLAMA_HOST`; the locality classifier's
  home moves to `llm/base.py` (spec phase confirms whether that is
  spec-visible or implementation-only).
- `mcp`: "Chat-Client Construction Moves To `application/backends.py`
  Behind An Injected Factory…" extends to backend dispatch and the new
  `embed_client()`.
- `reindex-command`: "Embedding-Model Tag Gate Forces Full Re-Embed On
  Mismatch", "`reindex()` Accepts An Explicit Model Tag Parameter" (the
  tag now carries the backend), and "Reindex Discloses The Real Re-Embed
  Trigger…" (a fourth, backend-changed statement; the legacy tag must not
  be reported as a change).
- `doctor-command`: "Failed Checks Print Actionable Remediation" and the
  model-installed checks gain `openai-compatible` wording and listing;
  the effective endpoint and its source are shown; the API key is never
  printed.
- `workspace-init`: "Static openkos.yaml Template" (the template documents
  the new keys as comments; `model:`/`embedding_model:` stay the only
  substitutions) and "Non-Fatal Post-Success Ollama Preflight" (backend
  awareness, if the preflight reads config).
- `query-command`, `llm-edge-production`, `entity-resolution-adjudication`,
  `curate-command`: the pinned `ollama serve` / `ollama pull` remediation
  and the `OLLAMA_HOST` remote-host advisory become conditional on the
  backend; Ollama wording stays byte-identical.
- `sensitivity-aware-llm`: probably no delta — "Embedding Is Gated As
  Egress" already resolves locality from the sending client. The spec phase
  adds a scenario only if the text does not already cover a separate
  `embedding_base_url` being classified on its own.
- `vector-store`: no delta expected; the meta accessors store an opaque
  string.

## Approach

- **Move before adding.** The classifier move is a pure relocation with a
  re-export, so the new module never imports its sibling concrete client.
- **Mirror `OllamaClient`, do not generalize it.** The new client copies the
  transport shape, the injected `urlopen`, the retry discipline for
  transient embed failures, and the error-ladder structure. A shared base
  class is not introduced; the Protocols are the contract.
- **Resolver, then migration.** `application/backends.py` takes the
  concrete classes as injected factories (it binds no concrete backend, per
  the `mcp` spec) and picks by `cfg.backend`. The CLI keeps its delegator
  names so existing patches keep working. The embed sites move to
  `embed_client()` in a separate, mechanical slice.
- **Tag shape preserves the legacy value.** For `ollama` the tag stays
  byte-identical to today (`<model>#chunk-v1`); for `openai-compatible` it
  carries the backend kind in a form that cannot be confused with a model
  name containing `:`. Design fixes the exact syntax and the parser used by
  the disclosure and by `application/revisions.py`.
- **Byte-identity for the default path.** With no new keys set, requests,
  outputs, remediation text, goldens and the embedding tag are unchanged.
- The core stays synchronous. No new runtime dependencies.

### Slice plan (auto-chain, stacked to main, dependency order)

Forecasts are the exploration's estimates scaled by the observed ~1.95x
forecast-to-actual ratio, counting authored additions plus deletions and
excluding delta specs. Several exploration slices exceed 400 lines once
scaled, so they are split here.

| # | Slice | Contents | Scaled forecast |
| --- | --- | --- | --- |
| 1 | Locality move + scope | `classify_backend_host` and helpers to `llm/base.py`, re-exported from `ollama.py`; add `llm` to AGENTS.md | ~300–400 |
| 2 | Config surface | `backend` (only `ollama` accepted yet), shared `base_url` wired for Ollama with the decision-9 precedence, `embedding_base_url`, env-only API key resolution, config tests | ~300–450 |
| 3 | Client: chat core | error hierarchy, transport, `/v1/chat/completions` request and response mapping, error ladder, fake-`urlopen` tests | ~350–450 |
| 4 | Client: chat bounds | `max_tokens`, `finish_reason`, `usage` counters and generation-capped branching, advisory `context_window`, `temperature`/`seed` | ~250–400 |
| 5 | Client: embeddings | `/v1/embeddings`, `index` ordering, `EMBED_DIM` validation, L2 normalization, transient-failure retry | ~300–400 |
| 6 | Client: diagnostics | `/v1/models`, `locality`, cross-backend exemption test | ~200–300 |
| 7 | Offline test double | stub for the new client, network-guard derivation for it | ~200–300 |
| 8 | Resolver seam | `chat_client` dispatch, `embed_client()`, conftest patched at the seam | ~350–450 |
| 9 | Embed-site migration | the seven direct embed constructions in `cli/main.py`, `mcp/server.py`, `cli/curate.py` moved to `embed_client()` | ~250–400 |
| 10 | Backend-aware tag | tag construction and parsing, legacy-as-`ollama`, fourth disclosure statement, `application/revisions.py` | ~250–400 |
| 11 | Wording: doctor + init | backend-conditional remediation, endpoint-and-source line, key never printed, init preflight | ~300–450 |
| 12 | Wording: CLI + curate | concrete-class handlers and the remote-host advisories in `cli/main.py` and `cli/curate.py` (split by module if over budget) | ~350–450 |
| 13 | Enable | `read_config` accepts `openai-compatible`; template comments; end-to-end tests through the offline double | ~150–300 |
| 14 | Docs | shape-level updates only; known limitations (reasoning output, advisory context window) | ~100–250 |

Total ~3,650–5,400. Slices 3–7 depend on 1 and are otherwise ordered for
review, not by hard dependency. Slice 8 depends on 2 and 7. Slices 9 and 10
depend on 8. Slices 11 and 12 depend on 8. Slice 13 depends on 9–12.
ADR-0031 lands with the design commit or slice 1. Delta specs land with the
slice whose behavior they describe.

## Affected Areas

| Area | Impact | Description |
| --- | --- | --- |
| `src/openkos/llm/base.py`, `src/openkos/llm/ollama.py` | Modified | classifier moved and re-exported |
| `src/openkos/llm/openai_compatible.py` | New | the second client |
| `src/openkos/config.py`, `src/openkos/templates/openkos.yaml.template` | Modified | new keys, validation, commented template entries |
| `src/openkos/application/backends.py` | Modified | backend dispatch, `embed_client()`, API key read |
| `src/openkos/application/doctor.py`, `src/openkos/application/revisions.py` | Modified | wording, endpoint source; backend-aware tag compare |
| `src/openkos/state/reindex.py` | Modified | `embedding_tag` gains the backend; legacy parsing |
| `src/openkos/cli/main.py`, `src/openkos/cli/curate.py`, `src/openkos/mcp/server.py` | Modified | construction through the resolver; conditional wording |
| `tests/unit/conftest.py`, `tests/unit/test_network_guard.py`, `tests/unit/llm/` | Modified/New | single patch point, guard coverage, client tests |
| `AGENTS.md`, `docs/adr/0031-*.md`, `docs/adr/README.md`, `docs/*.md` | Modified/New | scope list, ADR-0031, shape docs |

## Principles Impact

- **Local-first & private:** the default stays Ollama on loopback. The new
  backend needs no account; the key is optional and never stored in the
  workspace. No new egress class: every send still goes through the
  existing sensitivity gates.
- **Sensitivity across boundaries:** locality is still decided only by the
  literal-loopback classifier, fail-closed, no DNS, per client (chat and
  embedding endpoints separately).
- **Reconstructible:** a backend switch invalidates the vector cache
  through the tag gate; vectors are rebuilt from canonical files.
- **LLM calls behind `LLMBackend`; no pydantic/instructor; sync core:**
  unchanged.
- **Adopt OKF, immutable `raw/`, provenance:** untouched.

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| A backend switch with the same model name reuses incompatible vectors | High without mitigation | backend-qualified tag (slice 10) lands before the backend is selectable (slice 13) |
| Existing Ollama stores re-embed after upgrade | Med | `ollama` tag bytes unchanged; legacy tag read as `ollama`; a test pins no re-embed |
| Switching servers within one backend kind, same model name | Low | documented residual risk; `reindex --force` remedy; open decision O2 |
| Unnormalized embeddings skew graph proximity | Med | client-side L2 normalization with a test on a non-unit vector |
| Reasoning models emit `<think>` or prose that defeats JSON extraction | Med | extraction already fails closed; documented known limitation |
| Server context smaller than `context_window` silently truncates | Med | `context_window` documented as advisory for this backend; `doctor`/docs say to match the server's `-c` / `--max-model-len` |
| API key leaks through output, git, or plain HTTP to a remote host | Low, high impact | env-only; never printed; design decides whether a key sent over `http://` to a non-loopback host warns |
| `base_url` vs `OLLAMA_HOST` precedence surprises a user | Med | decision-9 constraints; `doctor` shows the effective endpoint and its source |
| A new-client test reaches the network | Low | single resolver patch point; source-derived network-guard check |
| An intermediate main state accepts the new value but mishandles it | Med | decision 11: the value is rejected until slice 13 |
| Ollama remediation goldens drift | Low | Ollama wording byte-identical; new wording only on the new branch |

## Rollback Plan

Revert slices in reverse order; each is green on its own. Reverting slice
13 alone makes `openai-compatible` unselectable again, and `read_config`
rejects it with a clear message; everything before it is inert for the
default path. If slice 10 is reverted after a user embedded with the new
backend, the old code sees a tag it does not produce and forces a full
re-embed on the next `reindex`: self-healing, costs embed calls, loses
nothing. `openkos.yaml` files that gained `backend`/`base_url` keys stay
readable only while slice 2 is present; reverting slice 2 needs those keys
removed by hand, and the design decides whether older readers ignore or
reject unknown keys. Bundles are never touched by this change.

## Dependencies

- #995 (neutral Protocols and error bases in `llm/base.py`), already merged.
- No new runtime dependencies.

## Follow-up issues to open

- **F1:** Eval harness backend selection and a re-benchmark on at least one
  OpenAI-compatible server, including the `OLLAMA_HOST`-specific poisoning
  in `evals/run_self_tests.py`, which does not cover a config-driven
  endpoint.
- **F2:** Optional `response_format` / grammar-constrained output where a
  server supports it.
- **F3:** Reasoning-output handling for servers that expose it.
- **F4:** `init --backend` and a backend-aware model picker (if O1 is
  deferred).

## Success Criteria

- [ ] With no new keys set, chat requests, embed requests, the embedding
      tag, remediation text and goldens are byte-identical to before.
- [ ] A user who sets only `OLLAMA_HOST` gets the same endpoint as before;
      the eval self-test sweep stays model-free.
- [ ] With `backend: openai-compatible` and a loopback `base_url`, chat and
      embed calls target `/v1/chat/completions` and `/v1/embeddings`, and
      the client reports local.
- [ ] A non-loopback or unparseable `base_url` classifies non-local, and
      confidential material is withheld exactly as for Ollama.
- [ ] Every returned embedding row has unit L2 norm and `EMBED_DIM` length,
      or the call fails with the dimension-mismatch error.
- [ ] Switching the embedding backend forces a full re-embed and the
      disclosure names a backend change; an existing Ollama store does not
      re-embed after upgrade.
- [ ] The API key is never written to `openkos.yaml` and never appears in
      `doctor` or `init` output; its absence never blocks startup.
- [ ] No unit test can construct a network-reaching client through the
      resolver; the network-guard check covers the new client.
- [ ] `uv run ruff check .`, `uv run ruff format --check .`,
      `uv run mypy .`, `uv run pytest --cov` (90% branch gate) and the eval
      self-test sweep are green.
- [ ] ADR-0031 exists, status Proposed, indexed.

## Open decisions for the orchestrator

Not settled by the owner decisions above. None blocks slices 1–12.

- **O1. Does `init` learn about the backend in this change?** Recommended:
  no. `init` keeps writing the Ollama default; a user switches by editing
  `openkos.yaml`, and the template documents the keys. Stake: a user of an
  OpenAI-compatible server sees `init`'s Ollama picker degrade gracefully
  (it already does when Ollama is unreachable) and then edits one file.
  Doing it now adds roughly one slice and a `workspace-init` delta.
- **O2. Is backend kind enough in the embedding tag?** Recommended: yes
  (decision 12). Stake: a user who moves from LM Studio to vLLM with the
  same model name keeps old vectors until `reindex --force`. Including the
  endpoint instead re-embeds on any port change.
- **O3. Precedence of `base_url` and `OLLAMA_HOST`.** Delegated to design
  under the decision-9 constraints; flagged here because it is
  user-visible.
