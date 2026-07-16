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

- **Embeddings** — Sentence Transformers (Apache-2.0) or Ollama. Default to a small, high-quality model such as `bge-small-en-v1.5` (384d, MIT) or `nomic-embed-text` (768d, Apache-2.0). Smaller dimensions mean less storage and faster search at scale. The model is **pinned** (embeddings are not comparable across models), and because the canonical text is always available, the whole index can be **re-embedded** at any time.
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
| Language / runtime | Python 3.13+ | PSF |
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
| Embeddings | Sentence Transformers or Ollama; `bge-small` (384d) / `nomic-embed` (768d) | larger model if needed | MIT / Apache-2.0 |
| Vector store (`VectorStore`) | sqlite-vec (same `.db` file) | LanceDB (on-disk IVF-PQ) | Apache-2.0 |
| Graph store (`GraphStore`) | SQLite node-edge + recursive SQL | dedicated engine only if ever needed | — |
| In-memory graph analysis | NetworkX (on subgraphs) | NetworkX | BSD |
| Local LLM (extraction) | Ollama (Qwen2.5 / Mistral) | larger model | Apache-2.0 |

The runtime and interoperability layer (FastAPI local API, MCP server, OKF import/export) arrives with MVP 3.

---

## Core framework

- **Python 3.13+**
- **Typer** for the command-line interface (Click, MIT, is the mature fallback if we ever want fewer abstractions)
- **Pydantic v2** for schemas and validation
- **FastAPI** for the local API layer (introduced in MVP 3)
- **Markdown + YAML frontmatter** using the OKF v0.1 field set (`type`, `title`, `description`, `resource`, `tags`, `timestamp`)

## Local AI

Two kinds of model are used: a **generative model** (to compile, extract, and answer) and, from MVP 2, an **embedding model** (for vector search). The model is never hard-coded — it is set in `openkos.yaml`, behind an `LLMBackend` interface, so the user chooses.

**Runtime.** [Ollama](https://ollama.com) is the recommended local runtime (one-click install, cross-platform, manages model downloads); llama.cpp and LM Studio are alternatives. They all speak the same **OpenAI-compatible HTTP API** — this names the *request/response shape* that became a de-facto standard (like "S3-compatible" storage), **not** OpenAI the company or its cloud. No data leaves the machine; speaking one common dialect simply lets OpenKOS work with any conforming local runtime, which is the opposite of lock-in. Ollama and llama.cpp are both MIT-licensed.

**Recommended generative models, by hardware:**

- **~8 GB RAM** — 1.5B–3B (`qwen2.5:3b`, `llama3.2:3b`): summarization and light extraction with tight prompts.
- **~16 GB / Apple Silicon** — 7–8B (**Qwen2.5 / Qwen3 7B**, **Mistral 7B**): the sweet spot for extraction quality and speed.
- **32 GB+ / GPU** — 14B–24B (Qwen 14B, Mistral Small 24B): stronger reasoning and JSON / function-calling.

Qwen and Mistral are the defaults because they are Apache-2.0 (truly open), strong at instruction-following and structured/JSON output, come in many sizes, and Qwen is strong multilingually. A smaller model means weaker extraction and more reliance on review and the lint.

**Recommended embedding models:** `nomic-embed-text` (Apache-2.0, 768d) or `bge-small` (MIT, 384d) — small dimensions keep storage and search cheap at scale.

**If no model is installed,** `openkos init` guides rather than failing silently: it offers to pull a default matched to the detected hardware, or points to the runtime's installer if none is present. For non-technical users later, the path is an embedded runtime (no separate install), hardware-aware auto-download with a progress UI, and an optional, explicit cloud fallback — never for `confidential` content — for machines that cannot run a capable local model. There is an honest hardware floor: a weak machine runs a weaker model and leans more on review and lint.

- **Model Context Protocol (MCP)** for exposing the bundle to external agents (MVP 3).
- **Model licensing note:** not every "open" model is OSI open source. For strictly open, free defaults prefer **Qwen** and **Mistral** (Apache-2.0). **Gemma** (Gemma Terms) and **Llama** (Llama Community License) are free to use but under custom, non-OSI licenses — use them only if that distinction does not matter to you. The recommended embedding models (`bge`, `e5`, `all-MiniLM`, `nomic-embed`) are MIT or Apache-2.0.

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
