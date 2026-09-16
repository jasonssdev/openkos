---
type: Roadmap
title: OpenKOS Roadmap
description: A ship-first roadmap organized as five MVP arcs plus an explicit, non-committed horizon.
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
- Hybrid retrieval: lexical (the FTS5 foundation shipped in MVP 1) + local vectors (`sqlite-vec`), fused by reciprocal rank fusion, with context assembly. A third channel — a seeded personalized-PageRank walk over the typed graph — was built, measured, and **retired** ([#434](https://github.com/jasonssdev/openkos/issues/434)): PageRank ranks by global centrality, which is a property of the corpus rather than of the question, so the reserved slot cost a real hit on 7 of 10 questions and helped on none. The typed graph itself was not the problem and stays; `query` simply no longer reads it
- Local embeddings served through Ollama (default `bge-m3`, multilingual; see [ADR-0006](adr/0006-default-embedding-model.md)), behind a configurable `Embedder` interface — **delivered and fully wired**: the `Embedder` protocol, `OllamaClient.embed()`, the `sqlite-vec` on-disk `vectors.db` store, and dense retrieval are all shipped. vec0 upsert/query and RRF hybrid fusion feed `query`; `.openkos/graph.db` is still built by `reindex`, and its readers are `contradictions` and the typed-graph tooling
- The two-output rule: a good answer can be filed back as a new OKF concept
- Incremental compilation and change tracking
- Freshness lint v1 — volatility classification with volatility-aware windows (per-type, LLM-suggested), contradiction and staleness detection, and a guided reconcile workflow
- The full lifecycle and `forget` surface — archive (`status: deprecated`), tombstones, the reference-aware scope/depth flow, and the privacy purge (git-history rewrite + index cleanup)
- Optional additional producers (PDF, web clip) as the extraction pipeline matures

What a user can do after MVP 2: ask questions that require synthesizing many sources, navigate a real graph of their knowledge, and watch the base get richer and stay honest as they use it.

Where the community can contribute: extraction strategies, relation vocabularies, retrieval rankers, and domain-specific object types.

---

## MVP 3 — The Ask Surface

*Goal: a user who never opens a terminal can ask the base a question and see what it is waiting on.*

**Status: in progress — both prerequisites below have shipped.**

MVP 3 gives the bundle a second surface. Reading it without a terminal is already solved: `bundle/` opens directly as an Obsidian vault, with links that resolve, a graph view over the typed edges, and OKF frontmatter rendered as properties — no plugin, and no configuration shipped by us ([ADR-0019](adr/0019-dot-directories-are-not-knowledge.md), [#981](https://github.com/jasonssdev/openkos/issues/981)–[#984](https://github.com/jasonssdev/openkos/issues/984), closed). What remains terminal-only is *asking* and *deciding*. MCP closes that gap without a frontend: an MCP-speaking chat client becomes the interface, so humans and agents address one surface instead of two that have to be kept in agreement.

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

**This has shipped.** Orchestration hardening solved *sequencing*: which advisor to run, when, and in what order. It did not make what the advisors produce survive. A curation session computed contradictions and duplicate groups at real model cost and then let them go — findings were printed to a terminal and lost — and the reporting verbs inherited the same gap, describing a bundle as settled while judgments already made about it lived nowhere.

An MCP server inherits this one more sharply than it inherited sequencing. An agent asking *what is pending in this base?* cannot be answered by recomputing every advisor on every call — the answer has to be something that already exists. Pending work had to become a first-class object, written down and re-openable, before it could be exposed as a tool.

It now is, in two stores with deliberately opposite policies ([ADR-0014](adr/0014-durable-pending-work-stores.md)) — because the two things being stored have opposite natures. A **finding** (a machine-computed contradiction verdict) is recomputable inference, so it lives in the derived layer at `.openkos/findings.db`, written by `curate`'s Contradictions stage and by `contradictions` itself through that same path. It carries the digests of the inputs it was judged from, so staleness is decided per row against what those inputs say now, rather than by invalidating the whole store on any bundle change. A **decision** (an operator's verdict on one of those proposals) is irreplaceable, so it lives in the canonical layer as a sidecar under `bundle/.state/decisions/`, reusing ADR-0013's storage shape verbatim: one frontmatter document per concept, a non-`.md` suffix that keeps it out of every `*.md` walk, and ids plus verdict only — no rationale, no quoted body text, which is what keeps that store non-confidential. The two are joined at read time rather than through a stored pointer, and `next`, `status`, and `reconcile --from-findings` all read them back, so a pair the operator already judged consistent stops being reported as outstanding instead of resurfacing on every run.

The principle is the one above taken a step further: work the engine has already done on the user's behalf should not have to be done twice because nobody wrote it down.

### Why this arc was split

MVP 3 was originally scoped as *The Runtime and Interoperability*: one arc holding an MCP server, a stable API, a local REST API, scheduled maintenance loops, full OKF import/export, sensitivity enforcement, memory projections, and third-party extension points. That is three audiences and three unrelated risks in a single arc, which makes it impossible to say what "done" means or who it is done for. It is now three arcs, each with one audience and one risk.

Two measurements set the boundaries, rather than taste:

- **Reading without a terminal was already solved, and cost nothing.** The capability had been claimed in the docs for months and never tested; verifying it took two minutes. That narrows this arc rather than filling it — a "nicer interface" is not a frontend project, because the remaining gap is asking and deciding, not reading.
- **No thin adapter is possible today.** `openkos.cli.main` is roughly 16,000 lines across 27 commands, against roughly 4,400 lines in `application/`, which covers ingest, query, and lifecycle only. The read verbs — `status`, `list`, navigation — have no application service behind them. An MCP server written against the code as it stands would either reimplement them or import Typer. Extracting them is prerequisite zero, not a cleanup.

Two edges of the original arc move out on the same reasoning. A local **REST API** goes to the horizon, because MCP already answers the question REST was there to answer, and a second network surface doubles the trust boundary for no user we can name. **Memory projections** go with it: they are a research direction, not a deliverable with someone waiting on it.

Deliverables:

- **Application services for the read verbs** — `status`, `list`, and navigation lifted out of the CLI, so that a second adapter is a thin layer over shared cores rather than a second implementation of them
- **A stable Python API**, which falls out of the above as the surface those services present
- **An MCP server** exposing the bundle as tools any compatible agent can call: `query`, `get`, `navigate`, and *what is pending* — the last of which is answerable only because durable pending work shipped first
- **Sensitivity enforcement at the MCP boundary.** The egress gate already covering embeddings ([#922](https://github.com/jasonssdev/openkos/issues/922), closed) extends to every tool response, so confidential objects do not leave through the new surface
- **Two ADRs before code**: the sync/async boundary, and concurrency across a human in an editor, an agent over MCP, and (from MVP 4) a daemon. The interprocess lock shipped for concurrent `openkos` processes ([#925](https://github.com/jasonssdev/openkos/issues/925), closed) makes those processes safe with respect to each other; it says nothing about those three writers

What a user can do after MVP 3: ask their knowledge base questions from a chat client they already have, see what the base is waiting on, and read and edit the answer in Obsidian — without a terminal.

Where the community can contribute: MCP integrations, client configurations, and read-side tools.

---

## MVP 4 — The Unattended Engine

*Goal: the engine carries the work it can carry, and queues only the work a human should decide.*

**Status: not started.**

MVP 3 gives the base a second surface; it does not reduce what the base asks of its user. Every maintenance pass is still an invocation someone has to remember, and the output of that pass is a list of tasks. The philosophy commits to the engine reducing *cognitive maintenance* while leaving *cognitive responsibility* with the human. This arc enforces that line in the engine's own operation.

Deliverables:

- A job substrate and a background runtime
- Folder watch — a source dropped in is ingested without an invocation
- Scheduled maintenance: lint, reconcile, and findings passes on a timer, kept human-in-the-loop
- **An explicit rule for what runs unattended and what queues.** The engine performs the non-consequential (`ingest`, `reindex`, `lint`, computing findings) and *enqueues* the consequential (merges, forgets, relation confirmations) as durable pending work — which `.openkos/findings.db` and `bundle/.state/decisions/` already exist to hold
- **A measurement of how much of the curation queue is mechanical.** A four-source ingest produced nine unresolved duplicate groups, a good share of them entities the engine had itself disambiguated. If the engine can disambiguate, the fraction it could also reconcile is a number worth having before automating anything on top of the queue

What a user can do after MVP 4: leave OpenKOS running, drop sources into a folder, and find the base current — with a short queue holding only the decisions that were genuinely theirs.

Where the community can contribute: schedulers, watch backends, and reconciliation heuristics.

---

## MVP 5 — Interoperability

*Goal: exchange knowledge with any OKF-speaking tool.*

**Status: not started.**

Deliverables:

- **OKF export first.** The bundle is already OKF-conformant, so export is the cheap half — and it is what first makes our conformance claim testable by somebody else
- **OKF import second.** Consuming bundles produced by other tools, including Google's reference producers, is cross-bundle entity resolution. It is an arc of work in its own right, not the mirror image of export
- Sensitivity enforcement at the export boundary — confidential objects excluded from exports and sharing
- Extension points for third-party producers and consumers

What a user can do after MVP 5: move knowledge in and out of OpenKOS without losing its structure, and extend it with their own producers and consumers.

Where the community can contribute: interop adapters, producers, and consumers.

---

## Horizon (not yet committed)

These are promising directions we intend to explore *after* the MVPs prove out with real users. They are listed for transparency and to invite discussion, not as promises:

- A local REST API, if a consumer appears that MCP cannot serve
- Opt-in memory projections over the graph (episodic, semantic, procedural)
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
