---
type: Decision
title: "ADR-0057: The first model runtime is a user-installed Ollama behind a guided setup"
description: The desktop app detects a user-installed Ollama, sends the user to the official download when it is absent, and pulls models with progress through Ollama's own API.
status: Accepted
date: 2026-10-14
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-14T00:00:00Z
sensitivity: public
---

# ADR-0057: The first model runtime is a user-installed Ollama behind a guided setup

- **Status:** Accepted
- **Date:** 2026-10-14

## Context

The engine reaches models through `LLMBackend` and `Embedder`, and Ollama is the default backend. A person who has never used a terminal cannot run `ollama pull`. The roadmap offers three paths, each putting model download and GPU support on a different owner: guide the user to install Ollama, bundle a runtime, or embed a `llama.cpp` server behind the existing `openai-compatible` backend ([ADR-0031](0031-openai-compatible-backend.md)).

The product constraints are fixed: a 32 GB unified-memory floor ([ADR-0047](0047-the-contradiction-and-identity-judges-default-to-gemma4-26b-a4b-and-the-engine-runs-one-chat-model-at-a-time.md)), online to set up, then offline by default.

Evidence comes from a throwaway packaging spike run on the project's development machine (Apple Silicon, macOS 27.0), against Ollama 0.32.9.

- **Pull streams and resumes (S5).** `/api/pull` for `qwen2.5:0.5b` (397 MB) streamed NDJSON progress. The client was aborted at 128.9 MB and the re-pull started at 129.0 MB, so the download resumes rather than restarts.
- **Real services against a local Ollama.** The frozen engine answered queries through Ollama on both interpreters. On 3.14 the models were `qwen3:8b` for chat and `bge-m3` for embeddings; the 3.12 run's models are not recorded. Query latency was 2.3 to 8.4 s on Python 3.12 and 1.75 to 6.3 s in the app on 3.14, including model load. One request took 236.5 s on 3.14 because an unrelated client had saturated the same Ollama; the engine was not at fault, but it shows a shared Ollama is shared.
- Detection by `/api/version` was not exercised by the spike, which always had a running Ollama; it is the intended probe.

## Decision

The first model runtime is a **user-installed Ollama**, reached through a guided setup step:

1. The app probes `/api/version`. If Ollama answers, setup continues.
2. If not, the app sends the user to Ollama's official download, then polls `/api/version` until it answers.
3. The app pulls the models through `/api/pull`, showing the streamed progress, and resumes an interrupted pull.

The app is the one flow; Ollama is a separate, second install, presented as one step of it. Setup uses the network; after it the app works offline by default (roadmap). A cloud backend stays optional and off by default.

## Consequences

**What becomes easier:**

- No model runtime to ship, sign or update in the app: Ollama owns GPU support, model storage and its own updates.
- Nothing new in the engine: the existing backend and the resolver in `application/backends.py` are used unchanged.
- The pull's progress and resumption come from Ollama, not from code the project writes.

**What becomes harder, and what we accept:**

- **Two installs.** The user installs the app and Ollama. This is acceptable if the guided step presents them as one flow; the cost is a second download, a second first-run dialog and a second place for the user to look when something fails.
- **A shared Ollama is shared.** The user's other tools can saturate it, as the 236.5 s query showed, and ADR-0047's one-chat-model-at-a-time rule applies to a server the engine does not own.
- The app depends on Ollama's API staying stable for `/api/version` and `/api/pull`.
- **Not measured:** an absent-Ollama run of the guided step; the polling behaviour after install; the models the product will pull by default and their sizes; behaviour below the 32 GB floor; the user-visible time to first answer on a clean machine.

## Alternatives considered

- **Bundle the Ollama binary.** Ollama's MIT licence allows it, and it would make setup one install. Not adopted: the GPU-library licensing was not verified, and neither was coexistence with a system Ollama already listening on the default port or sharing the model store. It would also put Ollama's updates and signing on this project. Revisit once those are verified.
- **Embed a `llama.cpp` server behind the `openai-compatible` backend.** One install and a smaller dependency, but the project would then own model download, quantization-format choices and GPU support itself. Rejected for the first release; possible later because the backend seam already exists.
- **A cloud model as the default.** Violates local-first and offline-by-default; it remains optional and off by default (roadmap).
