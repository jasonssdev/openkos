---
type: TechStack
title: OpenKOS Tech Stack
description: Technology choices for OpenKOS — a durable canonical core plus swappable derived indexes, all open-source, free, and local-first.
tags:
  - openkos
  - architecture
  - technology
  - local-first
  - open-knowledge-format
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-15T05:00:00Z
sensitivity: public
---

# Tech Stack

Every choice here honors four constraints: it runs **locally**, it is **open-source and free** (no paid services, no proprietary formats), it stays **lightweight**, and it must **hold a large knowledge base over many years**. All licenses noted below are permissive (MIT, Apache-2.0, BSD) or public domain.

> **On model licences in this document.** Model licences, release names, and version numbers are **volatile facts** — the fastest-moving in the project, and a vendor can relicense a family in either direction between one release and the next. A licence claim written from stale knowledge is a claim that quietly becomes false, and this document has been wrong that way before. So it stopped certifying: **OpenKOS names candidate models, it does not vouch for their licences.** Check the vendor's own terms page for the exact release you intend to pull, and trust the licence text over this document or the model's reputation.

## The core principle: durable core, swappable indexes

The single most important design decision for longevity is not which database we pick — it is that we split the system into two layers:

- **The canonical layer is durable.** Your knowledge lives as plain files plus SQLite and git. These are among the most long-lived, portable formats in existence (SQLite is a recommended preservation format of the U.S. Library of Congress; markdown is just text). This layer is meant to outlive any tool.
- **The derived layer is rebuildable.** Vector indexes and graph projections are caches. They are always reconstructible from the canonical layer, so the engines that power them sit behind interfaces and can be swapped without migrating or losing data.

The practical consequence: **choosing a vector or graph engine is not a lock-in decision.** If a dependency is abandoned (as KùzuDB was in October 2025), replacing it is a re-index, not a data loss. No single bet is fatal.

---

## Canonical layer (durable — never a lock-in)

