---
type: Architecture
title: OpenKOS Architecture
description: How the OpenKOS codebase and a user's knowledge bundle are organized, and how source material is stored and versioned.
tags:
  - openkos
  - architecture
  - repository
  - bundle
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-15T03:00:00Z
sensitivity: public
---

# Architecture

This document maps how OpenKOS is organized — both the engine's source code and the user's knowledge bundle — and how raw source material is stored and versioned. It describes the *mature* shape so the structure is a stable contract to fill in incrementally: MVP 1 modules now exist, while later layers do not yet. What each MVP actually needs is called out at the end. Note that MVP 1's actual module names (for example `extraction/concept.py`, `cli/main.py`, `retrieval/answer.py`, `state/fts.py`) differ from the forward-looking mature tree shown below, which is filled in incrementally.

Two ideas from elsewhere in the docs anchor everything here: the split between a **durable canonical layer** (files + SQLite + git) and a **rebuildable derived layer** (vectors, graph) from [`tech_stack.md`](tech_stack.md), and the Knowledge Object model from [`knowledge-object-model.md`](knowledge-object-model.md).

## Repository structure (the engine)

A `src/` layout whose folders mirror the architecture: the knowledge model, the canonical layer, the swappable derived backends behind interfaces, the producer/consumer plugin surface, and thin entry layers over one engine.

```
openkos/
├── src/openkos/
│   ├── model/                # the Knowledge Object + OKF conformance
│   │   ├── knowledge_object.py
│   │   ├── okf.py            # OKF field set + conformance checks
│   │   ├── relations.py      # typed relationships
│   │   ├── freshness.py      # timeless/snapshot/pointer + stamps
│   │   ├── sensitivity.py
│   │   └── types.py          # canonical vocabulary + type registry
│   ├── bundle/               # CANONICAL layer (durable): files + git
│   │   ├── bundle.py         # open/read/write a bundle
│   │   ├── index.py          # index.md
│   │   ├── log.py            # log.md
│   │   ├── provenance.py
│   │   └── git.py            # history, revert
│   ├── state/                # SQLite operational store (rebuildable)
│   │   ├── db.py
│   │   ├── registry.py       # object + type registry
│   │   └── fts.py            # FTS5 lexical index
│   ├── llm/                  # model runtime abstraction
│   │   ├── base.py           # LLMBackend interface
│   │   ├── ollama.py
│   │   └── openai_compat.py
│   ├── embeddings/
│   │   ├── base.py
│   │   └── sentence_transformers.py
│   ├── producers/            # ingesters — the plugin surface
│   │   ├── base.py           # Producer interface
│   │   ├── text.py           # MVP 1
│   │   ├── markdown.py
│   │   ├── pdf.py            # MVP 2+
│   │   └── web.py
│   ├── consumers/            # viewers / exporters
│   │   ├── base.py           # Consumer interface
│   │   └── okf_export.py
│   ├── compiler/             # the ingest pipeline
│   │   ├── ingest.py         # source → Knowledge Objects
│   │   ├── extract.py        # entity/relationship extraction (MVP 2)
│   │   └── reconcile.py      # contradiction handling (MVP 2)
│   ├── retrieval/            # DERIVED layer (swappable)
│   │   ├── vector/
│   │   │   ├── base.py       # VectorStore interface
│   │   │   ├── sqlite_vec.py # default
│   │   │   └── lancedb.py    # scale path
│   │   ├── lexical.py        # FTS5
│   │   ├── hybrid.py         # filter-first retrieval
│   │   └── context.py        # context assembly + citations
│   ├── graph/                # DERIVED layer (swappable)
│   │   ├── base.py           # GraphStore interface
│   │   ├── sqlite_graph.py   # node-edge + recursive SQL (default)
│   │   └── analysis.py       # NetworkX over subgraphs
│   ├── memory/               # memory projections (MVP 3)
│   ├── lint/                 # freshness + health (orphans, contradictions)
│   ├── lifecycle/            # undo / archive / merge / forget / purge
│   ├── sensitivity/          # boundary enforcement
│   ├── config.py             # openkos.yaml
│   ├── engine.py             # thin orchestrator (wiring / composition only)
│   ├── cli/                  # Typer (MVP 1): init, ingest, query, lint, status, forget, doctor
│   ├── api/                  # FastAPI local API (MVP 3)
│   └── mcp/                  # MCP server (MVP 3)
├── tests/{unit,integration,e2e,fixtures,evals}/
├── examples/                 # runnable example bundles
├── docs/
├── openspec/                 # the spec contract: specs/{domain}/ · changes/ · config.yaml
├── pyproject.toml · uv.lock
└── README.md · LICENSE · NOTICE · CHANGELOG.md · .github/
```

