# OpenKOS

[![PyPI](https://img.shields.io/pypi/v/openkos)](https://pypi.org/project/openkos/)
[![Python](https://img.shields.io/pypi/pyversions/openkos)](https://pypi.org/project/openkos/)
[![CI](https://github.com/jasonssdev/openkos/actions/workflows/ci.yml/badge.svg)](https://github.com/jasonssdev/openkos/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/jasonssdev/openkos/blob/main/LICENSE)

**Open Knowledge Orchestration System** — a local-first engine for the Open Knowledge Format.

OpenKOS turns your scattered text into a living, portable knowledge base your AI agents can actually use — compiled once, kept current, and stored as plain [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf) files so it is never locked to any app, model, or vendor.

> **Project status: alpha.** The Compiler, the Graph-and-Memory, the Ask Surface, and the Unattended Engine arcs (MVP 1 through MVP 4) are complete and shipped; The Quiet Engine arc, which cuts the decisions the engine asks of you, is complete too; and interoperability (MVP 5), OKF export and import, is complete. The desktop app (MVP 6) is next. The API may still change between releases, but OpenKOS is published and installable now. Early contributors and feedback are welcome — see [Contributing](#contributing).

---

## System requirements

OpenKOS runs local models through Ollama. Ollama keeps one chat model resident at a time plus the `bge-m3` embedder, so peak memory is set by the largest model in use. Figures are measured at the production context window (`num_ctx` 12288) on a Mac with unified memory.

| Setup | Memory used by Ollama | Recommended machine |
|---|---|---|
| Default (`qwen3:8b` + the `gemma4:26b-a4b` judge for contradictions and identity) | peak about 21.5 GB while the judge runs; about 7.2 GB otherwise | 32 GB unified memory or RAM (24 GB for Ollama, the rest for the OS) |
| Judges opted out (`models: {contradiction: null, adjudication: null}`) | about 7.2 GB (about 9.1 GB with `concurrent_extraction: true`) | 16 GB |
| Below 16 GB | a 3-4B chat model; weaker extraction | 8 GB is the floor, not a comfortable target |

- **Disk:** about 25 GB for the three default models (`qwen3:8b` 5.2 GB, `bge-m3` 1.2 GB, `gemma4:26b-a4b` 18 GB).
- **Discrete GPUs (NVIDIA and similar):** an estimate, not measured. Each model must fit in VRAM to run at full speed (Ollama offloads the rest to the CPU, much slower): about 24 GB of VRAM for the judge, about 8-12 GB for `qwen3:8b`.
- `openkos doctor` reports whether each task's model is installed.

## Quickstart

Five steps from a fresh machine to your first cited answer. Everything runs on your computer: no accounts, no API keys, nothing leaves your machine.

**1 · Install [Ollama](https://ollama.com)** — the local AI runtime OpenKOS talks to. Download it, open it once, and it stays running in the background.

**2 · Pull the default models** (one time):

```bash
ollama pull qwen3:8b        # chat model — extraction and answers (~5 GB)
ollama pull bge-m3          # embedding model — semantic search (~1.2 GB)
ollama pull gemma4:26b-a4b  # judge model — contradictions and identity (large; ~21.5 GB in memory)
```

> The first two cover ingest, query and everything else. The third is the default for the two judging jobs, `contradictions` and `adjudicate` (and `curate`'s Identity and Contradictions stages); without it only those fail, with the exact pull command. It is large, so the engine runs one chat model at a time and swaps between `qwen3:8b` and the judge as stages change; see [System requirements](#system-requirements) for memory and disk. On a smaller machine, opt out per task with `models: {contradiction: null, adjudication: null}` in `openkos.yaml` and the judges follow `model:`.
>
> For naming relations during curation there is also an **optional measured upgrade** (`ollama pull gemma2:27b`, **15.6 GB**) that nearly doubles relation-type accuracy — opt in later with `models: {edge_typing: gemma2:27b}` in `openkos.yaml`; `openkos doctor` and `curate` both point at it.

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

> **Upgrading a workspace created before 0.2.11?** Run `openkos reindex` once afterwards. From 0.2.11 the embedding store keeps chunk-backed vectors, so the rest of every long source is visible to semantic search; a store written before that has the old schema, which cannot be migrated in place. It is detected on open, dropped, and recreated, so that first `reindex` re-embeds the workspace and reports `no embedding-model tag stored (fresh or dropped store)` rather than a model change. Nothing in `bundle/` is touched: the derived indexes rebuild from it, which is the point of keeping them derived.

> **Upgrading from 0.4.0?** `ingest` now revises an existing same-type, same-key concept instead of writing a `-N` duplicate (`attach_at_ingest: false` restores the old behaviour). The contradiction and identity judges default to `gemma4:26b-a4b` on Ollama: run `ollama pull gemma4:26b-a4b`, or opt out with `models: {contradiction: null, adjudication: null}`. On identity review prompts Enter and `n` now skip and only `d` records a keep-distinct ruling. `curate` no longer presents relation suggestions unless you pass `--structure`, and cross-type merges need `--include-cross-type`. The [changelog](https://github.com/jasonssdev/openkos/blob/main/CHANGELOG.md) lists the rest.

> **Upgrading from 0.3.1?** A repeated key in `openkos.yaml` is now refused (`openkos doctor` names it). Do not run 0.3.0 or older at the same time as this version on one workspace: the transitional temp-directory lock is no longer taken. A watched file edited after import now imports as a new version and queues its supersession instead of being refused. Human-readable stderr text changed (it is not a parsing interface). The [changelog](https://github.com/jasonssdev/openkos/blob/main/CHANGELOG.md) lists the rest.

> **Upgrading from 0.3.0?** The workspace lock file moved to a per-user state directory, lock contention on the derived stores now exits `3` (retry-safe) instead of `1`, and a plain `query` no longer takes the lock; the [changelog](https://github.com/jasonssdev/openkos/blob/main/CHANGELOG.md) lists the rest.

> **Upgrading an existing bundle from 0.2.x?** Run `openkos repair` once to migrate it from OKF v0.1 to v0.2; reads keep working meanwhile, only new writes use the v0.2 shape. The [changelog](https://github.com/jasonssdev/openkos/blob/main/CHANGELOG.md) lists the few other behaviour changes (the `STATUS` column now says `stable`, and `doctor`/`lint` exit `2` when a check could not run).

**4 · Check the setup before anything can fail:**

```bash
openkos doctor
```

Outside a workspace, expect one `[FAIL]` — *Workspace initialized*, since you haven't created one yet — and six `[SKIP]` lines for the checks that need a workspace to inspect. Everything else should `[PASS]`. Anything that fails prints the command that fixes it. `doctor` is also the first thing to run whenever something misbehaves later.

**5 · Create a knowledge base and run the loop:**

```bash
mkdir ~/knowledge && cd ~/knowledge
openkos init                          # picks your models, scaffolds the workspace
openkos ingest ./meeting-notes.txt    # compile a source — a file, a folder, or a glob
openkos query "what did we decide?"   # an answer with citations, from your own knowledge
```

From here, `openkos next` recommends the one thing worth doing whenever one of its ranked tiers fires — and says so honestly when none does (`openkos status` then points at the standing disclosures, such as near-duplicate backlogs its duplicate tier deliberately leaves to `openkos duplicates`) — and `openkos --help` lists every command, grouped. The full reference is [`docs/cli.md`](https://github.com/jasonssdev/openkos/blob/main/docs/cli.md); the end-to-end experience is [`docs/user-journey.md`](https://github.com/jasonssdev/openkos/blob/main/docs/user-journey.md).

**Ask from a chat client.** `openkos mcp --workspace ~/knowledge` serves the workspace read-only over stdio to any MCP client (`query`, `get`, `navigate`, and what is pending). Confidential objects stay withheld unless you launch it with `--expose-confidential`. Prefer llama.cpp, LM Studio or vLLM to Ollama? Set `backend: openai-compatible` and `base_url` in `openkos.yaml` — see [`docs/cli.md`](https://github.com/jasonssdev/openkos/blob/main/docs/cli.md).

**Run it unattended.** `openkos daemon` runs in the foreground (`--once` runs what is due and exits): on a timer it refreshes the derived indexes and queues proposals (duplicates, relation types, volatility, contradictions), and it ingests files that settle in an `inbox` folder you name. Maintenance never changes a concept on its own (importing a settled inbox file is the one thing it writes, exactly as `openkos ingest` would), and it stays inside the model-call budget of the optional `unattended:` section of `openkos.yaml`. `openkos pending` (`--all`, `--stats`) lists what it queued for you to decide, and `openkos next`, `openkos status`, the MCP server and `openkos curate` read the same queue. Locked commands accept `--wait <seconds>` when another OpenKOS process holds the workspace lock — see [`docs/cli.md`](https://github.com/jasonssdev/openkos/blob/main/docs/cli.md).

**What `init` set up for you.** `raw/` holds your immutable sources; `bundle/` is the pure-OKF knowledge base — plain markdown you can open in Obsidian, VS Code, or GitHub, and take anywhere (open **`bundle/`** itself as the vault or folder, not the workspace root — [the procedure is here](https://github.com/jasonssdev/openkos/blob/main/docs/user-journey.md#reading-the-bundle-in-an-editor)); `openkos.yaml` is the engine config. The folder is also a git repository **you never have to operate**: every command commits its own changes, and `git log` / `git diff` / `git revert` are always there for inspection and undo.

---

## The problem

Your AI assistant forgets everything between sessions, so you re-explain the same context every time and the insights you build together disappear into chat history. Meanwhile your notes pile up in folders nobody keeps current. Two powerful things — your knowledge and your models — sit side by side, disconnected.

Retrieval (RAG) doesn't fix this: it re-reads your raw documents on every question and rediscovers everything from scratch. Nothing accumulates. The cross-references are never drawn, the contradictions never reconciled.

## The idea

Instead of retrieving from raw sources every time, an LLM can *incrementally build and maintain* a persistent, interlinked knowledge base that sits between you and your sources — Andrej Karpathy's [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) pattern. Knowledge is compiled once and then kept current. It compounds.

In June 2026 Google Cloud published a vendor-neutral specification for that pattern: the **Open Knowledge Format (OKF)** — a directory of markdown files with YAML frontmatter, portable across any tool. It is young (v0.2, still pre-1.0) but open, minimal, and gaining adoption. Google's framing was: *"What's missing is a format, not another service,"* and they invited the community to build producers and consumers.

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

OpenKOS ships in arcs, each usable on its own. Full detail in [`docs/roadmap.md`](https://github.com/jasonssdev/openkos/blob/main/docs/roadmap.md).

- **MVP 1 — The Compiler. (Complete.)** The Karpathy loop, locally, over text: ingest → OKF concepts with provenance → cited query → freshness lint. Useful in an afternoon.
- **MVP 2 — The Graph and Memory. (Complete.)** Entity/relationship extraction and reversible merge, a typed knowledge graph (an OpenKOS layer over OKF's untyped links — other tools still read the bundle fine), hybrid retrieval (lexical and semantic, rank-fused), contradiction detection with durable verdicts (findings persist, so a repeat check costs no model calls, and `reconcile` records how you settled each one), a fail-closed sensitivity filter (confidential concepts never leave the machine — held back from any backend that is not verifiably local), a guided curation loop, reference-aware `forget` plus an irreversible `purge` (right-to-be-forgotten), and answers that file back into the base (the two-output rule).
- **MVP 3 — The Ask Surface. (Complete.)** Reading the bundle already needs no terminal — it opens as an Obsidian vault as-is. This arc adds asking: application services for the read verbs and an MCP server (`query`, `get`, `navigate`, what is pending) gated on sensitivity, so a chat client you already have becomes the interface.
- **MVP 4 — The Unattended Engine. (Complete.)** A foreground daemon runs scheduled maintenance and watches an inbox folder inside a call budget you set; it does the non-consequential work and queues the consequential decisions as pending work for you to review, instead of asking you to remember them. The workspace lock is held only for short commit phases, so a person and the daemon can share one workspace.
- **The Quiet Engine (a named arc before MVP 5). (Complete.)** Fewer decisions per ingested source: concepts that already exist are attached to rather than forked, indexes stay fresh after unattended writes, and review becomes an after-the-fact digest with undo.
- **MVP 5 — Interoperability. (Complete.)** Full OKF export, then import, so knowledge moves in and out of the wider ecosystem without losing its structure.
- **MVP 6 — The Desktop App.** One installer and an icon, so a non-technical user can drop files in, ask, and settle the pending queue without a terminal; it brings a local API and a stable Python API with the app as its first client.

Beyond that: extension points for third-party producers and consumers, memory projections, graph visualization, and federation — explored only after the MVPs prove out with real users.

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
