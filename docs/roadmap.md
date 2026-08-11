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

**Status: complete and shipped.**

This is the smallest slice that delivers real value: point OpenKOS at a folder of text, get back a structured, cited, OKF-conformant knowledge base you can query.

Deliverables:

- Text and markdown ingestion, with raw sources kept immutable in a `raw/` directory that sits beside the OKF bundle rather than inside it — so sources keep their own names and extensions, and nothing dropped there can break bundle conformance
- Compilation of sources into OKF concept documents (`type`, `title`, `description`, `resource`, `tags`, `timestamp`), with `# Citations` mirroring provenance into the body
- Provenance chain linking every object back to its source
- Automatic `index.md` (catalog) and `log.md` (chronological history), following the OKF reserved-file structure, with `okf_version: "0.1"` declared at the bundle root
- A conformance check for the three rules of OKF §9, run in CI against the reference bundle
- A **model spike** (done): the same ingest was run against candidate tags at the 7–8B tier — `qwen3:8b`, `mistral:7b`, and `gemma4:e4b` — measuring which returned schema-valid extraction with fewest retries, using [`examples/good-life-demo/`](../examples/good-life-demo/) as the target shape. The measurement settled `qwen3:8b` as the default (recorded in [ADR-0001](adr/0001-default-extraction-model.md)), not argument — and it stays a config value either way. The licence of each candidate was confirmed against the vendor's terms as part of the spike
- Lexical retrieval (SQLite FTS5) with an index-first navigation strategy
- Query answering with citations
- Freshness lint v0 — mechanical checks only: flag any fact whose `as of` stamp is older than the configured freshness window (default 7d), and surface orphan pages by scanning markdown links; volatility classification is deferred to MVP 2
- One lifecycle operation — a simple delete (`forget <concept-id>`: remove the concept, its index entry, and its state), with undo through plain git; archive, tombstones, the reference-aware `forget` flow, and the privacy purge all arrive in MVP 2
- A command-line interface: `init`, `ingest`, `query`, `lint`, `status`, a basic `forget`, and `doctor` (see the [CLI reference](cli.md))
- Output is plain files, browsable in Obsidian, VS Code, or GitHub

What a user can do after MVP 1: drop notes and articles into a folder, compile them into a living knowledge base, and get cited answers — entirely offline.

Where the community can contribute: new **producers** (ingesters for additional text-shaped sources) and simple **consumers** (viewers, exporters).

---

## MVP 2 — The Graph and Memory

*Goal: the knowledge base gets structure and its retrieval gets smart.*

**Status: complete and shipped.**

MVP 2 turns a flat set of documents into a connected, semantically searchable graph, and closes the loop so that good answers compound back into the base.

Deliverables:

- Entity, concept, and relationship extraction (LLM-assisted, human-in-the-loop) — whose hard core is **cross-source entity resolution, deduplication, and reversible merge** (the "boundary problem" MVP 1 deliberately sidesteps by doing single-source extraction only). The stance, lifted from the [Knowledge Object model](knowledge-object-model.md): prefer fewer, richer objects over fragmented ones, make every merge reversible, and keep entity-resolution decisions reviewable rather than silently automatic
- A typed knowledge graph over the bundle (markdown links plus a SQLite node-edge projection; NetworkX for analysis). The typing is an OpenKOS layer over OKF's untyped links: a bundle stays readable by any OKF consumer, which simply sees untyped edges.
- Hybrid retrieval: lexical (the FTS5 foundation shipped in MVP 1) + local vectors (`sqlite-vec`) + graph traversal, with context assembly
- Local embeddings served through Ollama (default `bge-m3`, multilingual; see [ADR-0006](adr/0006-default-embedding-model.md)), behind a configurable `Embedder` interface — **delivered and fully wired**: the `Embedder` protocol, `OllamaClient.embed()`, the `sqlite-vec` on-disk `vectors.db` store, and dense retrieval are all shipped. vec0 upsert/query, RRF hybrid fusion, and PageRank graph traversal all ship and feed `query`
- The two-output rule: a good answer can be filed back as a new OKF concept
- Incremental compilation and change tracking
- Freshness lint v1 — volatility classification with volatility-aware windows (per-type, LLM-suggested), contradiction and staleness detection, and a guided reconcile workflow
- The full lifecycle and `forget` surface — archive (`status: deprecated`), tombstones, the reference-aware scope/depth flow, and the privacy purge (git-history rewrite + index cleanup)
- Optional additional producers (PDF, web clip) as the extraction pipeline matures

