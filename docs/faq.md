---
type: Reference
title: OpenKOS FAQ
description: Answers to the questions newcomers most often ask about OpenKOS.
tags:
  - openkos
  - faq
  - reference
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-14T14:00:00Z
sensitivity: public
---

# Frequently Asked Questions

## What is OpenKOS, in one sentence?

An open-source, local-first engine that turns your scattered text into a living, portable knowledge base your AI agents can actually use — stored as plain [Open Knowledge Format](okf-alignment.md) files so it is never locked to any app, model, or vendor.

## How is this different from RAG, NotebookLM, or uploading files to a chatbot?

Those systems retrieve from your raw documents at query time and rediscover everything on every question — nothing accumulates. OpenKOS follows the LLM Wiki pattern instead: it *compiles* your sources once into a structured knowledge base and then keeps it current. The cross-references, contradictions, and synthesis are built up over time rather than re-derived each time you ask.

## How is this different from Obsidian, Notion, or Logseq?

Those are note-taking apps you write in. OpenKOS is not a note app — it is an engine that compiles and maintains knowledge for you, and it stores everything as plain files. Because the output is standard markdown, you can browse it *in* Obsidian, VS Code, or GitHub if you like. OpenKOS does not replace your editor; it produces knowledge your editor can open.

## How is this different from the Obsidian LLM tools (obsidian-mind, obsidian-second-brain)?

Those are excellent, but they live inside Obsidian and are packaged as prompt/skill conventions for a specific coding agent. OpenKOS is a standalone, app-agnostic engine whose output is portable OKF — not tied to Obsidian, not tied to one agent, and usable from a plain command line.

## How does this relate to Google's Open Knowledge Format? Are you competing with Google?

No — we build on it. Google published OKF as an open, vendor-neutral *format* and explicitly said the missing piece is "a format, not another service," inviting others to build producers and consumers. OpenKOS is exactly that: a producer and consumer of OKF. Google's reference stack targets enterprise cloud data (BigQuery); OpenKOS is the local-first, personal counterpart. See [`okf-alignment.md`](okf-alignment.md).

## Why local-first? Can I use the cloud?

Local-first means it runs on your machine and works offline, so your knowledge never has to leave your computer. That is a privacy and ownership choice. You *can* use cloud models or sync if you want to — the cloud is an option OpenKOS supports, never a requirement it imposes.

## Is my data private?

Yes, by design. OpenKOS is built to run entirely locally with local model runtimes, so nothing needs to be sent anywhere. Your sources and your knowledge base are files on your disk that you own.

## Do I need to know AI or coding to use it?

You need to be comfortable running a command-line tool. You do not need to understand AI internals. The whole philosophy is "the human curates; the engine maintains" — you choose sources and ask questions; OpenKOS does the extraction, linking, and bookkeeping.

## What models does it use? Do I need API keys?

OpenKOS is designed for open-weight local models via runtimes like Ollama, so you do not need API keys or a paid account. The measured defaults are `qwen3:8b` for chat and `bge-m3` for embeddings (see the ADRs for how they were chosen), with one optional measured upgrade for relation naming (`gemma2:27b`, opt-in via `models:` in `openkos.yaml`). Model licences change, and OpenKOS does not vouch for them — check the vendor's terms for the release you pull, and prefer a permissive one. You may plug in other models if you prefer: the model is a config value behind an interface, so OpenKOS never blesses one.

## Why markdown? Why not a "real" database?

Markdown plus a small amount of structured metadata is human-readable, machine-readable, portable, and durable — it will still open in thirty years. Databases (SQLite, vector indexes) are used underneath as rebuildable caches, but the canonical knowledge is always plain files. This is what guarantees your knowledge outlives any particular tool.

## Will my knowledge be locked into OpenKOS?

No. That is the point of building on OKF. Your knowledge is a bundle of plain markdown files that any OKF-speaking tool can read, and that you can carry to any editor. If you stop using OpenKOS, you still have your complete knowledge base.

## Can I use it today?

Yes — it runs, though it is **alpha**: the API may still change between releases. The MVP 1 (compile-query-lint) and MVP 2 (typed graph, hybrid retrieval, entity resolution and reversible merge, the forget/purge lifecycle) arcs are complete; `openkos --help` lists every verb. OpenKOS is on PyPI — `pip install openkos` — or install from a source checkout to track the latest `main`. Early feedback and contributors are welcome — see [CONTRIBUTING.md](../CONTRIBUTING.md).

## How long does it take to ingest a document?

Longer than a web app, and the reason is structural rather than fixable: everything runs on your own machine, and the model has to read the document and write structured objects out of it. Roughly, on a recent laptop with the default model:

| Document | Pages | Time |
| --- | --- | --- |
| Short note | 2 | a minute or two |
| Meeting transcript | 15 | a few minutes |
| Chapter or report | 30 | several minutes |
| Long document | 100 | tens of minutes |

Cost tracks **model calls**, not file size directly, and a long document is split into windows of about 4,000 characters with one call per window — so the time grows roughly in proportion to length. `docs/cli.md` gives the exact call arithmetic if you want to predict a specific run.

Two things that are *not* wrong when you see them: a transcript is split earlier than an ordinary document (it is more prone to a failure mode at large window sizes), and a pause of more than five minutes mid-batch makes the next file slower, because Ollama unloads the model when it goes idle.

Progress is printed as it goes — the phase and the chunk being worked on — so a long run tells you it is moving. If it looks frozen, check that line before assuming it hung.

## How do I record that recurring meetings belong to one series?

Ingest each meeting note as usual — each becomes its own `Source` document, and adjudication correctly keeps the occurrences distinct. Then link them with the shipped relation verbs: ingest a short series note once (its `Source` document is the series anchor), and run `openkos relate sources/<occurrence> member_of sources/<series-anchor>` for each occurrence. The typed `member_of` edges land in each occurrence's `relations:` frontmatter and in the graph projection, so the series is a structural path — series anchor ← occurrences ← each meeting's extracted decisions and events (via their `derived_from` provenance) — not just a naming convention. Occurrences stay independent documents throughout: `merge`/`unmerge` never fuse them, and the relation is one `relate` away from being asserted or removed.

## What does "the human curates; the engine maintains" mean?

You are in charge of sourcing, exploring, and asking good questions. The engine does the tedious bookkeeping that makes a knowledge base actually useful — summarizing, cross-referencing, filing, checking freshness — that humans reliably abandon. Consequential changes stay reviewable, not silently automatic.

## What license is it? Is it free?

OpenKOS is licensed under the [Apache License 2.0](../LICENSE) and is free and open source. It is built to rely only on open-source, free technologies.

## How can I contribute?

See [CONTRIBUTING.md](../CONTRIBUTING.md) for how to get involved and [GOVERNANCE.md](../GOVERNANCE.md) for how decisions are made. Feedback on the design docs and on the shipped commands is a valuable contribution.
