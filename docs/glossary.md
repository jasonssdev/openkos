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

<a id="bundle-okf-bundle"></a>**Bundle (OKF bundle)** — A directory of markdown concept documents that together form a knowledge base. The bundle is the unit of storage and interchange; it is plain files, portable to any tool. In OpenKOS it lives at `bundle/` inside a [Workspace](#workspace) and contains concept documents and nothing else — no sources, no config — so it stays conformant. Conformance is not the same as being shareable as-is, though: the engine keeps durable sidecars under `bundle/.state/`, and the merge ledger among them holds the verbatim pre-merge bytes of every absorbed object, `confidential` ones included. Their `.ledger.okf` suffix keeps them out of every `*.md` walk, so conformance holds by construction; a copy of `bundle/` handed to someone else still carries them. See [Open Knowledge Format](#open-knowledge-format-okf).

<a id="canonical-layer"></a>**Canonical layer** — The durable part of an OpenKOS installation: the OKF bundle (markdown + frontmatter), the immutable raw sources, git history, and the SQLite operational store. It is the source of truth and is meant to outlive any tool. Contrast with the [Derived layer](#derived-layer).

<a id="compiler--compile"></a>**Compiler / compile** — The part of the engine (and the process) that turns a raw source into Knowledge Objects: extraction, typing, linking, freshness classification, and provenance. It is what `ingest` runs.

<a id="concept--concept-document"></a>**Concept / concept document** — OKF's name for a single knowledge file: one markdown file with YAML frontmatter representing one thing. In OpenKOS a concept document is the physical form of a [Knowledge Object](#knowledge-object-ko).

<a id="concept-id"></a>**Concept ID** — An object's identity, defined by OKF as its file path within the bundle with the `.md` suffix removed: `concepts/stoicism.md` has the concept ID `concepts/stoicism`. OpenKOS adopts this definition rather than adding an identifier of its own — there is no `id` field. Moving a file changes its ID; git records the rename, and OKF tolerates links to a moved target.

<a id="config-openkosyaml"></a>**Config (`openkos.yaml`)** — A per-bundle configuration file holding the engine's settings for that bundle: the local chat and embedding models, review mode, default sensitivity, the per-type birth-sensitivity offsets (`type_sensitivity_defaults`), the confidential local exemption, the freshness window, and per-tier/per-type volatility overrides. Structured YAML, owned by the engine and edited by the user. Distinct from the [Operating manual](#operating-manual-agentsmd).

<a id="conformance-okf"></a>**Conformance (OKF)** — The three rules of OKF §11: every non-reserved `.md` file has parseable YAML frontmatter; every frontmatter block has a non-empty `type`; and `index.md` / `log.md` follow their prescribed structure when present. Everything else is soft guidance — consumers must not reject a bundle over unknown types, extra keys, broken links, or a missing index. Distinct from the [Lint](#lint), which is OpenKOS's separate opinion about quality.

<a id="consumer"></a>**Consumer** — Any tool that reads and reasons over an OKF bundle (a viewer, a search index, an agent). OpenKOS is both a consumer and a [Producer](#producer).

<a id="context-assembly"></a>**Context assembly** — The retrieval step that gathers the most relevant concepts (and their citations) into the model's context — to answer a query, or to reconcile against what already exists during ingest.

<a id="continuant--occurrent"></a>**Continuant / occurrent** — The foundational split behind the object types: continuants *persist* through time (Person, Organization, Concept, Entity, Place), while occurrents *happen* in time (Event, Procedure). Borrowed from upper ontologies to ground the type vocabulary.

<a id="decision-sidecar"></a>**Decision sidecar** — A durable record of an operator's ruling on a pending [Finding](#finding) (for example, that two objects are the same, or that a flagged contradiction is not one), stored as `bundle/.state/decisions/<id>.decisions.okf` ([ADR-0014](adr/0014-durable-pending-work-stores.md)). It is irreplaceable human judgment, so it is canonical and versioned with the bundle rather than derived. A finding with no ruling in force is *pending work*.

<a id="derived-layer"></a>**Derived layer** — The rebuildable part of an installation: vector indexes and graph projections. Because it can always be reconstructed from the [Canonical layer](#canonical-layer), the engines behind it are swappable and never a lock-in.

<a id="egress"></a>**Egress** — Content leaving the machine: sent to an LLM or embedding backend that is not local, or returned through the MCP surface. `confidential` objects are withheld at these boundaries by fail-closed checks (`sensitivity.py`); printing to your own terminal is not egress. See [Sensitivity](#sensitivity).

<a id="entity-resolution"></a>**Entity resolution** — Deciding when two mentions refer to the same object, and merging duplicates, so the graph stays clean. A hard part of extraction, kept reviewable rather than silently automatic.

<a id="fast-fact"></a>**Fast fact** — A fact that can change within days (a live count, a balance, a status). Fast facts belong in their home system; the bundle should point at them rather than copy them. Contrast with [Slow fact](#slow-fact).

<a id="filter-first-retrieval"></a>**Filter-first retrieval** — A retrieval strategy where lexical search (FTS5) and the graph narrow the candidate set first, and vector ranking is applied only to that small set — keeping search fast even with millions of vectors.

<a id="finding"></a>**Finding** — A machine-computed verdict about the bundle (a suspected contradiction, an identity match, a decision revision) persisted in `.openkos/findings.db` with per-row input digests ([ADR-0014](adr/0014-durable-pending-work-stores.md)). A finding is a verdict rather than an index projection, so recreating one costs model calls; it stays *pending* until an operator rules on it in a [Decision sidecar](#decision-sidecar).

<a id="freshness"></a>**Freshness** — The temporal validity of a fact: whether it is still true *now*. See [Freshness class](#freshness-class).

<a id="freshness-class"></a>**Freshness class** — The category assigned to a fact based on how it behaves over time. Every fact must be one of three: [Timeless](#timeless), [Snapshot](#snapshot), or [Pointer](#pointer). The class determines how tooling (the lint) treats it.

<a id="high-water-mark-sensitivity"></a>**High-water-mark (sensitivity)** — The rule that a derived object is at least as sensitive as the most sensitive source it was compiled from; sensitivity propagates upward along the provenance chain.

<a id="indexmd"></a>**index.md** — A catalog file that lists the bundle's concepts with short summaries, used for navigation and index-first retrieval. Defined by OKF as an optional, reserved filename.

<a id="ingest"></a>**Ingest** — The operation of compiling a raw source into the bundle: reading it, writing a Source concept and the derived concepts a selector judge keeps from what extraction proposed, and recording provenance and log entries. (Automatically revising *related, existing* concepts during ingest remains a later capability.)

<a id="insight"></a>**Insight** — A filed synthesis: an answer a model produced over the bundle at answer time, written back by `query --save`. It depends on the mutable bundle rather than on an immutable source, so it defaults to the volatile tier and is down-weighted in retrieval. See the [Knowledge Object model](knowledge-object-model.md).

<a id="knowledge-graph"></a>**Knowledge graph** — The network formed by concepts and the markdown links between them. Richer than the folder hierarchy; traversed during retrieval. The links themselves are untyped, as OKF defines them — the kind of relationship lives in the prose beside each link. See [Typed relationship](#typed-relationship) for the OpenKOS layer that adds meaning on top, shipped in MVP 2.

<a id="knowledge-object-ko"></a>**Knowledge Object (KO)** — The fundamental unit of knowledge in OpenKOS: an OKF concept document plus a thin OpenKOS layer (provenance chain, freshness class, recommended type vocabulary). See [`knowledge-object-model.md`](knowledge-object-model.md).

<a id="lint"></a>**Lint** — The operation that checks the health of the bundle, mechanically (no LLM): stale `as of` stamps (older than the document's volatility-resolved window, shipped in MVP 2), orphan pages, dangling references and dangling provenance, unextracted sources, and sensitivity-coverage gaps. Contradiction detection is a separate LLM verb (`openkos contradictions`), also shipped in MVP 2. Enforces the freshness discipline automatically. It is **not** a conformance checker: it reports OpenKOS's opinion about knowledge health, never OKF's verdict about validity. See [Conformance](#conformance-okf).

<a id="living-document"></a>**Living document** — A concept document that is rewritten as new sources arrive. Concepts are living; raw sources are not. History is preserved through git and `log.md`, so "mutable head, immutable history."

<a id="llm-wiki-pattern"></a>**LLM Wiki pattern** — Andrej Karpathy's idea that a language model should *incrementally build and maintain* a persistent, interlinked knowledge base between you and your sources, rather than re-retrieving raw documents on every query. The pattern OpenKOS implements.

<a id="local-first"></a>**Local-first** — Software that runs on your machine and works offline, keeping your data under your control. The cloud is optional, never required.

<a id="logmd"></a>**log.md** — An append-only, chronological record of what happened in the bundle (ingests, queries, reconciliations). Defined by OKF as an optional, reserved filename.

<a id="mcp-model-context-protocol"></a>**MCP (Model Context Protocol)** — A standard for exposing tools to AI agents. OpenKOS exposes the bundle through an MCP server (`openkos mcp`) so agents can query and navigate it.

<a id="merge-ledger"></a>**Merge ledger** — The sidecar at `bundle/.state/ledger/<id>.ledger.okf` recording a merge: the verbatim pre-merge bytes of each absorbed object and the catalog delta, which is what makes every merge reversible ([ADR-0002](adr/0002-reversible-merge-ledger.md), [ADR-0013](adr/0013-relocate-merge-ledger-to-bundle-state.md), [ADR-0017](adr/0017-merge-ledger-stores-the-catalog-delta.md)).

<a id="okfversion"></a>**okf_version** — A frontmatter field declaring the OKF version a bundle targets (OpenKOS writes `"0.2"`). It lives in the bundle-root `index.md` — the one place OKF permits frontmatter in a reserved file — and exists so a future consumer knows exactly which revision of the spec the bundle was written against.

<a id="open-knowledge-format-okf"></a>**Open Knowledge Format (OKF)** — A vendor-neutral open specification, published by Google Cloud in June 2026, that formalizes the LLM Wiki pattern into a portable format: a directory of markdown concepts with YAML frontmatter, requiring only a `type` field. OpenKOS adopts OKF as its storage and interchange layer. See [`okf-alignment.md`](okf-alignment.md).

<a id="operating-manual-agentsmd"></a>**Operating manual (`AGENTS.md`)** — A per-bundle markdown file, following the vendor-neutral `AGENTS.md` convention, that tells an AI agent how the bundle is organized and what conventions to follow when operating on it (ingesting, querying, maintaining). Prose instructions — the disciplined-maintainer layer of the LLM Wiki pattern. Distinct from the structured [Config](#config-openkosyaml).

<a id="pointer"></a>**Pointer** — A freshness class for facts whose current value matters and changes fast: instead of the value, store where the truth lives (a link), optionally with the last observed value and a stamp. One of the three legal forms of a fact.

<a id="producer"></a>**Producer** — Any tool that writes an OKF bundle. OpenKOS produces bundles by compiling your text; other producers exist (for example, agents that document databases).

<a id="provenance--provenance-chain"></a>**Provenance / provenance chain** — The recorded link between a derived Knowledge Object and the immutable raw source(s) it was compiled from. Provenance is what makes retrieval explainable: any answer can be traced back to its origin.

<a id="query"></a>**Query** — The operation of asking a question against the bundle and getting a cited answer, assembled from relevant concepts.

<a id="raw-source"></a>**Raw source** — An original input file (article, notes, PDF), stored in `raw/` under its own name and extension. Raw sources are **immutable**: OpenKOS reads from them but never rewrites them. They live outside the bundle, and each is represented inside it by a **Source concept** whose `resource` points back at the original — the one bridge from the bundle to the material it was compiled from.

<a id="reconstructibility"></a>**Reconstructibility** — The guarantee that every index, embedding, and graph projection can be rebuilt from the canonical layer. It is why derived engines are swappable and why no single dependency is a lock-in.

<a id="representation-not-truth"></a>**Representation, not truth** — The principle that OpenKOS stores how an individual understands and documents knowledge, not objective truth. It is not an epistemic authority: conflicting perspectives may coexist, each keeping its own context (source, assumptions, evidence). Distinct from freshness — the engine reconciles what is out of date, not what is genuinely contested.

<a id="sensitivity"></a>**Sensitivity** — An OpenKOS-layer label (`public`, `private`, or `confidential`) that governs what may cross a trust boundary: what an agent may read, what may be sent to a cloud model, and what is included in exports or sync. It is a disclosure policy, not encryption, and propagates along the provenance chain by a high-water-mark rule. Its floor is the workspace's `default_sensitivity` (`private` out of the box), raised at birth for any type listed in `type_sensitivity_defaults`, which ships empty — `Person: 1` is the recommended opt-in, and makes a person page `confidential` from the moment it is compiled. Whichever of the two is more restrictive wins.

<a id="slow-fact"></a>**Slow fact** — A fact stable for weeks, months, or years (how a system is built, who owns what, a decision and its reasoning). Slow facts are what a knowledge base exists to store. Contrast with [Fast fact](#fast-fact).

<a id="snapshot"></a>**Snapshot** — A freshness class for a dated observation. A snapshot never goes stale because it claims what was true *on a date*, not what is true now. One of the three legal forms of a fact.

<a id="stamp"></a>**Stamp** — A date marker such as `(as of 2026-07-14)`, optionally with a source, attached to a fast-changing fact so it cannot silently rot.

<a id="three-criteria-test"></a>**Three-criteria test** — The bar a type must pass to enter the canonical core: distinct structure, distinct relationships, and transversal recurrence across domains. If it fails, it belongs as a domain extension, a tag, or body structure — not a core type.

<a id="three-tier-classification"></a>**Three-tier classification** — The type-vocabulary model: a stable canonical core, optional shareable domain extensions, and personal emergent types coined per bundle.

<a id="timeless"></a>**Timeless** — A freshness class for facts that do not decay and need no date. One of the three legal forms of a fact.

<a id="tombstone"></a>**Tombstone** — A log entry left behind when an object is deleted, recording that it existed and was removed (except in a privacy purge), so deletion stays auditable. Shipped with the MVP 2 lifecycle: `forget` writes a tombstone into `log.md`, and git history still provides recovery.

<a id="two-output-rule"></a>**Two-output rule** — The practice that a good answer to a query can be filed back into the bundle as a new concept, so that exploration compounds just like ingested sources do.

<a id="typed-relationship"></a>**Typed relationship** — A link between Knowledge Objects with a declared meaning (for example `depends_on`, `derived_from`, `part_of`). Typed relationships are what the OpenKOS graph and retrieval layers traverse, and they ship with the MVP 2 graph (written by `openkos relate`). They are an **OpenKOS extension, not an OKF feature**: OKF links are untyped, and the kind of relationship is carried by the prose next to the link. The typing is layered on as an extra frontmatter key, so a plain OKF consumer still sees the untyped directed edges the spec promises it and loses nothing structural.

<a id="volatility-tier"></a>**Volatility tier** — One of `static`, `slow`, or `volatile`, assigned per object type by default and overridable per document with the optional `volatility` frontmatter key. It sets the stale window the freshness lint applies to that document; `static` is never flagged ([ADR-0007](adr/0007-volatility-taxonomy.md)).

<a id="workspace"></a>**Workspace** — The directory a user opens, versioned with git. It holds `raw/` (immutable sources, any extension), `bundle/` (the OKF [Bundle](#bundle-okf-bundle)), `openkos.yaml`, `AGENTS.md`, and the git-ignored `.openkos/`. Sources live *beside* the bundle rather than inside it because they are input material, not concepts — which keeps the bundle conformant by construction and lets sources keep their own filenames. `openkos init` creates the workspace *and* its git repository, `.gitignore`, and first commit, so versioning is never a manual step. By convention the first workspace is `~/knowledge`; one engine installation serves many workspaces.
