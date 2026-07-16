---
type: Roadmap
title: OpenKOS Roadmap
description: A ship-first roadmap organized as three MVP arcs plus an explicit, non-committed horizon.
tags:
  - roadmap
  - mvp
  - development
  - openkos
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-14T19:00:00Z
sensitivity: public
---

# Roadmap

OpenKOS is built ship-first. The goal is not to design a perfect platform up front, but to release the smallest genuinely useful thing, put it in front of users, and iterate. Each MVP is a complete, usable arc: it can be adopted on its own, and it sets up the next one.

Two commitments hold across every stage:

- **Everything is an OKF bundle.** Output is always conformant Open Knowledge Format, so nothing you build in an early MVP is thrown away later.
- **Local-first.** Every capability runs on your machine and works offline; cloud is optional, never required.

The horizon section at the end lists directions we find promising but deliberately do **not** commit to yet.

---

## MVP 1 — The Compiler

*Goal: the Karpathy LLM Wiki loop, done locally and correctly, over plain text — useful in an afternoon.*

This is the smallest slice that delivers real value: point OpenKOS at a folder of text, get back a structured, cited, OKF-conformant knowledge base you can query.

Deliverables:

- Text and markdown ingestion, with raw sources kept immutable
- Compilation of sources into OKF concept documents (`type`, `title`, `description`, `resource`, `tags`, `timestamp`)
- Provenance chain linking every object back to its source
- Automatic `index.md` (catalog) and `log.md` (chronological history)
- Lexical retrieval (SQLite FTS5) with an index-first navigation strategy
- Query answering with citations
- Freshness lint v0 — mechanical checks only: flag any fact whose `as of` stamp is older than the configured freshness window (default 7d), and surface orphan pages by scanning markdown links; volatility classification is deferred to MVP 2
- Basic lifecycle operations — undo the last ingest (via `git revert`), archive (`status: deprecated`), and simple object deletion (removing the concept and its index references, with undo through normal git history); tombstones, the reference-aware `forget` flow, and the privacy purge arrive in MVP 2
- A command-line interface: `init`, `ingest`, `query`, `lint`, `status`, and a basic `forget` (see the [CLI reference](cli.md))
- Output is plain files, browsable in Obsidian, VS Code, or GitHub

What a user can do after MVP 1: drop notes and articles into a folder, compile them into a living knowledge base, and get cited answers — entirely offline.

Where the community can contribute: new **producers** (ingesters for additional text-shaped sources) and simple **consumers** (viewers, exporters).

---

## MVP 2 — The Graph and Memory

*Goal: the knowledge base gets structure and its retrieval gets smart.*

MVP 2 turns a flat set of documents into a connected, semantically searchable graph, and closes the loop so that good answers compound back into the base.

Deliverables:

- Entity, concept, and relationship extraction (LLM-assisted, human-in-the-loop)
- A typed knowledge graph over the bundle (markdown links plus a SQLite node-edge projection; NetworkX for analysis)
- Hybrid retrieval: lexical (FTS5) + local vectors (`sqlite-vec`) + graph traversal, with context assembly
- Local embeddings (Sentence Transformers) and local model runtimes (Ollama)
- The two-output rule: a good answer can be filed back as a new OKF concept
- Incremental compilation and change tracking
- Freshness lint v1 — volatility classification with volatility-aware windows (per-type, LLM-suggested), contradiction and staleness detection, and a guided reconcile workflow
- The full `forget` surface — tombstones, the reference-aware scope/depth flow, and the privacy purge (git-history rewrite + index cleanup)
- Optional additional producers (PDF, web clip) as the extraction pipeline matures

What a user can do after MVP 2: ask questions that require synthesizing many sources, navigate a real graph of their knowledge, and watch the base get richer and stay honest as they use it.

Where the community can contribute: extraction strategies, relation vocabularies, retrieval rankers, and domain-specific object types.

---

## MVP 3 — The Runtime and Interoperability

*Goal: make OpenKOS a first-class knowledge substrate for AI agents, and a good citizen of the OKF ecosystem.*

MVP 3 exposes the knowledge base to agents and to the wider world of OKF-speaking tools.

Deliverables:

- An MCP server exposing the bundle as tools (query, get, navigate) any compatible agent can call
- A stable Python API, CLI, and a local REST API
- Agent-assisted maintenance loops — scheduled lint, reconcile, and synthesis passes, kept human-in-the-loop
- Full OKF import/export: consume bundles produced by other tools (including Google's reference producers) and export yours for others to consume
- Sensitivity enforcement at trust boundaries — confidential objects are never sent to cloud models and are excluded from exports and sharing
- Opt-in memory projections over the graph (episodic, semantic, procedural)
- Extension points for third-party producers and consumers

What a user can do after MVP 3: wire OpenKOS into their AI agents as durable memory, exchange knowledge with any OKF tool, and let the base maintain itself on a schedule with review.

Where the community can contribute: MCP integrations, interop adapters, memory strategies, and agent workflows.

---

## Horizon (not yet committed)

These are promising directions we intend to explore *after* the MVPs prove out with real users. They are listed for transparency and to invite discussion, not as promises:

- A desktop application and graphical knowledge explorer
- Interactive graph visualization and memory browsing
- A richer, configurable memory engine
- Federation and selective sharing across multiple bundles or people
- Finer-grained agent permissions and sandboxing
- A plugin marketplace

Priorities here will be set by what users actually need, and by where the community wants to contribute.

---

## How to read this roadmap

The MVP boundaries are firm; the deliverables within them are negotiable. If you are considering contributing, the best entry points are the "community can contribute" notes under the current MVP. Open an issue before large changes so we can make sure the work fits and can be merged.
