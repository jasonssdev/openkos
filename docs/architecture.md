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

This document maps how OpenKOS is organized — both the engine's source code and the user's knowledge bundle — and how raw source material is stored and versioned.

**What ships and what is planned are kept apart here.** Everything under "Repository structure" describes the code that exists today at v0.2.13; anything not yet built lives in [Target architecture](#target-architecture) or in [`roadmap.md`](roadmap.md), labelled as such. That separation is deliberate: this document previously showed one forward-looking tree with its corrections in footnotes, and a reader could not tell a module that exists from one that does not.

Two ideas from elsewhere in the docs anchor everything here: the split between a **durable canonical layer** (files + SQLite + git) and a **rebuildable derived layer** (vectors, graph) from [`tech_stack.md`](tech_stack.md), and the Knowledge Object model from [`knowledge-object-model.md`](knowledge-object-model.md).

## Repository structure (the engine)

A `src/` layout whose packages mirror the architecture: the knowledge model, the canonical layer, the derived backends, the pipeline that turns text into objects, and the entry layer. This is the tree as it exists — generated from disk, not aspirational.

```
openkos/
├── src/openkos/
│   ├── model/                    # the Knowledge Object + OKF conformance
│   │   ├── okf.py                # OKF field set, framing, conformance checks
│   │   ├── relations.py          # typed relationships
│   │   └── types.py              # canonical vocabulary + type registry
│   ├── bundle/                   # CANONICAL layer (durable): files + sidecars
│   │   ├── bundle.py  index.py  log.py  listing.py
│   │   ├── provenance.py  references.py  links.py  relations.py
│   │   ├── merge.py  ledger.py  decisions.py
│   │   └── source_titles.py
│   ├── vcs/git.py                # history, revert, purge's history rewrite
│   ├── state/                    # SQLite stores under .openkos/ (see State taxonomy)
│   │   ├── derived.py            # the shared opener + manifest-hash gate
│   │   ├── fts.py  vectorstore.py  reindex.py
│   │   ├── findings.py  adjudications.py       # same file, two tenants
│   │   ├── edge_suggestions.py  question_vectors.py
│   ├── graph/                    # DERIVED layer
│   │   ├── base.py  sqlite_graph.py  analysis.py
│   │   └── proximity.py  summary.py
│   ├── retrieval/                # DERIVED layer
│   │   ├── pool.py  fusion.py    # candidate pool, RRF fusion
│   │   └── answer.py             # context assembly, citations, generation
│   ├── extraction/               # source text → Knowledge Objects
│   │   ├── concept.py  evidence.py  judge.py
│   ├── resolution/               # identity, contradiction, typing decisions
│   │   ├── candidates.py  similarity.py  normalize.py
│   │   ├── insight_identity.py  adjudication.py
│   │   ├── contradiction.py  reconciliation.py
│   │   └── edge_typing.py  volatility_typing.py
│   ├── llm/                      # model runtime abstraction
│   │   ├── base.py  ollama.py  prompting.py  parsing.py
│   ├── application/              # synchronous use-case services (ADR-0018)
│   │   └── query.py              # the first one; ingest and lifecycle to follow
│   ├── cli/                      # Typer entry layer
│   │   ├── main.py  curate.py  next_action.py  observability.py
│   ├── config.py                 # openkos.yaml + WorkspaceLayout
│   ├── lint.py  lifecycle.py  sensitivity.py
│   ├── fsio.py  lock.py          # filesystem primitives; interprocess lock
│   ├── prompt_budget.py  source_title.py
│   └── py.typed
├── tests/unit/ · evals/           # integration/e2e arrive when code justifies them
├── examples/                     # runnable example bundles
├── docs/                         # including adr/
├── openspec/                     # the spec contract: specs/{domain}/ · changes/ · config.yaml
├── pyproject.toml · uv.lock
└── README.md · LICENSE · NOTICE · CHANGELOG.md · .github/
```

The principles that shape it:

- **Each package is a piece of the architecture.** `model` is the Knowledge Object; `bundle` + `vcs` are the durable canonical layer; `state` + `retrieval` + `graph` are the derived layer; `extraction` + `resolution` are the pipeline that turns text into objects and then decides what they mean; `lint`/`lifecycle`/`sensitivity` are the disciplines; `cli` is the entry layer.
- **The `base.py` files are the seams that exist today.** `graph/base.py` and `llm/base.py` define the shapes their implementations satisfy (`sqlite_graph.py`, `ollama.py`). They are internal seams, not a published plugin API: OpenKOS ships no `Producer`/`Consumer` interface and no entry-point group. That extension surface is a roadmap item, not present code — see [`roadmap.md`](roadmap.md).
- **Use-case services, not one orchestrator.** [ADR-0018](adr/0018-application-layer-for-bounded-context-services.md) chose narrow synchronous services under `application/` over a single `engine.py`, so each use case owns its own composition instead of one module owning all of them. `application/query.py` is the first; ingest and lifecycle follow. The CLI is being reduced to parsing, presentation, and exit codes as each one lands ([#918](https://github.com/jasonssdev/openkos/issues/918)).
- **The derived layer is reconstructible — but not uniformly, and not for free.** The five SQLite stores under `.openkos/` sit at three different points on that scale. See [State taxonomy](#state-taxonomy) below, which is the one place that distinction is written down.

## Repository conventions

A few conventions keep the repository clean as it grows:

- **A package is created when its code arrives.** The tree above holds no empty scaffolding, and this document does not list folders that do not exist. What is planned is named in [Target architecture](#target-architecture) and dated in [`roadmap.md`](roadmap.md).
- **`pyproject.toml` is the single source of config** — dependencies, the console entry point (now `openkos = "openkos.cli.main:app"`, since the `cli` package has landed in MVP 1), and the Ruff / MyPy / Pytest settings all live there.
- **Specs are the contract, and they live in `openspec/`.** Behavior is agreed before it is built: `openspec/specs/{domain}/spec.md` is the living per-domain contract, and `openspec/changes/{change-name}/` carries a change in flight — proposal, delta specs, design, tasks — until it lands and its deltas merge into the main spec. The directory is tracked and reviewed like any other file, so the contract is readable by contributors rather than private to whoever wrote the code. `openspec/config.yaml` configures that process only; it does not compete with `pyproject.toml`, which remains the single source of config for the toolchain.
- **Ship types.** Include an empty `src/openkos/py.typed` marker so type information is published to tools and to packages that extend OpenKOS.
- **Internal seams are `typing.Protocol`.** Structural typing lets an implementation satisfy a seam without importing or subclassing it. Today this is used inside the engine (the graph and LLM backends); publishing any of it as a third-party extension point is a roadmap item and would need its own ADR.
- **The core is synchronous.** The CLI, the application services, the extraction pipeline, and the stores are plain sync code. When the local API and MCP server arrive in MVP 3 they form an async edge that calls the sync core through a thread pool; parallel work such as batch embedding also uses a thread pool from sync code. The core is not made async — which is why ADR-0018's services are specified as synchronous.
- **Layering is a followed convention, not yet an automated guard.** The canonical layer (`model`, `bundle`, `vcs`) does not depend on the derived layer (`state`, `retrieval`, `graph`); derived depends on canonical, never the reverse. `fsio` and `lock` are leaf modules that import nothing from `openkos`, so either layer may use them. A tool such as import-linter would guard these boundaries in CI; it is not wired yet.
- **The OKF adapter is one seam.** Everything that knows the on-disk shape of the format — parsing and emitting frontmatter, the reserved-file structure, the conformance rules of §9 — lives in `model/okf.py` and nowhere else. The rest of the engine works with Knowledge Objects and never touches the format directly. This is deliberate risk containment: OKF is a **v0.1 draft**, and §11 permits a major version to rename required fields or change reserved filenames. Keeping the format behind one module makes a spec revision a contained change to one file instead of a search across the codebase, and it is the reason we can adopt a young standard without betting the engine on it.

These conventions describe the code as it stands; they change when a decision changes, and a change worth keeping becomes an ADR.

## Workspace structure (the user's knowledge base)

The directory a user opens in Obsidian, VS Code, or GitHub. We call it a **workspace**, and it holds three things that are deliberately kept apart: the immutable sources, the compiled bundle, and the engine's own files. By convention it lives at the root of the user's home directory and is named `knowledge` (`~/knowledge`) — one machine can hold several workspaces, but that is the default a user should meet first.

```
~/knowledge/              # the WORKSPACE (the git repository, created by `openkos init`)
├── openkos.yaml          # config: model, review, default_sensitivity, freshness window…
├── AGENTS.md             # agent operating manual (how to work with this workspace)
├── raw/                  # source material — any extension (see "Source material and versioning")
│   ├── call-with-maria-2026-07-14.txt
│   ├── meeting-notes.md
│   └── lecture-recording.m4a.json   # sidecar manifest for a binary original (hash, source…)
├── bundle/               # THE OKF BUNDLE (bundle root) — conformant and portable
│   ├── index.md          # catalog of concepts (carries okf_version)
│   ├── log.md            # chronological history
│   ├── sources/          # one Source concept per raw original
│   ├── concepts/  entities/  places/  people/  organizations/
│   ├── projects/  decisions/  events/  procedures/  insights/
│   └── .state/           # engine sidecars, never `*.md`: merge ledger, decisions
└── .openkos/             # DERIVED: rebuildable, git-ignored (see "State taxonomy")
    ├── fts.db            # lexical index
    ├── graph.db          # node-edge projection
    ├── vectors.db        # dense index
    ├── findings.db       # contradiction + adjudication verdicts (NOT an index)
    └── insight_questions.db   # cached question embeddings for `query --save`
```

*(A content-addressed `raw-store/` for binary originals is described under
"Source material and versioning" below and is not yet built; today `raw/` holds
text-shaped sources only.)*

*(Those five stores do not share one lifecycle, and the differences are
load-bearing — which rebuild for free, which cost model calls, and which is a
verdict rather than a projection. [State taxonomy](#state-taxonomy) is the one
place that is written down; see also `design D1` in the `performance-caching`
change record and [ADR-0014](adr/0014-durable-pending-work-stores.md).)*

*(`bundle/.state/` is the one directory inside the bundle that holds no concepts: the merge-ledger sidecars [ADR-0013](adr/0013-relocate-merge-ledger-to-bundle-state.md) relocated there out of survivors' frontmatter, and the operator-decision sidecars [ADR-0014](adr/0014-durable-pending-work-stores.md) placed beside them. Nothing under it is named `*.md`, which is what keeps it invisible to every `rglob("*.md")` walk in the engine and therefore outside OKF §9 rule 1 — a structural exclusion rather than one every walk must remember, and `lint` carries a dedicated check that flags any `.md` file appearing there as a regression against it. Unlike `.openkos/`, this state is durable and canonical: it is versioned with the bundle, not git-ignored.)*

### Why `raw/` is outside the bundle

This split is the load-bearing decision in the layout, and it is worth being explicit about why, because the obvious alternative — dropping `raw/` inside the bundle next to the concepts — is what most tools would do.

An OKF bundle is a bundle *of concepts*. Raw sources are not concepts; they are input material. Keeping them inside the concept tree mixes two different kinds of thing, and the format notices: OKF §9 requires every non-reserved `.md` file in a bundle to carry frontmatter with a `type`. An ingested third-party markdown file carries neither — so a `raw/notes.md` inside the bundle would make the whole bundle non-conformant, and adding frontmatter to it would violate immutability. Working around that (renaming the copy, say) would only paper over the real issue: the file is in the wrong place.

Putting `raw/` beside the bundle rather than inside it resolves this **by construction, not by convention**:

- **The invariant is structural.** Nothing a user drops into `raw/` — by hand, bypassing `ingest` entirely, which "editing by hand" explicitly allows — can break conformance, because `raw/` is not in the OKF tree. An invariant that depended on every file arriving through the CLI would not be an invariant.
- **Sources keep their real names and extensions.** `meeting-notes.md` stays `meeting-notes.md` and still renders in Obsidian. No spec detail leaks into the user's filenames.
- **`bundle/` becomes a true unit of distribution.** Share it and you ship pure conformant OKF — no sources, no `openkos.yaml`, no operating manual mixed in. That also lines up with sensitivity: knowledge is frequently shareable when the transcript it came from is not. One caveat belongs here rather than in a footnote, because conformance and shareability are not the same property and it is easy to read the first as implying the second. Conformance holds: `bundle/.state/` carries no `*.md` file, so the merge-ledger sidecars are outside §9's reach by construction. Shareability does not follow automatically, because those sidecars hold, per merge, the absorbed object's full verbatim bytes, the survivor's pre-merge bytes, and whole-file snapshots of every third-party document the merge retargeted — including bodies whose sensitivity was `confidential` when they were frozen. Zip `bundle/` today and you ship the current concepts *and* everything a past `merge` folded away. This is what `forget`'s ledger sweep exists for, and it is worth checking before a bundle leaves the machine.
- **Text and binary sources get one treatment.** Both live in `raw/`; both are represented in the bundle by a Source concept carrying the hash and description. The bundle always holds the manifest, never the blob (see below).

The concept folders inside `bundle/` are grouped by type as one sensible convention; the layout itself is fixed — the engine always resolves `raw/` and `bundle/` beside `openkos.yaml`, and the config deliberately declares no layout keys it would not honor; making the layout genuinely configurable (for example a flat structure — in OKF the file path is the concept's identity, not its type) remains future work. `.openkos/` holds only derived, rebuildable state; it is what you `.gitignore`. What you version is `bundle/` plus the text-shaped `raw/` — which leads directly to the next section.

### The one bridge out of the bundle

A bundle that never points outside itself would lose its provenance, so exactly one document type is allowed to reach out: the **Source concept** in `bundle/sources/`. Its `resource` field names the original it summarizes (`raw/call-with-maria-2026-07-14.txt`, resolved from the workspace root) — which is precisely what OKF designed `resource` for: a URI identifying the underlying asset, normally outside the bundle.

Everything else stays inside. Derived objects cite their Source concepts with ordinary bundle-relative links (`[1] [Call with Maria Salazar — 2026-07-14](/sources/call-with-maria-2026-07-14.md)`), never the raw file directly. OKF §8 describes this pattern exactly — citations pointing into "a subdirectory that mirrors external material as first-class OKF concepts." The result is that the bundle's internal links always resolve, and a single, well-defined seam connects it to the sources on disk. The machine-readable `provenance` list still records the original paths for the engine's own use.

## Source material and versioning

Raw sources are immutable, but **immutable does not mean git-tracked.** Immutability means OpenKOS never rewrites a source; git is only one way to preserve history, and it is the wrong tool for large binaries — git keeps every version of every blob forever, does not compress binaries, and hosts like GitHub impose per-file and repository limits. A decade of PDFs, audio, and images committed to git would bloat the history until the repository is unusable. Committing raw material blindly also risks pushing confidential sources to a remote.

The history builds itself. `openkos init` makes the workspace a git repository (it never nests one inside a parent repo) and writes a `.gitignore` that excludes the rebuildable `.openkos/` stores, and from then on every mutating verb commits exactly the paths it wrote — a scoped `git add -- <paths>`, never `-A`, so unrelated dirty content in a host repository is never swept in. Nobody runs git by hand, which is what makes the granular history usable as an undo (`git revert <commit>` reverses one operation) and what lets `purge` require a clean tree at all. The engine says so rather than leaving it to be discovered: `init` discloses the repository, the generated `AGENTS.md` carries a version-control section, and the verbs whose writes are most often wanted back name the commit they just made.

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

The key architectural point: all of these are **thin adapters over the same local engine**. Today only `cli` exists; `api` and `mcp` are MVP 3 work, and the application services under `application/` are being extracted precisely so those adapters have a surface to call that is not Typer's internals. Adding a front-end never touches the core; UIs stack on top of one engine. For non-technical users the likely order is desktop app first, then chat/MCP, then an editor plugin.

The one thing outside the local-first spirit is a **cloud-hosted, multi-tenant** service holding users' knowledge. A legitimate middle ground is **self-hosting** — the user runs the local web UI on their *own* server or VPS: still their data and their machine, just remote, rather than someone else's cloud.

## Target architecture

Nothing in this section exists yet. It is kept separate from everything above so
a reader can never mistake a plan for a module, and it is deliberately short —
dates and scope belong to [`roadmap.md`](roadmap.md), not here.

- **`application/` completes.** ADR-0018's remaining services — `application/ingest.py`
  and `application/lifecycle.py` — join `query.py`, at which point `cli/main.py`
  is parsing, presentation, and exit codes ([#918](https://github.com/jasonssdev/openkos/issues/918)).
  Lifecycle carries the unsolved piece: the confirmation contracts have to be
  expressed as data before a non-TTY adapter can drive them.
- **`api/` and `mcp/` (MVP 3).** Thin async adapters over the synchronous
  application services — which is the reason those services are being extracted
  first. An adapter built on Typer command internals would duplicate behaviour
  and drift.
- **A published extension surface.** `Producer`/`Consumer` interfaces and an
  entry-point group for third-party ingesters and exporters are a roadmap item
  (MVP 3 and Horizon). No interface, protocol, or entry point for them exists
  today, and adopting one would need its own ADR.
- **Format and store options.** A second vector backend, full OKF import/export,
  and memory projections are all named in the roadmap and unbuilt.

Two long-standing entries in this document turned out to be decisions rather
than pending work, and are recorded here so they are not re-proposed as gaps: a
single `engine.py` orchestrator was **replaced** by ADR-0018's per-use-case
services, and the consolidation of the five `.openkos/` SQLite files into one
`openkos.db` remains an open option that no change has adopted.

## State taxonomy

A workspace holds three kinds of state, and the difference matters the moment
something is lost: one kind is canonical, one rebuilds for free, and one costs
model calls to recreate. They previously sat side by side undifferentiated.

**Canonical, versioned, never reconstructible.** `bundle/` — `index.md`,
`log.md`, and every concept document — plus the sidecars under `bundle/.state/`:
the merge ledgers (`bundle/.state/ledger/<id>.ledger.okf`, [ADR-0013](adr/0013-relocate-merge-ledger-to-bundle-state.md))
and the operator-decision records (`bundle/.state/decisions/<id>.decisions.okf`,
[ADR-0014](adr/0014-durable-pending-work-stores.md)). These hold human judgments
and the bytes needed to reverse a merge. They are committed with the bundle, and
nothing regenerates them. Nothing under `bundle/.state/` is named `*.md`, which
keeps it outside every `rglob("*.md")` walk and therefore outside OKF §9 by
construction rather than by convention.

**Derived, under `.openkos/`, git-ignored, deleted wholesale by `purge`.** All
five are SQLite, all are reconstructible in principle, and they differ in what
reconstruction costs:

| Store | Written by | Rebuild cost | Posture |
| --- | --- | --- | --- |
| `fts.db` | `reindex`, and every bundle-writing verb | free, local | manifest-hash gated; `purge` rebuilds it in line |
| `graph.db` | `reindex`, and every bundle-writing verb | free, local | manifest-hash gated; `purge` rebuilds it in line |
| `vectors.db` | `reindex`, and every bundle-writing verb | embedding calls | `purge` deletes without rebuilding; re-derived lazily |
| `findings.db` | `contradictions`, `curate`, `adjudicate` | **LLM calls** | per-row input digests, not manifest-gated; never rebuilt in line |
| `insight_questions.db` | `query --save` | one embedding | pure cache; a miss is re-embedded, and the store is advisory |

`findings.db` is the one that most repays understanding. It is not an index: a
finding is a *verdict*, not a projection, so a whole-store rebuild cannot
produce one. It carries per-row input digests and decides its own staleness
instead of riding the shared manifest-hash gate, and it holds two tenants in one
file — contradiction verdicts and adjudication verdicts — deliberately, so a
second tenant inherits `purge`'s erasure and `forget`'s sweep instead of opening
a new privacy surface.

`insight_questions.db` sits at the other end: losing it costs nothing but time,
which is exactly why `query --save` degrades to "could not check for
near-duplicates" and files the insight anyway rather than refusing.

**Ephemeral, outside the workspace entirely.** The interprocess mutation lock
([#925](https://github.com/jasonssdev/openkos/issues/925)) lives in a per-user
temp directory keyed by the workspace's real path, not under `.openkos/`. It
holds no content and survives nothing; a refusing command must leave the
workspace byte- and structure-identical, and a lock file created inside it would
break that.

## How the layers arrived

- **MVP 1 (The Compiler)** — delivered: `model`, `bundle`, `state/fts`,
  `llm/ollama`, `extraction`, `retrieval`, `lint`, `lifecycle`, `config`, `cli`.
  The workspace gained `raw/` (text), `bundle/` with its concept folders plus
  `index.md` and `log.md`, `openkos.yaml`, and `AGENTS.md`.
- **MVP 2 (The Graph and Memory)** — delivered: dense retrieval
  (`state/vectorstore.py`) and RRF-fused hybrid search (`retrieval/fusion.py`),
  the graph projection (`graph/`), entity resolution and merge (`resolution/`,
  `bundle/merge.py`, `bundle/ledger.py`), richer `lint` (volatility,
  contradictions), the reference-aware `lifecycle` (`forget`, and the
  irreversible `purge` backed by `vcs/git.py`), and sensitivity enforcement at
  the retrieval boundary — confidential concepts are filtered before any send to
  a backend not verifiably on this machine.
- **MVP 3 (The Runtime and Interoperability)** — in progress. The orchestration
  prerequisite is the application-service extraction above; the adapters follow.