What a user can do after MVP 2: ask questions that require synthesizing many sources, navigate a real graph of their knowledge, and watch the base get richer and stay honest as they use it.

Where the community can contribute: extraction strategies, relation vocabularies, retrieval rankers, and domain-specific object types.

---

## MVP 3 — The Runtime and Interoperability

*Goal: make OpenKOS a first-class knowledge substrate for AI agents, and a good citizen of the OKF ecosystem.*

MVP 3 exposes the knowledge base to agents and to the wider world of OKF-speaking tools.

**Onboarding hardening — shipped ([#128](https://github.com/jasonssdev/openkos/issues/128), closed).** The free-text model prompt in `openkos init` was replaced with a selection list over the chat models actually installed on the local Ollama server, with the recommended default marked, plus type-checking `model` on config read, rejecting YAML-reserved words in `validate_model`, and having `doctor` report a failed check instead of raising. `openkos --version` ([#181](https://github.com/jasonssdev/openkos/issues/181), closed) then closed the last gap in that area, so a user can tell which build they are running.

### Orchestration hardening — a prerequisite, not an extra

**This work lands before the deliverables below — and it has now shipped.** MVP 2 shipped a complete set of curation capabilities — `duplicates`, `adjudicate`, `suggest-relations`, `suggest-volatility`, `contradictions`, and the write verbs that resolve each one — but it did not ship the sequencing that binds them. Which advisor to run, when, and in what order was knowledge that lived only in the operator's head. The engine orchestrated the knowledge; the user still orchestrated the engine.

That gap is a prerequisite for MVP 3 rather than a parallel concern, because **an MCP server inherits it**. Exposing the verbs as agent tools moves the sequencing problem from the user's head into the agent's, where it is less reliable, not more: an agent has no better basis for knowing when to run `adjudicate` than a person does. And the resolution order is not arbitrary — identity precedes structure, because `merge` rewires typed edges (ADR-0005) and retargets third-party provenance (ADR-0011), so relations typed before a merge are relations the merge must redo. Encoding that dependency once, in the product, is what lets MVP 3 expose a workflow instead of a toolbox.

Scope, tracked as its own issues — **all four shipped**:

- **A consolidated curation loop** (`curate`, [#266](https://github.com/jasonssdev/openkos/issues/266), shipped) — one queue of pending decisions across every advisor, resolved in dependency order, reusing the existing write cores rather than reimplementing them.
- **A single-action pointer** (`next`, [#265](https://github.com/jasonssdev/openkos/issues/265), shipped) — deterministic, no model call: the one thing worth doing now, so no capability has to be discovered by reading `--help`.
- **Progress feedback** ([#190](https://github.com/jasonssdev/openkos/issues/190), shipped) on the verbs that wait on a model or rebuild an index, so latency is legible rather than indistinguishable from a hang.
- **Batch ingestion** (folder or glob, [#267](https://github.com/jasonssdev/openkos/issues/267), shipped), so populating a base is not one invocation per source.

The shared theme is the same principle already stated in the philosophy — *the human curates, the engine maintains* — applied to the engine's own operation: work the engine can carry should not be bookkeeping the user carries, and work the engine already does on the user's behalf should be reported rather than silent.

### Durable pending work — the second prerequisite

**This has not started.** Orchestration hardening solved *sequencing*: which advisor to run, when, and in what order. It did not make what the advisors produce survive. A curation session computes contradictions, candidate edges, and duplicate groups at real model cost and then lets them go — findings are printed to a terminal and lost, typed edges have no path to being applied outside the loop that produced them, and candidates past the display cap are unreachable by any verb. The reporting verbs inherit the same gap: they describe a bundle as settled while judgments already made about it live nowhere.

An MCP server inherits this one more sharply than it inherited sequencing. An agent asking *what is pending in this base?* cannot be answered by recomputing every advisor on every call — the answer has to be something that already exists. Pending work needs to be a first-class object in the bundle, written down, ranked, retractable, and re-openable, before it can be exposed as a tool.

The principle is the one above taken a step further: work the engine has already done on the user's behalf should not have to be done twice because nobody wrote it down.

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
