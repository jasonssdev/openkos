# OpenKOS

[![PyPI](https://img.shields.io/pypi/v/openkos)](https://pypi.org/project/openkos/)
[![Python](https://img.shields.io/pypi/pyversions/openkos)](https://pypi.org/project/openkos/)
[![CI](https://github.com/jasonssdev/openkos/actions/workflows/ci.yml/badge.svg)](https://github.com/jasonssdev/openkos/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/jasonssdev/openkos/blob/main/LICENSE)

**Open Knowledge Orchestration System** — a local-first engine for the Open Knowledge Format.

OpenKOS turns your scattered text into a living, portable knowledge base your AI agents can actually use — compiled once, kept current, and stored as plain [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf) files so it is never locked to any app, model, or vendor.

> **Project status: alpha.** The Compiler and the Graph-and-Memory arcs (MVP 1 and MVP 2) are complete and shipped. The API may still change between releases, but OpenKOS is published and installable now. Early contributors and feedback are welcome — see [Contributing](#contributing).

---

## Quickstart

Five steps from a fresh machine to your first cited answer. Everything runs on your computer: no accounts, no API keys, nothing leaves your machine.

**1 · Install [Ollama](https://ollama.com)** — the local AI runtime OpenKOS talks to. Download it, open it once, and it stays running in the background.

**2 · Pull the two default models** (one time, ~6 GB total):

```bash
ollama pull qwen3:8b   # chat model — extraction and answers (~5 GB)
ollama pull bge-m3     # embedding model — semantic search (~1.2 GB)
```

> These two are all curation needs out of the box. For naming relations during curation there is an **optional measured upgrade** (`ollama pull gemma2:27b`, **15.6 GB**) that nearly doubles relation-type accuracy — opt in later with `models: {edge_typing: gemma2:27b}` in `openkos.yaml`; `openkos doctor` and `curate` both point at it.

**3 · Install the engine** (needs Python 3.12+ and [git](https://git-scm.com)):

```bash
uv tool install openkos   # or: pipx install openkos — or: pip install openkos
openkos --version         # check what you actually got
```

**Check that version.** If it does not match the badge at the top of this page, your package index is serving an older release. Install from the repository instead — same engine, current code:

```bash
uv tool install --force git+https://github.com/jasonssdev/openkos
openkos --version
```

That command is also how you track unreleased `main` at any time.

**4 · Check the setup before anything can fail:**

```bash
openkos doctor
```

Every check should pass except *Workspace initialized* — you haven't created one yet. Anything that fails prints the command that fixes it. `doctor` is also the first thing to run whenever something misbehaves later.

**5 · Create a knowledge base and run the loop:**

```bash
mkdir ~/knowledge && cd ~/knowledge
openkos init                          # picks your models, scaffolds the workspace
openkos ingest ./meeting-notes.txt    # compile a source — a file, a folder, or a glob
openkos query "what did we decide?"   # an answer with citations, from your own knowledge
```

From here, `openkos next` always tells you the one thing worth doing, and `openkos --help` lists every command, grouped. The full reference is [`docs/cli.md`](https://github.com/jasonssdev/openkos/blob/main/docs/cli.md); the end-to-end experience is [`docs/user-journey.md`](https://github.com/jasonssdev/openkos/blob/main/docs/user-journey.md).

**What `init` set up for you.** `raw/` holds your immutable sources; `bundle/` is the pure-OKF knowledge base — plain markdown you can open in Obsidian, VS Code, or GitHub, and take anywhere; `openkos.yaml` is the engine config. The folder is also a git repository **you never have to operate**: every command commits its own changes, and `git log` / `git diff` / `git revert` are always there for inspection and undo.

---

## The problem

Your AI assistant forgets everything between sessions, so you re-explain the same context every time and the insights you build together disappear into chat history. Meanwhile your notes pile up in folders nobody keeps current. Two powerful things — your knowledge and your models — sit side by side, disconnected.

Retrieval (RAG) doesn't fix this: it re-reads your raw documents on every question and rediscovers everything from scratch. Nothing accumulates. The cross-references are never drawn, the contradictions never reconciled.

## The idea

Instead of retrieving from raw sources every time, an LLM can *incrementally build and maintain* a persistent, interlinked knowledge base that sits between you and your sources — Andrej Karpathy's [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) pattern. Knowledge is compiled once and then kept current. It compounds.

In June 2026 Google Cloud published a vendor-neutral specification for that pattern: the **Open Knowledge Format (OKF)** — a directory of markdown files with YAML frontmatter, portable across any tool. It is young (v0.1, still a draft) but open, minimal, and gaining adoption. Google's framing was: *"What's missing is a format, not another service,"* and they invited the community to build producers and consumers.

**OpenKOS is that producer and consumer, built for individuals and running entirely on your machine.**

## Before / after

| | Without OpenKOS | With OpenKOS |
| --- | --- | --- |
| Asking your AI about your own notes | It re-reads raw files every time | It answers from a compiled, cited knowledge base |
| New source | Piles up unread | Compiled into a Source plus typed knowledge objects, each linked to its source |
| Provenance | "Where did this come from?" is a guess | Every object links back to its immutable source |
| Facts that change | Old claims quietly rot | Freshness stamps keep the base honest over time |
| Portability | Trapped in one app | Plain OKF files — open in Obsidian, VS Code, GitHub, anything |
| Privacy | Your knowledge leaves your machine | Local-first, offline-capable, local models |

## Philosophy

- **Local-first and private by default.** Runs on your machine, works offline, built for local models. The cloud is optional, never required.
- **Standard-aligned, not bespoke.** We adopt OKF rather than invent a format, and adopt its definitions rather than restate them. An open, vendor-neutral specification is the most agnostic choice there is.
- **Living knowledge, honest over time.** Sources are immutable; concept documents evolve as you learn; fast-changing facts carry freshness stamps so nothing silently becomes a lie.
- **The human curates; the engine maintains.** You source, explore, and ask. OpenKOS does the bookkeeping — extraction, linking, freshness, indexing.
- **Reconstructible and explainable.** Every index, embedding, and graph rebuilds from the canonical bundle plus sources. Answers always cite.

## How OpenKOS relates to the ecosystem

We build on the shoulders of prior work rather than competing with it:

- **[Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)** — the seminal pattern. OpenKOS is a concrete engine that instantiates it.
- **[Open Knowledge Format](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing)** (Google Cloud) — the standard we store and exchange in. Google's reference stack targets enterprise cloud data (BigQuery); OpenKOS is its local-first, personal counterpart.
- **Obsidian-based tools** (obsidian-mind, obsidian-second-brain) — excellent, but tied to Obsidian and packaged as prompt/skill conventions. OpenKOS is a standalone, app-agnostic engine whose output is portable OKF.

The wedge, in one line: **the local-first, personal producer-consumer-runtime for OKF that nobody else has built.**

## Roadmap at a glance

OpenKOS ships in three MVP arcs, each usable on its own. Full detail in [`docs/roadmap.md`](https://github.com/jasonssdev/openkos/blob/main/docs/roadmap.md).

- **MVP 1 — The Compiler. (Complete.)** The Karpathy loop, locally, over text: ingest → OKF concepts with provenance → cited query → freshness lint. Useful in an afternoon.
- **MVP 2 — The Graph and Memory. (Complete.)** Entity/relationship extraction and reversible merge, a typed knowledge graph (an OpenKOS layer over OKF's untyped links — other tools still read the bundle fine), hybrid retrieval (lexical and semantic, rank-fused), a fail-closed sensitivity filter (confidential concepts never leave the machine — held back from any backend that is not verifiably local), a guided curation loop, reference-aware `forget` plus an irreversible `purge` (right-to-be-forgotten), and answers that file back into the base (the two-output rule).
- **MVP 3 — The Runtime and Interoperability.** An MCP server and APIs so agents use OpenKOS as durable memory; full OKF import/export with the wider ecosystem.

Beyond that: a desktop app, graph visualization, richer memory, and federation — explored only after the MVPs prove out with real users.

## Documentation

- [`docs/vision.md`](https://github.com/jasonssdev/openkos/blob/main/docs/vision.md) — vision, philosophy, and positioning
- [`docs/philosophy.md`](https://github.com/jasonssdev/openkos/blob/main/docs/philosophy.md) — the foundational essay: what knowledge is and why OpenKOS matters
- [`docs/knowledge-object-model.md`](https://github.com/jasonssdev/openkos/blob/main/docs/knowledge-object-model.md) — how knowledge is represented (OKF + the OpenKOS layer)
- [`docs/roadmap.md`](https://github.com/jasonssdev/openkos/blob/main/docs/roadmap.md) — the MVP roadmap
- [`docs/ideas.md`](https://github.com/jasonssdev/openkos/blob/main/docs/ideas.md) — ideas under consideration, none of them committed
- [`docs/tech_stack.md`](https://github.com/jasonssdev/openkos/blob/main/docs/tech_stack.md) — technology choices
- [`docs/architecture.md`](https://github.com/jasonssdev/openkos/blob/main/docs/architecture.md) — repository and bundle structure, and source versioning
- [`docs/okf-alignment.md`](https://github.com/jasonssdev/openkos/blob/main/docs/okf-alignment.md) — how OpenKOS relates to OKF
- [`docs/glossary.md`](https://github.com/jasonssdev/openkos/blob/main/docs/glossary.md) — definitions of the core vocabulary
- [`docs/faq.md`](https://github.com/jasonssdev/openkos/blob/main/docs/faq.md) — frequently asked questions
- [`docs/user-journey.md`](https://github.com/jasonssdev/openkos/blob/main/docs/user-journey.md) — the end-to-end user experience
- [`docs/testing.md`](https://github.com/jasonssdev/openkos/blob/main/docs/testing.md) — manual end-to-end testing walkthrough
- [`docs/cli.md`](https://github.com/jasonssdev/openkos/blob/main/docs/cli.md) — the command-line reference
- [`docs/brand.md`](https://github.com/jasonssdev/openkos/blob/main/docs/brand.md) — visual identity: isotype, wordmark, palette, typography
- [`docs/adr/`](https://github.com/jasonssdev/openkos/blob/main/docs/adr/) — architecture decision records

## Contributing

OpenKOS is early, which is the best time to shape it. The clearest entry points are the "community can contribute" notes under the current MVP in the [roadmap](https://github.com/jasonssdev/openkos/blob/main/docs/roadmap.md). Please open an issue to discuss anything larger than a small change before sending a PR, so we can make sure it fits and can be merged.

See [CONTRIBUTING.md](https://github.com/jasonssdev/openkos/blob/main/CONTRIBUTING.md) for how to get involved and [CODE_OF_CONDUCT.md](https://github.com/jasonssdev/openkos/blob/main/CODE_OF_CONDUCT.md) for community standards. Maintainers: see [MAINTAINERS.md](https://github.com/jasonssdev/openkos/blob/main/MAINTAINERS.md) for how contributions are reviewed and decided.

## License

Apache License 2.0 — see [LICENSE](https://github.com/jasonssdev/openkos/blob/main/LICENSE).
