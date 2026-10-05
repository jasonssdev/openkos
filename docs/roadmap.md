---
type: Roadmap
title: OpenKOS Roadmap
description: A ship-first roadmap organized as MVP arcs plus an explicit, non-committed horizon.
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
- Compilation of sources into OKF concept documents (`type`, `title`, `description`, `resource`, `tags`, `generated`, `status`), with `sources` generated from `provenance`
- Provenance chain linking every object back to its source
- Automatic `index.md` (catalog) and `log.md` (chronological history), following the OKF reserved-file structure, with `okf_version: "0.2"` declared at the bundle root
- A conformance check for the three rules of OKF §11, run in CI against the reference bundle
- A **model spike** (done): the same ingest was run against candidate tags at the 7–8B tier — `qwen3:8b`, `mistral:7b`, and `gemma4:e4b` — measuring which returned schema-valid extraction with fewest retries, using [`examples/good-life-demo/`](../examples/good-life-demo/) as the target shape. The measurement settled `qwen3:8b` as the default (recorded in [ADR-0001](adr/0001-default-extraction-model.md)), not argument — and it stays a config value either way. The licence of each candidate was confirmed against the vendor's terms as part of the spike
- Lexical retrieval (SQLite FTS5) with an index-first navigation strategy
- Query answering with citations
- Freshness lint v0 — mechanical checks only: flag any fact whose `as of` stamp is older than the configured freshness window (default 7d), and surface orphan pages by scanning markdown links; volatility classification is deferred to MVP 2
- One lifecycle operation — a simple delete (`forget <concept-id>`: remove the concept, its index entry, and its state), with undo through plain git; archive, tombstones, the reference-aware `forget` flow, and the privacy purge all arrive in MVP 2
- A command-line interface: `init`, `ingest`, `query`, `lint`, `status`, a basic `forget`, and `doctor` (see the [CLI reference](cli.md))
- Output is plain files, browsable in Obsidian, VS Code, or GitHub

What a user can do after MVP 1: drop notes and articles into a folder, compile them into a living knowledge base, and get cited answers — entirely offline.

Where the community can contribute: sample sources and example bundles that serve as fixtures, documentation, and bug reports against the shipped verbs.

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

What a user can do after MVP 2: ask questions that require synthesizing many sources, navigate a real graph of their knowledge, and watch the base get richer and stay honest as they use it.

Where the community can contribute: extraction strategies, relation vocabularies, retrieval rankers, and domain-specific object types.

---

## MVP 3 — The Ask Surface

*Goal: a user who never opens a terminal can ask the base a question and see what it is waiting on.*

**Status: complete and shipped.** Both prerequisites and every deliverable below have shipped, including the `mcp` adapter itself.

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
- **No thin adapter was possible at the time.** The command logic lived in `openkos.cli.main`, and `application/` covered ingest, query, and lifecycle only. The read verbs — `status`, `list`, navigation — had no application service behind them, so an MCP server written against the code as it stood would either have reimplemented them or imported Typer. Extracting them was prerequisite zero, not a cleanup.

Three edges of the original arc move out on the same reasoning. A local **REST API** goes to the horizon, because MCP already answers the question REST was there to answer, and a second network surface doubles the trust boundary for no user we can name (MVP 6 promotes it, because a desktop app is that user). **Memory projections** go with it: they are a research direction, not a deliverable with someone waiting on it. A **stable Python API** moves out of this arc: the application services under `application/` are the surface it would present, but MCP already serves this arc's audience, and declaring those services stable is a compatibility promise with no user waiting on it yet. It ships in MVP 6, with the desktop app as its first client, so the surface is shaped by a real consumer.

Deliverables — **all shipped**:

