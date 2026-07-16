---
type: Reference
title: OpenKOS and OKF
description: How OpenKOS relates to the Open Knowledge Format — what it adopts, what it adds, and what it deliberately is not.
tags:
  - openkos
  - open-knowledge-format
  - okf
  - interoperability
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-14T16:00:00Z
sensitivity: public
---

# OpenKOS and OKF

One of the most important things to understand about OpenKOS is that it does **not** invent a new knowledge format. It stands on existing, open work and contributes the piece that was missing. This document explains exactly how the two fit together.

## The two layers

Think of the space as two layers, each answering a different question:

| Layer | Question it answers | Who defines it |
| --- | --- | --- |
| **OKF** — Open Knowledge Format | *How is knowledge written?* | Google Cloud (open spec) |
| **OpenKOS** | *What produces, consumes, and maintains it — locally, for one person?* | This project |

OKF is the format. OpenKOS is the local-first engine that speaks it.

## What OKF is

The **Open Knowledge Format**, published by Google Cloud in June 2026, is a vendor-neutral open specification that formalizes the LLM Wiki pattern into a portable, interoperable format. In one screen:

- A **bundle** is a directory of markdown files.
- Each file is a **concept** — one thing — whose **Concept ID is its path** within the bundle with `.md` removed (§2). Identity comes free from the filesystem; there is no separate identifier.
- Each concept has YAML frontmatter for structured fields (`type`, `title`, `description`, `resource`, `tags`, `timestamp`) and a markdown body for everything else.
- Concepts link to each other with ordinary markdown links, forming a graph. Those links are **untyped** (§5.3): the link asserts that a relationship exists; the prose around it says what kind.
- Reserved files `index.md` and `log.md` help navigation and history. They are not concept documents and carry no frontmatter — the single exception being the bundle-root `index.md`, which may carry `okf_version` and nothing else (§11).
- Conventional body headings (`# Schema`, `# Examples`, `# Citations`) have defined meaning when present (§4.2, §8).

OKF is deliberately minimal: it **requires exactly one field, `type`**, and leaves everything else to the producer. Its design goals are that anyone can produce it without an SDK, anyone can consume it without an integration, and it survives moving between systems. Crucially, Google framed it as *"a format, not another service"* and invited the community to build producers and consumers around it. The spec and reference tools are open on GitHub.

### Conformance is a short, precise list

OKF §9 defines conformance in exactly three rules. A bundle is conformant if:

1. Every non-reserved `.md` file in the tree contains a parseable YAML frontmatter block.
2. Every frontmatter block contains a non-empty `type` field.
3. Every reserved filename (`index.md`, `log.md`) follows the structure of §6 and §7 when present.

Everything else is soft guidance, and the spec is emphatic that consumers **MUST NOT** reject a bundle for missing optional fields, unknown `type` values, unknown extra frontmatter keys, broken cross-links, or missing `index.md` files. Two consequences matter for OpenKOS.

First, rule 1 applies to *every* non-reserved `.md` file — including files that are not knowledge at all. An ingested markdown source carries no frontmatter, and adding some would violate immutability. Rather than work around that, OpenKOS takes the rule as a signal that it is pointing at something real: **a bundle is a bundle of concepts, and raw sources are not concepts.** So a workspace keeps `raw/` *beside* the bundle rather than inside it, and `bundle/` contains concept documents and nothing else. Conformance then holds by construction — nothing a user drops into `raw/`, by hand and bypassing the CLI, can break it — and sources keep their own names and extensions. The layout is described in [`architecture.md`](architecture.md).

Second — and this is easy to get backwards — **the OpenKOS lint is not a conformance checker.** §5.3 says a broken link "is not malformed; it may simply represent not-yet-written knowledge," and §9 forbids rejecting bundles over it. When `openkos lint` flags an orphan page or a stale `as of` stamp, it is expressing *OpenKOS's opinion about knowledge health*, not OKF's verdict about validity. A bundle can fail every lint check we have and still be perfectly conformant. We keep the two vocabularies separate on purpose: conformance is the spec's, quality is ours.

## How OpenKOS uses OKF

OpenKOS **adopts OKF as its storage and interchange layer**. A Knowledge Object in OpenKOS *is* an OKF concept document. This has concrete consequences:

