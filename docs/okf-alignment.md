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
- Each file is a **concept** — one thing — identified by its path.
- Each concept has YAML frontmatter for structured fields (`type`, `title`, `description`, `resource`, `tags`, `timestamp`) and a markdown body for everything else.
- Concepts link to each other with ordinary markdown links, forming a graph.
- Optional reserved files `index.md` and `log.md` help navigation and history.

OKF is deliberately minimal: it **requires exactly one field, `type`**, and leaves everything else to the producer. Its design goals are that anyone can produce it without an SDK, anyone can consume it without an integration, and it survives moving between systems. Crucially, Google framed it as *"a format, not another service"* and invited the community to build producers and consumers around it. The spec and reference tools are open on GitHub.

## How OpenKOS uses OKF

OpenKOS **adopts OKF as its storage and interchange layer**. A Knowledge Object in OpenKOS *is* an OKF concept document. This has concrete consequences:

- **We dogfood the format.** OpenKOS's own design documents use the OKF frontmatter field set.
- **We add a thin, compatible layer.** OKF requires only `type`; OpenKOS adds recommended fields for provenance and freshness and a recommended type/relation vocabulary. Everything we add lives as ordinary frontmatter and links, so a bundle always **degrades gracefully** to conformant OKF — strip the OpenKOS extras and any other OKF tool can still read it.
- **We commit to conformance.** OKF conformance is a tested property, not an aspiration. Output is always a valid bundle.
- **We interoperate.** Because bundles are portable, OpenKOS can consume bundles produced by other tools (including Google's reference producers) and export bundles for others to consume. Full import/export lands in MVP 3.

The mapping between OKF and the OpenKOS layer is specified in detail in [`knowledge-object-model.md`](knowledge-object-model.md).

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

- Open Knowledge Format — announcement and spec (Google Cloud, June 2026): <https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing>
- OKF repository: <https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf>
- LLM Wiki pattern (Andrej Karpathy): <https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f>
