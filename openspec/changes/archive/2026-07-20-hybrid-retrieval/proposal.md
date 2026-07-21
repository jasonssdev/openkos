# Proposal: `hybrid-retrieval` (Slice 1) — Embedder seam + Ollama embeddings

## Intent

MVP-2 aims at hybrid (dense + FTS + graph) retrieval, but exploration surfaced a
blocker: openkos has NO persistent SQLite state — FTS5 and the graph are both
`:memory:`, rebuilt per run (see `sdd/hybrid-retrieval/explore`, id 1340; discovery
id 1342). Dense retrieval therefore needs persistence and a feasibility-risky
sqlite-vec extension load — deferred. This first slice de-risks the endpoint that
everything downstream depends on: **turning text into 1024-dim vectors via a local
Ollama embeddings model**, behind a hermetic seam. No retrieval behavior changes.

## Scope

### In Scope

- `Embedder` Protocol in `src/openkos/llm/base.py`, beside `LLMBackend`. Contract:
  `embed(texts: Sequence[str]) -> list[list[float]]`, output dimension 1024.
- Additive `embed()` on `OllamaClient` (`src/openkos/llm/ollama.py`) hitting
  `/api/embed`, reusing the existing `urllib` + `OllamaError`-family discipline
  (config-free, no new HTTP dep).
- New `embedding_model` config field (default `qwen3-embedding:0.6b`), distinct from
  chat `DEFAULT_MODEL="qwen3:8b"` (`config.py:20`).
- A `doctor` preflight check for the embedding model, reusing the existing
  `[PASS]`/`[FAIL]` + remediation pattern in `cli/main.py`.

### Out of Scope (non-goals — deferred slices)

- sqlite-vec, any vector store, persistence/cache DB, extension loading.
- Retrieval/fusion (RRF), the `answer()` path, graph traversal.
- The two-output rule; any new CLI query behavior.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `llm-client`: add the `Embedder` Protocol and `OllamaClient.embed()`
  (`/api/embed`, dim 1024, same typed-error vocabulary as `chat()`); lift
  "embeddings" from the spec's Non-Goals.
- `doctor-command`: add a check that the configured `embedding_model` is installed
  (N/A when Ollama is unreachable — no double-reporting one root cause).

## Approach

`embed()` clones `chat()`'s `urlopen` + `_map_http_error`/`_unavailable` plumbing:
POST `{"model": embedding_model, "input": [...]}` to `/api/embed`, parse
`embeddings`, validate each row is length-1024 floats else raise `OllamaError`.
The `Embedder` Protocol keeps tests hermetic — inject a fake `Embedder` returning
deterministic vectors, mirroring the fake `LLMBackend` already injected in
`answer()`. `config.read_config` gains `embedding_model` with the new default;
`doctor` reuses `list_models()`/`model_tag_matches()`.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/llm/base.py` | Modified | `Embedder` Protocol beside `LLMBackend` |
| `src/openkos/llm/ollama.py` | Modified | `embed()` via `/api/embed`, dim-1024 validation |
| `src/openkos/config.py` | Modified | `embedding_model` field + default |
| `src/openkos/cli/main.py` | Modified | `doctor` embedding-model check |
| `tests/unit/llm/`, `tests/unit/cli/` | New/Modified | fake `Embedder`, `embed()` + doctor tests |
| `docs/cli.md`, `docs/roadmap.md` | Modified | note embedding preflight |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| `/api/embed` response shape variance (`embeddings` vs `embedding`) | Med | Parse defensively; validate list-of-1024-floats or raise `OllamaError` |
| New network dependency (embedding model) | Med | Hermetic fake `Embedder`; reuse `OllamaUnavailable` + doctor |
| Dimension drift (model not 1024-dim) | Low | Assert dim 1024 per row; fail loud with typed error |
| Config-import leak into `llm/` leaf | Low | Caller passes the tag; AST leaf-discipline test stays green |

## Rollback Plan

Additive and local: remove `Embedder` from `base.py`, `embed()` from `ollama.py`,
the `embedding_model` field from `config.py`, and the `doctor` check + new tests.
No persisted state, no migration, no dependency change, no touch to retrieval.

## Dependencies

- None new. Reuses `OllamaClient`/`OllamaError`, `config.read_config`,
  `list_models()`/`model_tag_matches()`. `qwen3-embedding:0.6b` is an official
  Ollama embeddings model (dim 1024).

## Review Workload Forecast

Estimate (authored additions + deletions): prod ~70–90, tests ~150–200, docs
~20–30 → **~250–320 lines total**. Comfortably under the 800-line budget.
**Single PR, no chaining.** `400-line budget risk: Low`.

## Success Criteria

- [ ] `Embedder` Protocol exists; a fake satisfies it structurally in tests.
- [ ] `OllamaClient.embed(texts)` returns `len(texts)` rows, each 1024 floats.
- [ ] Malformed/unavailable/wrong-dim responses raise the correct typed error.
- [ ] `embedding_model` defaults to `qwen3-embedding:0.6b`, distinct from chat model.
- [ ] `doctor` reports the embedding model (installed / not-installed / N/A).
- [ ] `llm/` no-config-import test stays green; `uv run pytest`, ruff, mypy strict, 90% branch cov green.