- **Markdown + YAML frontmatter** — the OKF bundle; the canonical form of all knowledge
- **Immutable source files** — original content, never rewritten
- **Git** — version history and recoverable revisions (via subprocess or GitPython, BSD)
- **SQLite** — operational state: provenance, object registry, configuration, change tracking (public domain; bundled with Python's standard library)
- **SQLite FTS5** — lexical (keyword) retrieval; scales to millions of rows comfortably

This layer alone is the entire storage story for MVP 1.

## Derived layer (rebuildable — behind interfaces, swappable)

Introduced in MVP 2. Each engine has a simple default and a documented scale path, and every derived store can be rebuilt from the canonical layer.

- **Embeddings** — served by **Ollama**, the same runtime already used for the generative model (one server, one dependency, no separate embedding stack). Default to a modern, multilingual, permissively-licensed model — **`bge-m3`** (1024-dim, 8192-token context). Multilingual matters because a personal knowledge base is often not English-only. The model is **pinned** (embeddings are not comparable across models) and recorded in the derived state — never in the OKF bundle — and because the canonical text is always available, the whole index can be **re-embedded** at any time. The default is settled in two ordered stages: **reliability first** — a candidate that crashes the Ollama runner on a realistic corpus is excluded before any quality comparison — then **quality** among the survivors, by the same spike discipline as the generative model. `qwen3-embedding:0.6b` (Apache-2.0, Matryoshka dimensions) was the original default for its truncatable dimensions, but was **discarded for reliability**: it raises non-deterministic EOF crashes with no stable token threshold (see [ADR-0006](adr/0006-default-embedding-model.md)), so it is no longer a live default option regardless of its Matryoshka advantage.
- **Vector store** (behind a `VectorStore` interface):
  - *Default:* **sqlite-vec** (Apache-2.0) — stays inside the same SQLite file, zero extra infrastructure. Exact brute-force search; excellent up to roughly one million vectors, and kept viable well beyond that by **filter-first retrieval** (FTS5 and the graph narrow candidates first, so we rank a small filtered set rather than the whole corpus).
  - *Scale path:* **LanceDB** (Apache-2.0) — embedded, on-disk IVF-PQ indexing that handles datasets larger than RAM. The choice when a heavy, multi-year corpus grows into the millions of vectors.
- **Graph store** (behind a `GraphStore` interface):
  - *Default:* **SQLite node-edge tables + recursive SQL (CTEs)** for traversal (neighbors, paths). No new dependency, and it cannot be abandoned.
  - *In-memory analysis:* **NetworkX** (BSD) — pure Python, huge community, easy to reason about and to get help with. Used on subgraphs for analytics.
  - *Scale path (rarely needed at single-user scale):* a dedicated embedded graph engine, adopted only if the graph ever outgrows SQLite. Any such engine must stay local-first and permissively licensed, and would sit behind the same `GraphStore` interface — we would add one reluctantly.

## Stack by MVP

**MVP 1 — The Compiler** (canonical layer only; no vectors)

| Role | Technology | License |
| --- | --- | --- |
| Language / runtime | Python 3.12+ | PSF |
| CLI | Typer | MIT |
| Schemas / validation | Pydantic v2 | MIT |
| Knowledge format | Markdown + YAML frontmatter (OKF) | — |
| Frontmatter (round-trip) | python-frontmatter + ruamel.yaml | MIT |
| Markdown parsing | markdown-it-py (CommonMark) | MIT |
| State + lexical search | SQLite + FTS5 | Public domain |
| History / versioning | Git (subprocess or GitPython) | — / BSD |
| Local LLM (compile / summarize) | Ollama serving an open-weight model | MIT |

**MVP 2 — The Graph and Memory** (adds the derived layer, all swappable)

| Role | Default (simple) | Scale path (millions+) | License |
| --- | --- | --- | --- |
| Embeddings | Ollama serving `bge-m3` (multilingual, 1024-dim, 8192-tok ctx; reliability-first default) | larger model (e.g. `qwen3-embedding:4b`) if measured reliable | check the vendor's terms |
| Vector store (`VectorStore`) | sqlite-vec (same `.db` file) | LanceDB (on-disk IVF-PQ) | Apache-2.0 |
| Graph store (`GraphStore`) | SQLite node-edge + recursive SQL | dedicated engine only if ever needed | — |
| In-memory graph analysis | NetworkX (on subgraphs) | NetworkX | BSD |
| Local LLM (extraction) | Ollama (Qwen3 / Mistral Small) | larger model | check the vendor's terms |

The runtime and interoperability layer (FastAPI local API, MCP server, OKF import/export) arrives with MVP 3.

---

## Core framework

- **Python 3.12+**
- **Typer** for the command-line interface (Click, MIT, is the mature fallback if we ever want fewer abstractions)
- **Pydantic v2** for schemas and validation
- **FastAPI** for the local API layer (introduced in MVP 3)
- **Markdown + YAML frontmatter** using the OKF v0.1 field set (`type`, `title`, `description`, `resource`, `tags`, `timestamp`)

## Local AI

Two kinds of model are used: a **generative model** (to compile, extract, and answer) and, from MVP 2, an **embedding model** (for vector search). The model is never hard-coded — it is set in `openkos.yaml`, behind an `LLMBackend` interface, so the user chooses.

**Runtime.** [Ollama](https://ollama.com) is the recommended local runtime (one-click install, cross-platform, manages model downloads); llama.cpp and LM Studio are alternatives. They all speak the same **OpenAI-compatible HTTP API** — this names the *request/response shape* that became a de-facto standard (like "S3-compatible" storage), **not** OpenAI the company or its cloud. No data leaves the machine; speaking one common dialect simply lets OpenKOS work with any conforming local runtime, which is the opposite of lock-in. Ollama and llama.cpp are both MIT-licensed.

**Recommended generative models, by hardware:**

- **~8 GB RAM** — 3–4B: summarization and light extraction with tight prompts. This is the honest floor, not a comfortable target.
- **~16 GB / Apple Silicon** — 7–8B: the sweet spot for extraction quality and speed, and what a normal MacBook should aim at.
- **32 GB+ / GPU** — 14B–24B: stronger reasoning and JSON / function-calling.

**Qwen3** and **Mistral Small** are the candidate families at each tier — named because they are widely available through Ollama, come in the sizes above, and are strong at instruction-following and structured output; the model spike also evaluated a **Gemma** tag (`gemma4:e4b`). Check the licence of the exact release you pull; see the licensing note below for what to look for. A smaller model means weaker extraction and more reliance on review and the lint.

**The default was settled by a spike, not by this document.** What the compiler actually needs is schema-valid JSON out of a 7–8B model with few retries, and that was measured, not argued: the [`good-life-demo`](../examples/good-life-demo/) fixture was the target shape, and the same ingest run against each candidate settled `qwen3:8b` as the default with data (recorded in [ADR-0001](adr/0001-default-extraction-model.md); harness in [`evals/model_spike/`](../evals/model_spike/)). It remains a config value — swapping it is one line in `openkos.yaml`.

That is the deeper point, and it is deliberate: **OpenKOS does not bless a model.** The model sits behind `LLMBackend` and is a config value, so the project has no model policy — it has a default, first-class alternatives, and a documented reason. If your organisation restricts models by origin, or you simply prefer another vendor, swap it; the engine does not care and nothing in the bundle changes.

**Embeddings (MVP 2)** are served through the **same Ollama runtime** as the generative model — there is no separate embedding stack and no heavy in-process dependency (e.g. no PyTorch). The embedding model sits behind an `Embedder` interface and is a config value in `openkos.yaml`, exactly like the generative model: a default with first-class alternatives, never blessed.

**Recommended default** *(as of 2026-07-22):* `bge-m3` — multilingual (100+ languages), 1024-dim, 8192-token context, measured reliable (0% failure from 8k to 100k characters, truncating past its context rather than crashing). **The default is chosen reliability-first, then quality.** Reliability is a hard, prior filter: a model that crashes the Ollama runner on a realistic corpus is excluded before any retrieval-quality comparison; the quality spike then decides only among candidates that are already reliable. `qwen3-embedding:0.6b` was the original default for its **Matryoshka** truncatable dimensions, but is **discarded for reliability** — it raises non-deterministic EOF crashes with no stable token threshold ([ADR-0006](adr/0006-default-embedding-model.md)) — and is no longer a live default option. First-class alternatives that clear the reliability bar: **multilingual-e5** and larger BGE / qwen-embedding tags if a bigger model is warranted. Because re-embedding is free, being wrong costs a re-index, not data.

**If no model is installed,** `openkos init` guides rather than failing silently: on a TTY it prompts for a model tag (default `qwen3:8b`), resolving the tag by precedence `--model` flag > prompt > default — it does not detect hardware or auto-pull a model. A missing model is then diagnosed by `openkos doctor`, which suggests the `ollama pull <model>` remediation. For non-technical users later, the path is an embedded runtime (no separate install), hardware-aware auto-download with a progress UI, and an optional, explicit cloud fallback — never for `confidential` content — for machines that cannot run a capable local model. There is an honest hardware floor: a weak machine runs a weaker model and leans more on review and lint.

- **Model Context Protocol (MCP)** for exposing the bundle to external agents (MVP 3).
- **Model licensing note.** Not every "open" model is OSI open source, and the line moves — which is why this document tells you what to check rather than which weights to trust. Read the licence of the release you are about to pull, on the vendor's own terms page.

  What to look for. Vendor-specific licences — the **Gemma Terms of Use** and the **Llama Community License** are the two you will meet most often — are not OSI open source. They typically carry a prohibited-use policy you must pass downstream to your own users, and reserve the vendor's right to terminate. The Gemma Terms go further and reserve the right to *"restrict (remotely or otherwise) usage"*.

  That last kind of clause is why this is a philosophy question and not just a legal one. OpenKOS promises a knowledge base that runs offline and that nobody can take away from you. A model whose licence reserves remote restriction contradicts that promise, however good the weights are. **Prefer an OSI-approved permissive licence — Apache-2.0, MIT — for the defaults**; anything else is the user's informed choice, never ours on their behalf. The same test applies to the embedding model you pin.

## Freshness and quality gates

- Freshness lint enforcing the timeless / snapshot / pointer discipline
- Provenance validation — every derived object resolves to a source
- Contradiction and staleness detection over the bundle

## Testing

- Pytest with unit, integration, and end-to-end pipeline tests
- Schema and OKF-conformance validation tests
- Retrieval and ranking evaluations with reproducible local benchmarks
- Regression datasets for knowledge extraction
- Security tests for prompt injection and permission boundaries

## Tooling

- `uv` for dependency and environment management
- Ruff for linting and formatting
- MyPy for static type checking
- Git and GitHub, with GitHub Actions for CI
- Pre-commit hooks
- Docker for optional development environments

## What we deliberately avoid (and why)

- **Server-based databases** (pgvector/Postgres, Qdrant, Milvus, Weaviate) — open-source and capable, but they are services to run, which breaks local-first, lightweight, zero-infrastructure operation.
- **GPL-licensed libraries** (for example `python-igraph`, GPL-2.0) — to avoid copyleft friction with the project's permissive license. We prefer BSD/MIT/Apache equivalents.
- **Abandoned or single-owner-critical dependencies** — the KùzuDB archival is the cautionary example. We favor boring, widely adopted, hard-to-kill technology (SQLite, NetworkX) for anything in the durable core.
- **Engine sprawl** — every additional storage engine is weight and long-term maintenance. We keep the number of engines small on purpose.

## What we are not requiring

OpenKOS never requires:

- Proprietary cloud services or mandatory online accounts
- Closed or vendor-specific data formats
- A single fixed LLM provider or runtime
- Cloud-only vector or graph databases
- Mandatory external network access
- Any specific end-user application (Obsidian, Logseq, Notion, and others all work, because the output is plain OKF files — but none is required)