- **We dogfood the format.** OpenKOS's own design documents use the OKF frontmatter field set.
- **We add a thin, compatible layer.** OKF requires only `type`; OpenKOS adds recommended fields for provenance and freshness and a recommended type/relation vocabulary. Everything we add lives as ordinary frontmatter and links, so a bundle always **degrades gracefully** to conformant OKF — strip the OpenKOS extras and any other OKF tool can still read it. §4.1 sanctions this directly: producers MAY add keys, and consumers SHOULD preserve unknown keys when round-tripping.
- **We adopt its definitions rather than restate them.** Identity is the Concept ID (the path), not an invented `id` field. Citations use the `# Citations` heading. Links are bundle-relative. Where OKF has already decided something, we do not decide it again.
- **We commit to conformance.** OKF conformance is a tested property, not an aspiration: the three rules of §9 are checked, and output is always a valid bundle.
- **We declare the version we target.** Bundles carry `okf_version: "0.1"` in the root `index.md`, the one place §11 permits it.
- **We interoperate.** Because bundles are portable, OpenKOS can consume bundles produced by other tools (including Google's reference producers) and export bundles for others to consume. Full import/export lands in MVP 3. A concrete proof available today: any bundle OpenKOS produces can be opened in the visualizer bundled with the OKF repository, which is itself a reference *consumer* — a graph view of your knowledge, for free, from a tool that knows nothing about OpenKOS.

The mapping between OKF and the OpenKOS layer is specified in detail in [`knowledge-object-model.md`](knowledge-object-model.md).

### Where the spec and its reference producer already disagree

Worth knowing before relying on either, and verified against the bundles checked into the OKF repository rather than inferred:

**Citations.** §8 prescribes a numbered list — `[1] [title](url)`. Google's own published bundles do not use it; they emit a bare bullet list of URLs under `# Citations`. OpenKOS follows the spec's form, which is both more useful (a citation with a title is readable) and the letter of the standard — but a consumer written against Google's output, rather than against the text, may not recognize it.

**Link style.** §5.1 recommends bundle-relative links beginning with `/`, and OpenKOS uses them. Google's bundles are inconsistent with themselves here: their root `index.md` links `/datasets/`, while `tables/index.md` links `events_.md` — plain relative. Both forms are legal (§5.1, §5.2), so neither is wrong; the divergence just means "what OKF links look like" has no single answer in practice yet.

That second point carries a real cost we accept knowingly: a `/`-rooted link does **not** resolve on GitHub's file viewer, which reads it as a site-absolute path. A bundle browsed on GitHub will show those links as dead. We keep the spec's recommended form anyway, because it is the one that survives a document moving within its subdirectory, and because the engine, Obsidian, and any consumer that resolves from the bundle root all handle it correctly. It is a rendering cost in one viewer, weighed against link stability everywhere else — and if the ecosystem converges on relative links, this is a mechanical change behind the OKF adapter.

The lesson generalizes: **conformance is defined by the text, but interoperability is defined by what other tools actually do.** We track both, and where they part we say which one we followed and why.

## Building on a v0.1 draft — the risk, stated plainly

OKF is published as **version 0.1, marked Draft**, and §11 reserves the right for a major version to make breaking changes, including renaming required fields and changing reserved filenames. The repository that hosts it also carries the standard notice that it is not an official Google product. It has real traction — thousands of stars, active issues and pull requests — but it is young, and betting a project's storage layer on a draft is a genuine risk rather than a formality.

We take the bet, for the reason the whole project rests on: the value of a knowledge format is how many tools speak it, and a young open spec with momentum beats a bespoke format with none. But we hedge it in three concrete ways. Bundles **declare `okf_version`**, so a future consumer knows exactly what it is reading. The **OKF adapter is isolated in one module** — reading and writing the format is one seam, not a concern smeared across the engine — so a spec revision is one module's problem and a contained change, not a rewrite. And the **canonical layer is plain markdown plus git**, so even in the worst case where OKF changes shape or stalls entirely, no user's knowledge is trapped: it is still their files, on their disk, readable without us.

## Freshness: an OpenKOS discipline on top of OKF

OKF says how knowledge is written, but not whether a stored fact is still true. OpenKOS adds that: a **freshness discipline** that classifies every fact as timeless, snapshot, or pointer, stamps volatile facts with an `as of` marker, and enforces it with a lint. It builds naturally on OKF's `timestamp` field and adds a `freshness` field in the OpenKOS layer. The full model is described in [`knowledge-object-model.md`](knowledge-object-model.md).

## Where OpenKOS fits — and what it deliberately is not

Google shipped OKF with reference implementations aimed at **enterprise cloud data** (an agent that documents BigQuery tables, and a visualizer). That leaves a clear gap: nobody has built the **local-first, private, personal** producer-consumer-runtime for individuals. OpenKOS fills exactly that gap.

Just as importantly, here is what OpenKOS is **not**:

- It is **not a competing format.** Inventing yet another knowledge format in this niche would fragment the ecosystem and forfeit interoperability — our core advantage. We adopt OKF.
- It is **not a cloud service or catalog.** It is an engine that runs on your machine.
- It is **not tied to any single app, model, or vendor.**

Building on OKF is a deliberate strategy: the value of a knowledge format comes from how many tools speak it. By speaking the same language as the rest of the ecosystem, OpenKOS makes your knowledge interoperable from day one, while contributing the local-first engine that the ecosystem was missing.

## References

- **OKF v0.1 specification** (the normative source; section numbers in this document refer to it): <https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md>
- Open Knowledge Format — announcement (Google Cloud, June 2026): <https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing>
- OKF repository, including the reference producer and visualizer: <https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf>
- LLM Wiki pattern (Andrej Karpathy): <https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f>