The principles that shape it:

- **Each folder is a piece of the architecture.** `model` is the Knowledge Object; `bundle` + `state` are the durable canonical layer; `retrieval` + `graph` + `memory` are the rebuildable derived layer; `producers`/`consumers` are the plugin surface; `lint`/`lifecycle`/`sensitivity` are the disciplines.
- **The `base.py` files are the extension points.** `VectorStore`, `GraphStore`, `Producer`, `Consumer`, and `LLMBackend` are interfaces; `sqlite_vec.py`/`lancedb.py`, `text.py`/`pdf.py`, and the rest are implementations. The community can ship a new producer or backend as a *separate package* (via entry points) without touching the core — this is the surface opened for contribution in MVP 2.
- **One `engine.py` orchestrates; `cli`/`api`/`mcp` are thin adapters** over it, so no logic is duplicated across the command line, the local API, and the MCP server.
- **Everything under `state/` and the derived layer is reconstructible** from the canonical layer. If it is lost or corrupted, it rebuilds — which is why backends are swappable without migration.

## Repository conventions

A few conventions keep the repository clean as it grows:

- **Start lean, grow by MVP.** The tree above is the mature target, not a scaffold to create empty. Begin with the MVP 1 subset — `model`, `bundle`, `state`, `llm`, `producers`, `compiler`, `retrieval` (lexical + context), `lint`, `lifecycle`, `config`, `cli` — then add `embeddings`, `retrieval/vector`, `graph`, and `consumers` in MVP 2, and `api`, `mcp`, and `memory` in MVP 3. A folder is created when its code arrives.
- **`pyproject.toml` is the single source of config** — dependencies, the console entry point (now `openkos = "openkos.cli.main:app"`, since the `cli` package has landed in MVP 1), and the Ruff / MyPy / Pytest settings all live there.
- **Specs are the contract, and they live in `openspec/`.** Behavior is agreed before it is built: `openspec/specs/{domain}/spec.md` is the living per-domain contract, and `openspec/changes/{change-name}/` carries a change in flight — proposal, delta specs, design, tasks — until it lands and its deltas merge into the main spec. The directory is tracked and reviewed like any other file, so the contract is readable by contributors rather than private to whoever wrote the code. `openspec/config.yaml` configures that process only; it does not compete with `pyproject.toml`, which remains the single source of config for the toolchain.
- **Ship types.** Include an empty `src/openkos/py.typed` marker so type information is published to tools and to packages that extend OpenKOS.
- **Extension interfaces are `typing.Protocol`.** `Producer`, `Consumer`, `VectorStore`, `GraphStore`, and `LLMBackend` are Protocols (structural typing), so an external plugin implements the shape without importing or subclassing core classes. Plugins are discovered through entry points (for example the `openkos.producers` group), defined from MVP 1 even though the first producers are built in.
- **`engine.py` stays thin** — composition and wiring only; behavior lives in the subpackages.
- **The core is synchronous.** The engine, CLI, compiler, and stores are plain sync code. When the local API and MCP server arrive in MVP 3, they form an async edge that calls the sync engine through a thread pool; parallel work such as batch embedding also uses a thread pool from sync code. The core is not made async.
- **Layering is a followed convention, not yet an automated guard.** The canonical layer (`model`, `bundle`, `state`) does not depend on the derived layer (`retrieval`, `graph`, `memory`); derived depends on canonical, never the reverse. A tool such as import-linter will guard these boundaries in CI once the derived layer lands; it is not wired yet.
- **The OKF adapter is one seam.** Everything that knows the on-disk shape of the format — parsing and emitting frontmatter, the reserved-file structure, the conformance rules of §9 — lives in `model/okf.py` and nowhere else. The rest of the engine works with Knowledge Objects and never touches the format directly. This is deliberate risk containment: OKF is a **v0.1 draft**, and §11 permits a major version to rename required fields or change reserved filenames. Keeping the format behind one module makes a spec revision a contained change to one file instead of a search across the codebase, and it is the reason we can adopt a young standard without betting the engine on it.

These are starting conventions; like everything pre-code, they can change as the implementation teaches us more.

## Workspace structure (the user's knowledge base)

The directory a user opens in Obsidian, VS Code, or GitHub. We call it a **workspace**, and it holds three things that are deliberately kept apart: the immutable sources, the compiled bundle, and the engine's own files.

