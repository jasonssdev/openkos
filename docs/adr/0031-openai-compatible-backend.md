---
type: Decision
title: "ADR-0031: A second, OpenAI-compatible local backend beside the Ollama default"
description: OpenKOS gains an OpenAI-compatible chat and embedding client selected by a backend key, with one shared endpoint key, an optional environment-only API key, client-side L2 normalization of embeddings, neutral error bases every consumer catches, and the backend kind recorded in the embedding tag; Ollama on loopback stays the default and its bytes stay unchanged.
status: Proposed
date: 2026-09-29
tags:
  - openkos
  - adr
  - llm
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-09-29T00:00:00Z
sensitivity: public
---

# ADR-0031: A second, OpenAI-compatible local backend beside the Ollama default

- **Status:** Proposed
- **Date:** 2026-09-29

## Context

OpenKOS talks to exactly one model server: Ollama, through `OllamaClient`. Many people who run models locally use a server that speaks the OpenAI-compatible HTTP API instead: llama.cpp's `llama-server`, LM Studio, vLLM, LocalAI. They cannot use OpenKOS without also installing Ollama (#1057).

The neutral seams from #995 make a second backend possible without rewriting consumers: `llm/base.py` defines the `LLMBackend`, `Embedder` and `BackendDiagnostics` Protocols and the `BackendError`/`BackendUnavailable` bases, and chat construction already goes through `application/backends.py` behind an injected factory. Six forces shape the decision:

- **Local-first is a principle, not a default that can drift.** No account, no mandatory key, no cloud default. Locality must keep being decided by one literal-loopback rule, with no DNS, failing closed.
- **Secrets have never had a home in the workspace.** `init` autocommits `openkos.yaml`, so any secret written there lands in the user's git history.
- **Graph proximity assumes unit vectors.** `graph/proximity.py` converts cosine similarity to Euclidean distance as `sqrt(2 - 2·cos)`. That property is pinned only for Ollama with `bge-m3`; an arbitrary OpenAI-compatible server does not promise it.
- **Vectors from two servers are not interchangeable,** even under the same model name. The vector cache is gated by a stored embedding tag that today names only the model and the composition scheme.
- **Most consumers catch Ollama's concrete error classes.** About 55 `except`/`isinstance` sites across `resolution/`, `state/reindex.py`, `retrieval/answer.py`, `mcp/` and `cli/` name `OllamaError` and its subclasses. An error from a second client would escape them.
- **The default path must not move.** Existing workspaces must see byte-identical requests, output and embedding tags, and must not re-embed.

## Decision

We add `OpenAICompatibleClient` (`llm/openai_compatible.py`) as a second concrete backend, keep Ollama as the default, and select between them with a `backend: ollama | openai-compatible` key in `openkos.yaml`.

- **Mirror, do not generalize.** The new client copies `OllamaClient`'s stdlib transport and injected `urlopen`, and implements the existing Protocols structurally. It uses only the common OpenAI subset: `/v1/chat/completions` (non-streaming, `max_tokens`, top-level `temperature`/`seed`), `/v1/embeddings` and `/v1/models`. `context_window` is advisory for this backend: it is used for OpenKOS's own prompt budgeting and is never sent, because these servers fix their context size at start. Pure helpers, the locality classifier first, move to `llm/base.py` and are re-exported from `ollama.py`.
- **One shared endpoint key.** `base_url` (and an optional `embedding_base_url`) serves whichever backend is selected. For Ollama, `OLLAMA_HOST` keeps precedence over the file, so existing users and the eval harness's poisoned-`OLLAMA_HOST` guarantee are unchanged. For the new backend, `base_url` is required. `doctor` shows the effective endpoint and where it came from.
- **The API key lives only in the environment.** An optional `OPENKOS_OPENAI_API_KEY` is read by the application layer, sent only as a bearer header, never stored in `Config`, never printed, and never read from `openkos.yaml` (a key placed there is refused). `OPENAI_API_KEY` is deliberately not read, so a cloud credential is never forwarded to a local or third-party endpoint. Sending a key over plain HTTP to a non-loopback host produces a warning, not a refusal.
- **Normalize embeddings on the client.** The new client L2-normalizes every returned row after validating its `EMBED_DIM` length. `OllamaClient`'s output is unchanged.
- **Neutral error bases, caught everywhere.** `llm/base.py` gains `BackendModelNotFound`, `BackendGenerationCapped` and `BackendEmbeddingDimensionMismatch`. Both clients' classes subclass them, and every consumer catches the neutral names. Ollama's class names do not change.
- **The embedding tag records the backend kind.** For Ollama the tag stays `<model>#chunk-v1`; for another backend it gains `#backend=<kind>`. A tag with no backend part reads as `ollama`, so existing stores do not re-embed, and a backend switch forces one full re-embed through the existing gate. The server's URL is not recorded.

## Consequences

**Easier.**

- Users of llama.cpp, LM Studio, vLLM or LocalAI can run every verb without Ollama.
- A third backend is a new client plus one dispatch branch. Consumers already catch the neutral bases, and every construction goes through one resolver, which a source guard enforces.
- Locality and the confidential exemption stay one authority for both backends. Chat and embedding endpoints are classified separately.

**Harder, and accepted.**

- Two clients must keep matching error-ladder and thread-safety disciplines. Each has its own tests, including the AST guard that `chat` writes no instance state.
- Reasoning output is not suppressed on the new backend, since servers expose it inconsistently. Extraction already fails closed, so this costs recall, not correctness. It is documented as a known limitation.
- The advisory `context_window` can disagree with the server's real context size. The generation-capped message and the docs tell the user to match them.
- Switching between two servers of the same kind with the same model name keeps the old vectors. `openkos reindex --force` is the documented remedy. Recording the URL would re-embed on every port change and put host details in `vectors.db`.
- `llama-server` must be started with a batch size large enough for OpenKOS's embedding chunks, or embedding requests fail. `doctor` cannot detect this; it is documented.
- The eval harnesses keep constructing `OllamaClient` directly. Nothing in this ADR is measured on the new backend yet, and re-benchmarking is follow-up work.

## Alternatives considered

- **A shared HTTP base class for both clients.** Rejected: it couples two clients whose request shapes and error mappings differ, and puts `OllamaClient`'s bytes at risk for no behavior gain. The Protocols are the contract.
- **Chat only, embeddings stay on Ollama.** Rejected: a user with no Ollama would still need Ollama. Client-side normalization removes the unit-vector risk at negligible cost.
- **Trusting the server to normalize.** Rejected: nothing guarantees it, and a violation would silently distort graph proximity.
- **A `base_url` only for the new backend, or per-backend key namespaces.** Rejected: an asymmetric schema, and a declared endpoint is more discoverable than the ambient `OLLAMA_HOST` for both backends.
- **`base_url` overriding `OLLAMA_HOST`.** Rejected: it would silently move users who export `OLLAMA_HOST`, and it would weaken the eval harness's model-free guarantee.
- **An API key in `openkos.yaml`, or a fallback to `OPENAI_API_KEY`.** Rejected: the first is committed to git by `init`; the second forwards a cloud credential to an arbitrary endpoint.
- **A new neutral hierarchy that renames Ollama's classes.** Rejected: it breaks every external `except OllamaError`. Adding neutral bases, as #995 did for `BackendUnavailable`, keeps every name and widens what consumers can catch.
- **Catching both concrete classes at every site.** Rejected: it doubles about 55 handlers and must be repeated for every future backend.
- **Tagging vectors by model name only, or by full URL.** Rejected for the reasons in the last Decision bullet.
