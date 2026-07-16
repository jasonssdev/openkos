# OpenKOS

**Open Knowledge Orchestration System** — a local-first engine for the Open Knowledge Format.

OpenKOS turns your scattered text into a living, portable knowledge base your AI agents can actually use — compiled once, kept current, and stored as plain [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf) files so it is never locked to any app, model, or vendor.

> **Project status: pre-alpha.** OpenKOS is being designed in the open. The vision, architecture, and roadmap are here; MVP 1 is in progress. There is no installable release yet. Early contributors and feedback are welcome — see [Contributing](#contributing).

---

## The problem

Your AI assistant forgets everything between sessions, so you re-explain the same context every time and the insights you build together disappear into chat history. Meanwhile your notes pile up in folders nobody keeps current. Two powerful things — your knowledge and your models — sit side by side, disconnected.

Retrieval (RAG) doesn't fix this: it re-reads your raw documents on every question and rediscovers everything from scratch. Nothing accumulates. The cross-references are never drawn, the contradictions never reconciled.

## The idea

Instead of retrieving from raw sources every time, an LLM can *incrementally build and maintain* a persistent, interlinked knowledge base that sits between you and your sources — Andrej Karpathy's [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) pattern. Knowledge is compiled once and then kept current. It compounds.

In June 2026 Google Cloud turned that pattern into a real, vendor-neutral standard: the **Open Knowledge Format (OKF)** — a directory of markdown files with YAML frontmatter, portable across any tool. Google's framing was: *"What's missing is a format, not another service,"* and they invited the community to build producers and consumers.

**OpenKOS is that producer and consumer, built for individuals and running entirely on your machine.**

## Before / after

| | Without OpenKOS | With OpenKOS |
| --- | --- | --- |
| Asking your AI about your own notes | It re-reads raw files every time | It answers from a compiled, cited knowledge base |
| New source | Piles up unread | Compiled into concepts; existing pages updated; contradictions flagged |
| Provenance | "Where did this come from?" is a guess | Every object links back to its immutable source |
| Facts that change | Old claims quietly rot | Freshness stamps keep the base honest over time |
| Portability | Trapped in one app | Plain OKF files — open in Obsidian, VS Code, GitHub, anything |
| Privacy | Your knowledge leaves your machine | Local-first, offline-capable, local models |

## What it will feel like (planned CLI)

```bash
# point OpenKOS at a folder of text and compile it into an OKF bundle
openkos ingest ./sources

# ask a question and get a cited answer from the compiled knowledge
openkos query "what did I conclude about vector databases?"

# check the base stays honest — stale `as of` stamps and orphan pages
openkos lint
```

*(Illustrative of the intended MVP 1 experience. The full command set is `init`, `ingest`, `query`, `lint`, `status`, and a basic `forget` — see [`docs/cli.md`](docs/cli.md). Nothing runs yet.)*

## Getting started

> Pre-alpha: the steps below describe the intended flow and do not run yet.

OpenKOS is a local-first command-line tool. You install the engine once, then create a knowledge bundle per knowledge base — much like installing git once and running `git init` in many repositories.

**Prerequisites:** Python 3.13+, and a local model runtime — [Ollama](https://ollama.com) with a model pulled (for example `ollama pull qwen2.5`). No accounts, no API keys: nothing leaves your machine.

**Install the engine** (once, after the first PyPI release):

```bash
uv tool install openkos   # or: pipx install openkos — or: pip install openkos
```

**Create a bundle** (per knowledge base):

```bash
mkdir ~/knowledge && cd ~/knowledge
openkos init
```

`init` scaffolds the bundle (`raw/`, concept folders, `index.md`, `log.md`), writes `openkos.yaml` and `AGENTS.md`, helps you pick a local model, and initializes a git repository (with `.openkos/` git-ignored).

**Then the loop:**

```bash
openkos ingest ./meeting-notes.txt   # compile a source into the bundle
openkos query "what did we decide?"  # get a cited answer
```

See [`docs/cli.md`](docs/cli.md) for the full command reference and [`docs/user-journey.md`](docs/user-journey.md) for the end-to-end experience.

## Philosophy

- **Local-first and private by default.** Runs on your machine, works offline, built for local models. The cloud is optional, never required.
- **Standard-aligned, not bespoke.** We adopt OKF rather than invent a format. An open, vendor-neutral standard is the most agnostic choice there is.
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

OpenKOS ships in three MVP arcs, each usable on its own. Full detail in [`docs/roadmap.md`](docs/roadmap.md).

- **MVP 1 — The Compiler.** The Karpathy loop, locally, over text: ingest → OKF concepts with provenance → cited query → freshness lint. Useful in an afternoon.
- **MVP 2 — The Graph and Memory.** Entity/relationship extraction, a typed knowledge graph, hybrid retrieval (lexical + vector + graph), answers that file back into the base.
- **MVP 3 — The Runtime and Interoperability.** An MCP server and APIs so agents use OpenKOS as durable memory; full OKF import/export with the wider ecosystem.

Beyond that: a desktop app, graph visualization, richer memory, and federation — explored only after the MVPs prove out with real users.

## Documentation

- [`docs/vision.md`](docs/vision.md) — vision, philosophy, and positioning
- [`docs/philosophy.md`](docs/philosophy.md) — the foundational essay: what knowledge is and why OpenKOS matters
- [`docs/knowledge-object-model.md`](docs/knowledge-object-model.md) — how knowledge is represented (OKF + the OpenKOS layer)
- [`docs/roadmap.md`](docs/roadmap.md) — the MVP roadmap
- [`docs/tech_stack.md`](docs/tech_stack.md) — technology choices
- [`docs/architecture.md`](docs/architecture.md) — repository and bundle structure, and source versioning
- [`docs/okf-alignment.md`](docs/okf-alignment.md) — how OpenKOS relates to OKF
- [`docs/glossary.md`](docs/glossary.md) — definitions of the core vocabulary
- [`docs/faq.md`](docs/faq.md) — frequently asked questions
- [`docs/user-journey.md`](docs/user-journey.md) — the end-to-end user experience
- [`docs/cli.md`](docs/cli.md) — the MVP 1 command-line reference
- [`docs/brand.md`](docs/brand.md) — visual identity: isotype, wordmark, palette, typography
- [`docs/adr/`](docs/adr/) — architecture decision records (the log begins with the first code-time decision)

## Contributing

OpenKOS is early, which is the best time to shape it. The clearest entry points are the "community can contribute" notes under the current MVP in the [roadmap](docs/roadmap.md). Please open an issue to discuss anything larger than a small change before sending a PR, so we can make sure it fits and can be merged.

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to get involved and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for community standards. Maintainers: see [MAINTAINERS.md](MAINTAINERS.md) for how contributions are reviewed and decided.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