```
my-knowledge/             # the WORKSPACE (the git repository)
├── openkos.yaml          # config: model, review, default_sensitivity, freshness window, layout…
├── AGENTS.md             # agent operating manual (how to work with this workspace)
├── raw/                  # source material — any extension (see "Source material and versioning")
│   ├── call-with-maria-2026-07-14.txt
│   ├── meeting-notes.md
│   └── lecture-recording.m4a.json   # sidecar manifest for a binary original (hash, source…)
├── bundle/               # THE OKF BUNDLE (bundle root) — conformant, portable, shareable
│   ├── index.md          # catalog of concepts (carries okf_version)
│   ├── log.md            # chronological history
│   ├── sources/          # one Source concept per raw original
│   ├── concepts/  people/  organizations/
│   └── projects/  decisions/  events/  procedures/
└── .openkos/             # DERIVED + heavy: rebuildable, git-ignored by default
    ├── openkos.db        # SQLite: operational state, FTS5, graph node-edge tables
    ├── vectors/          # vector index (MVP 2+)
    └── raw-store/        # content-addressed binary originals
```

### Why `raw/` is outside the bundle

This split is the load-bearing decision in the layout, and it is worth being explicit about why, because the obvious alternative — dropping `raw/` inside the bundle next to the concepts — is what most tools would do.

An OKF bundle is a bundle *of concepts*. Raw sources are not concepts; they are input material. Keeping them inside the concept tree mixes two different kinds of thing, and the format notices: OKF §9 requires every non-reserved `.md` file in a bundle to carry frontmatter with a `type`. An ingested third-party markdown file carries neither — so a `raw/notes.md` inside the bundle would make the whole bundle non-conformant, and adding frontmatter to it would violate immutability. Working around that (renaming the copy, say) would only paper over the real issue: the file is in the wrong place.

Putting `raw/` beside the bundle rather than inside it resolves this **by construction, not by convention**:

- **The invariant is structural.** Nothing a user drops into `raw/` — by hand, bypassing `ingest` entirely, which "editing by hand" explicitly allows — can break conformance, because `raw/` is not in the OKF tree. An invariant that depended on every file arriving through the CLI would not be an invariant.
- **Sources keep their real names and extensions.** `meeting-notes.md` stays `meeting-notes.md` and still renders in Obsidian. No spec detail leaks into the user's filenames.
- **`bundle/` becomes a true unit of distribution.** Share it and you ship pure conformant OKF — no sources, no `openkos.yaml`, no operating manual mixed in. That also lines up with sensitivity: knowledge is frequently shareable when the transcript it came from is not.
- **Text and binary sources get one treatment.** Both live in `raw/`; both are represented in the bundle by a Source concept carrying the hash and description. The bundle always holds the manifest, never the blob (see below).

