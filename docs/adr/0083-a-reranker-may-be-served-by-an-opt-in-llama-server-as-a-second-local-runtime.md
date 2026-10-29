---
type: Decision
title: "ADR-0083: A reranker may be served by an opt-in llama-server as a second local runtime"
description: Proposes that a cross-encoder reranker, if the pre-registered measurement of #1651 licenses reranking at all, is served by an explicitly opt-in llama-server (llama.cpp) behind its own Reranker seam, while Ollama stays the default and only required runtime.
status: Proposed
date: 2026-10-28
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-28T00:00:00Z
sensitivity: public
---

# ADR-0083: A reranker may be served by an opt-in llama-server as a second local runtime

- **Status:** Proposed
- **Date:** 2026-10-28

## Context

Fusion of the lexical and dense ranks can leave a right document just below the display cut, and a reranker over a wider candidate pool is the standard remedy (#1651, part of #1652). Ollama is the default and so far the only required local runtime ([ADR-0057](0057-the-first-model-runtime-is-a-user-installed-ollama-behind-a-guided-setup.md)), and it has no rerank endpoint: `POST /api/rerank` does not exist and the feature request was closed without an implementation. A purpose-trained cross-encoder (for example `bge-reranker-v2-m3`, Apache-2.0, multilingual, 0.6B) therefore cannot be served by Ollama.

`llama-server` from llama.cpp (MIT) serves one when started with `--reranking`, on `/v1/rerank`. That endpoint is the Jina shape (`{"query", "documents"}` in, `results` with an `index` and a `relevance_score` out), not the OpenAI-compatible shape that `llm/openai_compatible.py` speaks ([ADR-0031](0031-openai-compatible-backend.md)), and a bidirectional reranker must fit the whole prompt of the query plus one document in the server's `--ubatch-size`. Using it means running a second local server.

The cheapest alternative, a chat model scoring each candidate pointwise through the existing `LLMBackend.chat`, needs no new runtime but costs one call per candidate, and [`PREREGISTRATION-1651.md`](../../evals/retrieval_gold/PREREGISTRATION-1651.md) forecasts about 75 seconds per query for it. Whether any reranking pays is an empirical question that [`PREREGISTRATION-1651-cross-encoder.md`](../../evals/retrieval_gold/PREREGISTRATION-1651-cross-encoder.md) registers before a result exists. The owner decided on 2026-10-28 that an optional, explicitly opt-in `llama-server` is acceptable as a second runtime for this purpose, and that adopting reranking itself still depends on that measurement.

## Decision

We allow a reranker to be served by an optional, opt-in `llama-server` as a second local runtime. Ollama stays the default and the only required runtime: a workspace that never configures a reranker sees no change, installs nothing new and starts no second server. Adopting reranking at all is gated on the #1651 measurement; this ADR does not license it and ships no code.

If the measurement licenses it, the reranker is reached through its own `Reranker` protocol (`rerank(query, documents) -> list[float]`, scores in input order), resolved in `application/backends.py`, with its own URL key in `openkos.yaml`. It is not an `openai-compatible` client and does not extend `LLMBackend`. A failed or unreachable reranker falls back to the fused order, so retrieval never depends on it. The engine does not start, install or manage the server.

## Consequences

- Ollama remains the single required runtime, so the guided setup, the packaging and the local-first story are unchanged for everyone who does not opt in.
- A new optional URL key and a new `Reranker` seam are added only if the measurement licenses reranking; the seam is internal, like `GraphStore` and `LLMBackend`, and publishes no plugin API.
- A user who opts in operates two local servers, downloads a reranker GGUF separately, and must size `--ubatch-size` to the document cut; the documents a request cannot fit are a reported failure, not a silent truncation.
- A latency cost is paid on every `query` that reranks. The measurement fixes a bar for it before the run.
- Sensitivity is unaffected in kind: the server is local and nothing leaves the device, but the candidates sent to it are subject to the same disclosure rules as any text sent to a local model.
- This ADR is superseded or amended if the measurement does not license reranking; it records the permission to try, not a commitment to ship.

## Alternatives considered

- **Ollama only, a chat model scoring pointwise.** No new runtime, but one call per candidate: tens of seconds per query on the shipped model, and a chat score is not a calibrated relevance score. It is measured first as its own arm and is not excluded by this decision.
- **An in-process cross-encoder** (`sentence-transformers`, PyTorch). Rejected by [`tech_stack.md`](../tech_stack.md): a heavy in-process dependency in a core that is deliberately light. `fastembed` (ONNX) avoids PyTorch, but wheel support on Python 3.14 is unverified.
- **A heavier serving stack** (Hugging Face Text Embeddings Inference, vLLM). Both serve rerankers, but they are larger, GPU-oriented servers that are a worse fit for a one-click local install than a single `llama.cpp` binary.
- **Extending the `openai-compatible` backend.** The rerank endpoint is not OpenAI-compatible, and a chat or embedding client that also scores documents would blur two seams that today have one job each.
