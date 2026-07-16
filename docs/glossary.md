---
type: Reference
title: OpenKOS Glossary
description: Definitions of the core terms and vocabulary used across OpenKOS.
tags:
  - openkos
  - glossary
  - reference
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-15T04:00:00Z
sensitivity: public
---

# Glossary

Definitions of the terms that appear throughout OpenKOS. Terms are listed alphabetically.

**Bundle (OKF bundle)** — A directory of markdown concept documents that together form a knowledge base. The bundle is the unit of storage and interchange; it is plain files, portable to any tool. See [Open Knowledge Format](#open-knowledge-format-okf).

**Canonical layer** — The durable part of an OpenKOS installation: the OKF bundle (markdown + frontmatter), the immutable raw sources, git history, and the SQLite operational store. It is the source of truth and is meant to outlive any tool. Contrast with the [Derived layer](#derived-layer).

**Compiler / compile** — The part of the engine (and the process) that turns a raw source into Knowledge Objects: extraction, typing, linking, freshness classification, and provenance. It is what `ingest` runs.

**Concept / concept document** — OKF's name for a single knowledge file: one markdown file with YAML frontmatter representing one thing. In OpenKOS a concept document is the physical form of a [Knowledge Object](#knowledge-object-ko).

**Config (`openkos.yaml`)** — A per-bundle configuration file holding the engine's settings for that bundle: the local model, review mode, default sensitivity, freshness window, and the type registry. Structured YAML, owned by the engine and edited by the user. Distinct from the [Operating manual](#operating-manual-agentsmd).

**Consumer** — Any tool that reads and reasons over an OKF bundle (a viewer, a search index, an agent). OpenKOS is both a consumer and a [Producer](#producer).

**Context assembly** — The retrieval step that gathers the most relevant concepts (and their citations) into the model's context — to answer a query, or to reconcile against what already exists during ingest.

**Continuant / occurrent** — The foundational split behind the object types: continuants *persist* through time (Person, Concept, Entity), while occurrents *happen* in time (Event, Procedure). Borrowed from upper ontologies to ground the type vocabulary.

**Derived layer** — The rebuildable part of an installation: vector indexes and graph projections. Because it can always be reconstructed from the [Canonical layer](#canonical-layer), the engines behind it are swappable and never a lock-in.

**Entity resolution** — Deciding when two mentions refer to the same object, and merging duplicates, so the graph stays clean. A hard part of extraction, kept reviewable rather than silently automatic.

**Fast fact** — A fact that can change within days (a live count, a balance, a status). Fast facts belong in their home system; the bundle should point at them rather than copy them. Contrast with [Slow fact](#slow-fact).

**Filter-first retrieval** — A retrieval strategy where lexical search (FTS5) and the graph narrow the candidate set first, and vector ranking is applied only to that small set — keeping search fast even with millions of vectors.

**Freshness** — The temporal validity of a fact: whether it is still true *now*. See [Freshness class](#freshness-class).

**Freshness class** — The category assigned to a fact based on how it behaves over time. Every fact must be one of three: [Timeless](#timeless), [Snapshot](#snapshot), or [Pointer](#pointer). The class determines how tooling (the lint) treats it.

**High-water-mark (sensitivity)** — The rule that a derived object is at least as sensitive as the most sensitive source it was compiled from; sensitivity propagates upward along the provenance chain.

**Ingest** — The operation of compiling a raw source into the bundle: reading it, writing a summary concept, updating related concepts, and recording provenance and log entries.

**index.md** — A catalog file that lists the bundle's concepts with short summaries, used for navigation and index-first retrieval. Defined by OKF as an optional, reserved filename.

**Knowledge graph** — The network formed by concepts and the typed relationships (markdown links) between them. Richer than the folder hierarchy; traversed during retrieval.

**Knowledge Object (KO)** — The fundamental unit of knowledge in OpenKOS: an OKF concept document plus a thin OpenKOS layer (provenance chain, freshness class, recommended type vocabulary). See [`knowledge-object-model.md`](knowledge-object-model.md).

**Lint** — The operation that checks the health of the bundle: unstamped volatile facts, stale claims, contradictions, orphan pages. Enforces the freshness discipline automatically.

**LLM Wiki pattern** — Andrej Karpathy's idea that a language model should *incrementally build and maintain* a persistent, interlinked knowledge base between you and your sources, rather than re-retrieving raw documents on every query. The pattern OpenKOS implements.

**Living document** — A concept document that is rewritten as new sources arrive. Concepts are living; raw sources are not. History is preserved through git and `log.md`, so "mutable head, immutable history."

**Local-first** — Software that runs on your machine and works offline, keeping your data under your control. The cloud is optional, never required.

**log.md** — An append-only, chronological record of what happened in the bundle (ingests, queries, reconciliations). Defined by OKF as an optional, reserved filename.

**MCP (Model Context Protocol)** — A standard for exposing tools to AI agents. In MVP 3, OpenKOS exposes the bundle through an MCP server so agents can query and navigate it.

**Operating manual (`AGENTS.md`)** — A per-bundle markdown file, following the vendor-neutral `AGENTS.md` convention, that tells an AI agent how the bundle is organized and what conventions to follow when operating on it (ingesting, querying, maintaining). Prose instructions — the disciplined-maintainer layer of the LLM Wiki pattern. Distinct from the structured [Config](#config-openkosyaml).

**Open Knowledge Format (OKF)** — A vendor-neutral open specification, published by Google Cloud in June 2026, that formalizes the LLM Wiki pattern into a portable format: a directory of markdown concepts with YAML frontmatter, requiring only a `type` field. OpenKOS adopts OKF as its storage and interchange layer. See [`okf-alignment.md`](okf-alignment.md).

**Pointer** — A freshness class for facts whose current value matters and changes fast: instead of the value, store where the truth lives (a link), optionally with the last observed value and a stamp. One of the three legal forms of a fact.

**Producer** — Any tool that writes an OKF bundle. OpenKOS produces bundles by compiling your text; other producers exist (for example, agents that document databases).

**Provenance / provenance chain** — The recorded link between a derived Knowledge Object and the immutable raw source(s) it was compiled from. Provenance is what makes retrieval explainable: any answer can be traced back to its origin.

**Query** — The operation of asking a question against the bundle and getting a cited answer, assembled from relevant concepts.

**Raw source** — An original input file (article, notes, PDF). Raw sources are **immutable**: OpenKOS reads from them but never rewrites them.

**Reconstructibility** — The guarantee that every index, embedding, and graph projection can be rebuilt from the canonical layer. It is why derived engines are swappable and why no single dependency is a lock-in.

**Representation, not truth** — The principle that OpenKOS stores how an individual understands and documents knowledge, not objective truth. It is not an epistemic authority: conflicting perspectives may coexist, each keeping its own context (source, assumptions, evidence). Distinct from freshness — the engine reconciles what is out of date, not what is genuinely contested.

**Sensitivity** — An OpenKOS-layer label (`public`, `private`, or `confidential`) that governs what may cross a trust boundary: what an agent may read, what may be sent to a cloud model, and what is included in exports or sync. It is a disclosure policy, not encryption, defaults to `private`, and propagates along the provenance chain by a high-water-mark rule.

**Slow fact** — A fact stable for weeks, months, or years (how a system is built, who owns what, a decision and its reasoning). Slow facts are what a knowledge base exists to store. Contrast with [Fast fact](#fast-fact).

**Snapshot** — A freshness class for a dated observation. A snapshot never goes stale because it claims what was true *on a date*, not what is true now. One of the three legal forms of a fact.

**Stamp** — A date marker such as `(as of 2026-07-14)`, optionally with a source, attached to a fast-changing fact so it cannot silently rot.

**Three-criteria test** — The bar a type must pass to enter the canonical core: distinct structure, distinct relationships, and transversal recurrence across domains. If it fails, it belongs as a domain extension, a tag, or body structure — not a core type.

**Three-tier classification** — The type-vocabulary model: a stable canonical core, optional shareable domain extensions, and personal emergent types coined per bundle.

**Timeless** — A freshness class for facts that do not decay and need no date. One of the three legal forms of a fact.

**Tombstone** — A log entry left behind when an object is deleted, recording that it existed and was removed (except in a privacy purge), so deletion stays auditable.

**Two-output rule** — The practice that a good answer to a query can be filed back into the bundle as a new concept, so that exploration compounds just like ingested sources do.

**Typed relationship** — A link between Knowledge Objects with a declared meaning (for example `depends_on`, `derived_from`, `part_of`). Typed relationships are what the graph and retrieval layers traverse.