The concept folders inside `bundle/` are grouped by type as one sensible convention, but the layout is configurable in `openkos.yaml` (a user could prefer a flat structure — in OKF the file path is the concept's identity, not its type). `.openkos/` holds only derived, rebuildable state; it is what you `.gitignore`. What you version is `bundle/` plus the text-shaped `raw/` — which leads directly to the next section.

### The one bridge out of the bundle

A bundle that never points outside itself would lose its provenance, so exactly one document type is allowed to reach out: the **Source concept** in `bundle/sources/`. Its `resource` field names the original it summarizes (`raw/call-with-maria-2026-07-14.txt`, resolved from the workspace root) — which is precisely what OKF designed `resource` for: a URI identifying the underlying asset, normally outside the bundle.

Everything else stays inside. Derived objects cite their Source concepts with ordinary bundle-relative links (`[1] [Call with Maria Salazar — 2026-07-14](/sources/call-with-maria-2026-07-14.md)`), never the raw file directly. OKF §8 describes this pattern exactly — citations pointing into "a subdirectory that mirrors external material as first-class OKF concepts." The result is that the bundle's internal links always resolve, and a single, well-defined seam connects it to the sources on disk. The machine-readable `provenance` list still records the original paths for the engine's own use.

## Source material and versioning

Raw sources are immutable, but **immutable does not mean git-tracked.** Immutability means OpenKOS never rewrites a source; git is only one way to preserve history, and it is the wrong tool for large binaries — git keeps every version of every blob forever, does not compress binaries, and hosts like GitHub impose per-file and repository limits. A decade of PDFs, audio, and images committed to git would bloat the history until the repository is unusable. Committing raw material blindly also risks pushing confidential sources to a remote.

So OpenKOS splits `raw/` by the shape of the material:

**Text-shaped originals** (`.txt`, `.md`, and the text extracted from a document) are **git-tracked**, under their own names and extensions. They are small, diffable, and git handles their history well. They are also what the compiler actually reads and what re-compilation needs. Because `raw/` sits outside the bundle, a markdown original needs no special handling at all: it is stored exactly as it arrived.

**Binary or large originals** (PDF, audio, images) are **kept out of the main git history**, handled by three pieces:

1. **A small, git-tracked manifest** per original — its `sha256` hash, filename, type, timestamp, and source URL. This keeps provenance intact and verifiable without the blob in git: the hash in git proves which original produced each Knowledge Object even when the blob lives elsewhere.
2. **A configurable raw store** for the blob itself: the local filesystem (`.openkos/raw-store/`, content-addressed and git-ignored by default), **Git LFS** if the user wants it in the remote, or an external location (a cloud drive, S3). Content-addressing by hash deduplicates and verifies.
3. **Sensitivity-aware sync.** Material classified `confidential` is never pushed to a remote; the sync/gitignore policy respects the sensitivity class, so `raw/` inherits the same trust boundary as everything else.

Provenance therefore points to three things that together survive any single one going missing: the extracted text (in git), the manifest with the original's hash (in git), and the blob (in the raw store or external).

The result: connecting a bundle to GitHub is safe by default — you push the knowledge (markdown), the text sources, and the manifests, all small and textual; the heavy binaries stay local (or in LFS/external if the user opts in), and confidential material does not leave. Git stays lean forever, provenance stays intact, and the knowledge base is never killed by a PDF. This embodies the project's stance directly: the knowledge (markdown) is the permanent, lightweight thing you version; raw binaries are archival, preserved but outside the history that compounds.

*(The exact manifest format and default raw-store behavior are decisions to be recorded as ADRs once implementation begins.)*

## Delivery and front-ends

Local-first constrains *where the data and compute live* — on the user's machine, offline, theirs — not the *interface technology*. What breaks local-first is a **cloud-hosted** app that holds users' data on someone else's server, not the browser or web tech per se. So OpenKOS is not limited to a single kind of UI. Several delivery paths are all local-first:

- **Desktop app** (Tauri/Electron/native) — one installer and an icon, no terminal; the friendliest path for non-technical users, and where a runtime and model can be bundled. Note that a Tauri/Electron app *is* a web UI in a native shell, so "web vs desktop" is a false dichotomy at the technical level.
- **Local web UI (`localhost`)** — the engine serves a browser UI from its own local API (the FastAPI layer). Nothing leaves the machine; this is how Jupyter, Ollama, and most self-hosted tools work. Best wrapped inside the desktop app so the user never starts a server by hand.
- **Static HTML explorer** — a single self-contained HTML file that reads a bundle with no server (the approach of Google's OKF visualizer). Zero install, ideal for browsing knowledge read-only.
- **Editor plugin** — because the bundle is plain markdown, Obsidian and VS Code already act as a GUI over the knowledge; a plugin adds OpenKOS actions inside a tool the user already uses.
- **Chat / agent (MCP)** — the user "just talks to" OpenKOS from an AI client. For some non-technical users this is the lowest-friction interface of all.
- **CLI** — for technical users and automation.

The key architectural point: all of these are **thin consumers of the same local engine** through the `cli` / `api` / `mcp` adapters described above. Adding a front-end never touches the core; UIs stack on top of one engine. For non-technical users the likely order is desktop app first, then chat/MCP, then an editor plugin.

The one thing outside the local-first spirit is a **cloud-hosted, multi-tenant** service holding users' knowledge. A legitimate middle ground is **self-hosting** — the user runs the local web UI on their *own* server or VPS: still their data and their machine, just remote, rather than someone else's cloud.

## How it fills in by MVP

The structure above is the mature contract; it is populated incrementally.

- **MVP 1 (The Compiler)** needs `model`, `bundle`, `state` (with `fts`), `llm/ollama`, `producers/text` and `markdown`, `compiler/ingest`, `retrieval/lexical` and `context`, `lint/freshness`, a basic `lifecycle`, `config`, and `cli`. The workspace has `raw/` (text), `bundle/` with the concept folders plus `index.md` and `log.md`, `openkos.yaml`, and `AGENTS.md`.
- **MVP 2 (The Graph and Memory)** adds `embeddings`, `retrieval/vector`, `graph`, `compiler/extract` and `reconcile`, richer `lint`, additional `producers` (PDF, web) with the binary raw-store policy, and the reference-aware `lifecycle`.
- **MVP 3 (The Runtime and Interoperability)** adds `api`, `mcp`, `memory`, full OKF import/export in `consumers`, and sensitivity enforcement at the new boundaries.

The folders exist from the start as a contract; the code arrives MVP by MVP.