- **Application services for the read verbs** (shipped) — `status`, `list`, and navigation lifted out of the CLI, so a second adapter is a thin layer over shared cores rather than a second implementation of them
- **An MCP server** ([#1009](https://github.com/jasonssdev/openkos/issues/1009), shipped) exposing the bundle as tools any compatible agent can call: `query`, `get`, `navigate`, and *what is pending* — the last of which is answerable only because durable pending work shipped first
- **Sensitivity enforcement at the MCP boundary** ([#1010](https://github.com/jasonssdev/openkos/issues/1010), shipped). The egress gate already covering embeddings ([#922](https://github.com/jasonssdev/openkos/issues/922), closed) is joined by a disclosure gate specific to the MCP surface, so confidential objects do not leave through the new surface either as LLM egress or as a raw tool result
- **Two ADRs before code** (written): the sync/async boundary ([ADR-0021](adr/0021-sync-async-boundary.md)), and concurrency across a human in an editor, an agent over MCP, and (from MVP 4) a daemon ([ADR-0020](adr/0020-concurrency-across-three-writers.md)). The interprocess lock shipped for concurrent `openkos` processes ([#925](https://github.com/jasonssdev/openkos/issues/925), closed) makes those processes safe with respect to each other; the MCP adapter itself never writes and never takes that lock. Building the adapter itself added two more: the hand-rolled server choice ([ADR-0027](adr/0027-hand-rolled-stdio-mcp-server.md)) and MCP disclosure as its own boundary, separate from LLM egress ([ADR-0028](adr/0028-mcp-disclosure-is-its-own-boundary.md))

What a user can do after MVP 3: ask their knowledge base questions from a chat client they already have, see what the base is waiting on, and read and edit the answer in Obsidian — without a terminal.

Where the community can contribute: MCP integrations, client configurations, and read-side tools.

---

## MVP 4 — The Unattended Engine

*Goal: the engine carries the work it can carry, and queues only the work a human should decide.*

**Status: complete and shipped.**

MVP 3 gives the base a second surface; it does not reduce what the base asks of its user. Every maintenance pass is still an invocation someone has to remember, and the output of that pass is a list of tasks. The philosophy commits to the engine reducing *cognitive maintenance* while leaving *cognitive responsibility* with the human. This arc enforces that line in the engine's own operation.

Deliverables — **all shipped**, with the one measurement noted below:

- **A job substrate and a background runtime.** `openkos daemon` runs due jobs in the foreground, one at a time, records every outcome in a disposable `.openkos/jobs.db`, and stops cleanly on a signal. Only the runner is bounded by a call budget; a command run by hand never is
- **Folder watch.** A source that settles in a configured inbox folder is ingested without an invocation; the inbox is outside the workspace and never written. A file edited after import is imported as a new version, and the supersession of the earlier Source is queued for a person to confirm rather than written
- **Scheduled maintenance.** A pass on a timer refreshes the derived indexes, counts lint findings and runs the advisors, kept human-in-the-loop: it writes nothing under `bundle/`
- **An explicit rule for what runs unattended and what queues.** The engine performs the non-consequential (`ingest`, `reindex`, `lint`, computing findings) and *enqueues* the consequential (merges, forgets, relation confirmations) as durable pending work in `.openkos/findings.db`, listed by `openkos pending` and read by `next`, `status`, the MCP server and `curate`; a human-facing write resolves the row it answers
- **A workspace lock held only for commit phases,** so a person and a daemon share one workspace, with `--wait` to ride out a short contention and incremental refresh of the derived full-text and graph stores
- **The instrument for measuring how much of the curation queue is mechanical.** `openkos pending --stats` reports how often a proposal was applied as proposed, per kind. The measurement itself needs the queue to accumulate real use and is not yet taken

What a user can do after MVP 4: leave OpenKOS running, drop sources into a folder, and find the base current — with a short queue holding only the decisions that were genuinely theirs.

Where the community can contribute: schedulers, watch backends, and reconciliation heuristics.

---

## The Quiet Engine — a named arc before MVP 5

*Goal: cut the number of decisions OpenKOS asks of a person per ingested source, and make the remaining ones optional.*

**Status: complete and shipped.** The exit criteria below were measured against the pre-registered bars and met ([results](../evals/quiet_engine/results/)). Two pieces were not delivered and continue as follow-up work, each under its own pre-registered measurement: the opt-in post-hoc review for one structural class, and accept-recommended on Identity prompts. The arc is a named arc between MVP 4 and MVP 5, not a numbered MVP: MVP 5 and MVP 6 keep their numbers and content ([ADR-0044](adr/0044-the-quiet-engine-arc-precedes-interoperability.md)).

MVP 6's goal is a non-technical user without a terminal, but a desktop app built over today's engine would present the same review queue with buttons. The engine also creates much of that queue itself: a concept that already exists is forked into a `-N` copy rather than attached to, and unattended writes can leave derived indexes stale. This arc removes the load at its source, before a surface is built on top of it.

Deliverables, in dependency order:

- **Attach, don't fork, at ingest.** When an extracted candidate matches an existing concept on the same OKF type and the same normalized title key, the existing concept is revised (provenance appended, evidence merged, `version` bumped, canonical id kept) instead of a `-N` copy being written. Types where an identical title routinely names different things (Event, Person) keep today's behavior until measured
- **Versions revise, deprecation propagates.** A new version of a watched source revises the concepts its previous version produced, and superseding a Source deprecates the concepts whose entire provenance is that Source, so retiring a Source needs no `--force`
- **Fresh after every write, including unattended ones.** Daemon imports refresh the same derived indexes as CLI writes, the watch applies the same text-source allowlist as `ingest`, and a source is searchable as soon as it lands
- **Post-hoc review for one structural class, under [ADR-0034](adr/0034-identity-auto-merge-only-for-a-measured-class.md).** Same-type, same-normalized-key `base`/`-N` families, and anything attach-at-ingest cannot attach, get their own pre-registered decision rule on a fixture with hard negatives. If the class passes it ships opt-in and off by default, with each automatic merge committed and its `unmerge` command disclosed; if it fails, nothing ships and the measurement is recorded
- **Review becomes a digest, not a gate.** Every prompt offers skip and accept-recommended, "no" is distinguishable from "not now", unattended runs end with a "what changed" summary listing each automatic action and its undo, and `pending` never blocks querying or ingesting and states truncation
- **Two product metrics, measured before and after:** human decisions per ingested source, and time from dropping a file to the first answer that can cite it, on a committed corpus shaped like the end-to-end corpus

Exit criteria — the bars were fixed in a [pre-registration](../evals/quiet_engine/PREREGISTRATION.md) written before the arc started:

- Decisions per source at or below a registered bar
- No `-N` duplicate created for same-type, same-key concepts outside the excluded types
- No false sufficiency refusal attributable to a stale index
- Every automatic action listed with its undo

Out of scope: any GUI (MVP 6); freezing the Python API ([ADR-0039](adr/0039-the-stable-python-api-ships-with-a-desktop-app-as-its-first-client.md) is unchanged); automatic merges based on judge confidence; automatic cross-type merges; anything irreversible, such as `purge`, without explicit consent.

What a user can do after this arc: drop sources in and find a base that is current and mostly free of engine-made duplicates, with a short after-the-fact digest instead of a blocking queue.

Where the community can contribute: fixtures with hard negatives for the structural class, and corpora for the product metrics.

---

## MVP 5 — Interoperability

*Goal: exchange knowledge with any OKF-speaking tool.*

**Status: not started.**

Deliverables:

- **OKF export first.** The bundle is already OKF-conformant, so export is the cheap half — and it is what first makes our conformance claim testable by somebody else
- **OKF import second.** Consuming bundles produced by other tools, including Google's reference producers, is cross-bundle entity resolution. It is an arc of work in its own right, not the mirror image of export
- Sensitivity enforcement at the export boundary — confidential objects excluded from exports and sharing

This arc is deliberately narrow. Export and import are built on the internal `application/` services; a public API is a compatibility promise of a different kind, and freezing it before import has pressured those services, while OKF is still v0.2, would freeze the wrong shape. The stable Python API therefore moves to MVP 6, and the extension points that depend on it to the Horizon ([ADR-0039](adr/0039-the-stable-python-api-ships-with-a-desktop-app-as-its-first-client.md)).

What a user can do after MVP 5: move knowledge in and out of OpenKOS without losing its structure.

Where the community can contribute: interop adapters and OKF conformance fixtures.

---

## MVP 6 — The Desktop App

*Goal: a non-technical user can use OpenKOS without a terminal.*

**Status: not started.**

Reading knowledge already needs no new interface: the bundle is plain markdown, and Obsidian or any editor is its viewer. What a terminal still gates is installing the engine, dropping sources in, asking, and deciding the pending queue, where "the human curates, the engine maintains" currently means `[y/N]` prompts.

Deliverables:

- **A desktop application.** One installer and an icon; internally a local web UI on `localhost` wrapped in a native shell, so the user never starts a server by hand. Nothing leaves the machine
- **Packaging as a core deliverable, not an extra.** The main barrier for a non-technical user is installation: Python, `uv`, a local model runtime, multi-GB models and `openkos.yaml`. The app ships or guides the engine and model setup
- **A local API** the app talks to. This promotes the Horizon item for a local REST API: the desktop app is the consumer MCP cannot serve
- **A stable Python API** over the application services, versioned and declared public, with the desktop app as its first client, so the API is shaped by a real consumer rather than designed in a vacuum
- **A minimum app scope that maps onto shipped verbs:** drop files (the inbox watch), ask (`query`), and review the pending-decision queue (`pending`, `curate`), which is the heart of the app

The shell technology (Tauri, Electron, or another; each needs the Python engine as a sidecar process) is not decided here. It gets its own ADR when this arc starts. Every UI remains a thin adapter over the same local engine; adding it never touches the core.

What a user can do after MVP 6: install OpenKOS like any other application, drop files in, ask questions, and settle the pending queue, without a terminal.

Where the community can contribute: packaging for more platforms, accessibility, and client work against the stable API.

---

## Horizon (not yet committed)

These are promising directions we intend to explore *after* the MVPs prove out with real users. They are listed for transparency and to invite discussion, not as promises:

- Optional additional producers (PDF, web clip) as the extraction pipeline matures
- Opt-in memory projections over the graph (episodic, semantic, procedural)
- A graphical knowledge explorer
- Interactive graph visualization and memory browsing
- A richer, configurable memory engine
- Federation and selective sharing across multiple bundles or people
- Finer-grained agent permissions and sandboxing
- Extension points for third-party producers and consumers, built on the stable Python API
- A plugin marketplace

Priorities here will be set by what users actually need, and by where the community wants to contribute.

---

## How to read this roadmap

The MVP boundaries are firm; the deliverables within them are negotiable. If you are considering contributing, the best entry points are the "community can contribute" notes under the current MVP. Open an issue before large changes so we can make sure the work fits and can be merged.
