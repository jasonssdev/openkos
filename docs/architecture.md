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

**What ships and what is planned are kept apart here.** Everything under "Repository structure" describes the code that exists today; anything not yet built lives in [Target architecture](#target-architecture) or in [`roadmap.md`](roadmap.md), labelled as such. That separation is deliberate: this document previously showed one forward-looking tree with its corrections in footnotes, and a reader could not tell a module that exists from one that does not.

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
│   ├── vcs/git.py                # CANONICAL layer: history, revert, purge's history rewrite
│   ├── state/                    # DERIVED layer: SQLite stores under .openkos/ (see State taxonomy)
│   │   ├── derived.py            # the shared opener + manifest-hash gate
│   │   ├── fts.py  vectorstore.py  reindex.py
│   │   ├── findings.py  adjudications.py       # same file, two tenants
│   │   ├── revision_findings.py  # decision-revision verdicts, a further tenant of the same file
│   │   ├── edge_suggestions.py  question_vectors.py
│   │   ├── pending_queue.py      # the pending-work queue, a further tenant of findings.db
│   │   └── jobs.py               # jobs.db: the unattended runner's outcome log
│   ├── graph/                    # DERIVED layer
│   │   ├── base.py  sqlite_graph.py  analysis.py
│   │   └── proximity.py  summary.py
│   ├── retrieval/                # DERIVED layer
│   │   ├── pool.py  fusion.py    # candidate pool, RRF fusion
│   │   ├── history.py            # bounded revision-history walk over superseded objects
│   │   └── answer.py             # context assembly, citations, generation
│   ├── extraction/               # source text → Knowledge Objects
│   │   ├── concept.py  evidence.py  judge.py
│   ├── resolution/               # identity, contradiction, typing decisions
│   │   ├── candidates.py  similarity.py  normalize.py
│   │   ├── insight_identity.py  adjudication.py
│   │   ├── contradiction.py  reconciliation.py
│   │   ├── decision_subject.py  decision_revision.py   # decision-revision detector
│   │   └── edge_typing.py  volatility_typing.py
│   ├── llm/                      # model runtime abstraction
│   │   ├── base.py  ollama.py  openai_compatible.py  prompting.py  parsing.py
│   ├── application/              # synchronous use-case services (ADR-0018)
│   │   ├── query.py  ingest.py  lifecycle.py
│   │   ├── status.py  list_service.py  lint.py  doctor.py   # read verbs' cores
│   │   ├── concept_read.py       # `get`'s curated field set
│   │   ├── pending.py  consistency.py   # pending-work predicates; MCP consistency warnings
│   │   ├── next_action.py        # `next`'s ranked tier engine
│   │   ├── repair.py             # OKF v0.2 migration plan/apply
│   │   ├── runner.py  runtime.py  budget.py  watch.py  watch_notify.py   # the unattended runner: jobs, halt control, call budget, inbox watch, optional native wake-up
│   │   ├── queue_producers.py    # advisor findings enqueued as pending work
│   │   ├── lock_wait.py  commit_phase.py   # the workspace lock held for a commit phase only
│   │   ├── revisions.py  revisions_report.py   # decision-revision plan and report
│   │   ├── backends.py           # the one seam that resolves/constructs an LLM client
│   │   └── consent.py            # confirmation gates staged as typed data
│   ├── cli/                      # Typer entry layer
│   │   ├── main.py  curate.py  observability.py
│   ├── mcp/                      # stdio MCP adapter (read-only), async edge over the sync core
│   │   ├── transport.py  server.py  tools.py  gate.py
│   ├── config.py                 # openkos.yaml + WorkspaceLayout
│   ├── lint.py  lifecycle.py  sensitivity.py
│   ├── event_dates.py  source_date.py  # bounded event-date resolver; a Source's event_date from evidence
│   ├── read_outcome.py           # shared "a read verb's check could not run" vocabulary
│   ├── fsio.py  lock.py  userstate.py  # filesystem primitives; interprocess lock; per-user state/lock/log directories
│   ├── prompt_budget.py  source_title.py
│   └── py.typed
├── tests/                        # unit/ (incl. unit/e2e) · smoke/ (packaging)
├── evals/                        # measurement harnesses
├── examples/                     # runnable example bundles
├── docs/                         # including adr/
├── openspec/                     # the spec contract: specs/{domain}/ · changes/ · config.yaml
├── pyproject.toml · uv.lock
└── README.md · LICENSE · NOTICE · CHANGELOG.md · .github/
```

The principles that shape it:

- **Each package is a piece of the architecture.** `model` is the Knowledge Object; `bundle` + `vcs` are the durable canonical layer; `state` + `retrieval` + `graph` are the derived layer; `extraction` + `resolution` are the pipeline that turns text into objects and then decides what they mean; `lint`/`lifecycle`/`sensitivity` are the disciplines; `cli` and `mcp` are entry layers, one synchronous over stdin/args, one async over stdio.
- **The Protocol seams that exist today** are `GraphStore` (`graph/base.py`), `VectorStore` (`state/vectorstore.py`), and `LLMBackend` and `Embedder` (`llm/base.py`). They define the shapes their implementations satisfy (`sqlite_graph.py`, the `sqlite-vec` store, `ollama.py`, `openai_compatible.py`). They are internal seams, not a published plugin API: OpenKOS ships no `Producer`/`Consumer` interface and no entry-point group. That extension surface is a roadmap item, not present code — see [`roadmap.md`](roadmap.md).
- **One resolver seam constructs every LLM client.** `application/backends.py` resolves the configured `backend` (`ollama`, the default, or `openai-compatible`), the effective endpoint, and the environment-only API key, then constructs the matching concrete client — the CLI and MCP adapters call through it rather than importing `OllamaClient`/`OpenAICompatibleClient` directly. Adding a backend widens this one seam; it does not touch the pipeline packages that call `LLMBackend`/`Embedder`.
- **Use-case services, not one orchestrator.** [ADR-0018](adr/0018-application-layer-for-bounded-context-services.md) chose narrow synchronous services under `application/` over a single `engine.py`, so each use case owns its own composition instead of one module owning all of them. The write use cases are `query.py`, `ingest.py`, and `lifecycle.py`; the read verbs (`status`, `list`, `lint`, `doctor`, `get`) have their own read cores, so an adapter is a thin layer over shared code rather than a second implementation; and `consent.py` holds the confirmation contracts as typed data so a non-TTY adapter can answer a gate without re-deriving its prompt. `cli/` keeps parsing, presentation, exit codes, and the shared write mechanics the services call through rather than own.
- **The derived layer is reconstructible — but not uniformly, and not for free.** The derived SQLite stores under `.openkos/` sit at several different points on that scale. See [State taxonomy](#state-taxonomy) below, which is the one place that distinction is written down.

## Repository conventions

A few conventions keep the repository clean as it grows:

- **A package is created when its code arrives.** The tree above holds no empty scaffolding, and this document does not list folders that do not exist. What is planned is named in [Target architecture](#target-architecture) and dated in [`roadmap.md`](roadmap.md).
- **`pyproject.toml` is the single source of config** — dependencies, the console entry point (`openkos = "openkos.cli.main:app"`), and the Ruff / MyPy / Pytest settings all live there.
- **Specs are the contract, and they live in `openspec/`.** Behavior is agreed before it is built: `openspec/specs/{domain}/spec.md` is the living per-domain contract, and `openspec/changes/{change-name}/` carries a change in flight — proposal, delta specs, design, tasks — until it lands and its deltas merge into the main spec. The directory is tracked and reviewed like any other file, so the contract is readable by contributors rather than private to whoever wrote the code. `openspec/config.yaml` configures that process only; it does not compete with `pyproject.toml`, which remains the single source of config for the toolchain.
- **Ship types.** Include an empty `src/openkos/py.typed` marker so type information is published to tools and to packages that extend OpenKOS.
- **Internal seams are `typing.Protocol`.** Structural typing lets an implementation satisfy a seam without importing or subclassing it. Today this is used inside the engine (the graph, vector, LLM, and embedding seams); publishing any of it as a third-party extension point is a roadmap item and would need its own ADR.
- **The core is synchronous.** The CLI, the application services, the extraction pipeline, and the stores are plain sync code. The `mcp` adapter is the one async edge over that core today: it owns the only event loop reaching into `mcp/`, and each tool call runs the synchronous application services on its own worker thread (ADR-0021, ADR-0027). A future local API would form its async edge the same way; parallel work such as batch embedding also uses a thread pool from sync code. The core itself is not made async — which is why ADR-0018's services are specified as synchronous.
- **Layering is a followed convention, partially guarded by AST tests.** The canonical layer (`model`, `bundle`, `vcs`) does not depend on the derived layer (`state`, `retrieval`, `graph`); derived depends on canonical, never the reverse. `fsio`, `lock` and `userstate` are leaf modules that import only other leaf modules (`userstate` imports nothing from `openkos`), so either layer may use them. AST import guards pin parts of it: `tests/unit/bundle/test_layering.py`, `tests/unit/resolution/test_layering.py`, `tests/unit/retrieval/test_layering.py`, `tests/unit/mcp/test_layering.py`, and the canonical-layer check in `tests/unit/graph/test_base.py`. The rest is not automated; a tool such as import-linter would guard every boundary in CI and is not wired.
- **The OKF adapter is one seam.** Everything that knows the on-disk shape of the format — parsing and emitting frontmatter, the reserved-file structure, the conformance rules of §11 — lives in `model/okf.py` and nowhere else. The rest of the engine works with Knowledge Objects and never touches the format directly. This is deliberate risk containment: OKF is pre-1.0 (v0.2), and §12 permits a major version to rename required fields or change reserved filenames. Keeping the format behind one module makes a spec revision a contained change to one file instead of a search across the codebase, and it is the reason we can adopt a young standard without betting the engine on it.

These conventions describe the code as it stands; they change when a decision changes, and a change worth keeping becomes an ADR.

## Workspace structure (the user's knowledge base)

The directory a user works in — the git repository `openkos init` creates. We call it a **workspace**, and it holds three things that are deliberately kept apart: the immutable sources, the compiled bundle, and the engine's own files. By convention it lives at the root of the user's home directory and is named `knowledge` (`~/knowledge`) — one machine can hold several workspaces, but that is the default a user should meet first.

To *read* the knowledge in an editor it is **`bundle/`** that opens, not the workspace: as an Obsidian vault (Open folder as vault) or as a VS Code folder. Bundle documents link with bundle-root-absolute paths (`[Stoicism](/concepts/stoicism.md)`), which resolve only when the vault root *is* the bundle — open the workspace root instead and no inter-document link resolves.

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
    ├── insight_questions.db   # cached question embeddings for `query --save`
    └── jobs.db           # OPERATIONAL: the unattended runner's job outcomes
```

*(A content-addressed `raw-store/` for binary originals is described under
"Source material and versioning" below and is not yet built; today `raw/` holds
text-shaped sources only.)*

*(Those stores do not share one lifecycle, and the differences are
load-bearing — which rebuild for free, which cost model calls, and which is a
verdict rather than a projection. [State taxonomy](#state-taxonomy) is the one
place that is written down; see also `design D1` in the `performance-caching`
change record and [ADR-0014](adr/0014-durable-pending-work-stores.md).)*

*(`bundle/.state/` is the one directory inside the bundle that holds no concepts: the merge-ledger sidecars [ADR-0013](adr/0013-relocate-merge-ledger-to-bundle-state.md) relocated there out of survivors' frontmatter, and the operator-decision sidecars [ADR-0014](adr/0014-durable-pending-work-stores.md) placed beside them. Nothing under it is named `*.md`, and — since [ADR-0019](adr/0019-dot-directories-are-not-knowledge.md) — it is also a dot-directory, which every `*.md` walk in the engine (`okf.iter_bundle_markdown`) now excludes structurally regardless of a file's suffix; either reason alone keeps it invisible to those walks and therefore outside OKF §11 rule 1 — a structural exclusion rather than one every walk must remember, and `lint` carries a dedicated check that flags any `.md` file appearing there as a regression against it. Unlike `.openkos/`, this state is durable and canonical: it is versioned with the bundle, not git-ignored.)*

### Why `raw/` is outside the bundle

This split is the load-bearing decision in the layout, and it is worth being explicit about why, because the obvious alternative — dropping `raw/` inside the bundle next to the concepts — is what most tools would do.

An OKF bundle is a bundle *of concepts*. Raw sources are not concepts; they are input material. Keeping them inside the concept tree mixes two different kinds of thing, and the format notices: OKF §11 requires every non-reserved `.md` file in a bundle to carry frontmatter with a `type`. An ingested third-party markdown file carries neither — so a `raw/notes.md` inside the bundle would make the whole bundle non-conformant, and adding frontmatter to it would violate immutability. Working around that (renaming the copy, say) would only paper over the real issue: the file is in the wrong place.

Putting `raw/` beside the bundle rather than inside it resolves this **by construction, not by convention**:

- **The invariant is structural.** Nothing a user drops into `raw/` — by hand, bypassing `ingest` entirely, which "editing by hand" explicitly allows — can break conformance, because `raw/` is not in the OKF tree. An invariant that depended on every file arriving through the CLI would not be an invariant.
- **Sources keep their real names and extensions.** `meeting-notes.md` stays `meeting-notes.md` and still renders in Obsidian. No spec detail leaks into the user's filenames.
- **`bundle/` becomes a true unit of distribution.** Share it and you ship pure conformant OKF — no sources, no `openkos.yaml`, no operating manual mixed in. That also lines up with sensitivity: knowledge is frequently shareable when the transcript it came from is not. One caveat belongs here rather than in a footnote, because conformance and shareability are not the same property and it is easy to read the first as implying the second. Conformance holds: `bundle/.state/` carries no `*.md` file, so the merge-ledger sidecars are outside §11's reach by construction. Shareability does not follow automatically, because those sidecars hold, per merge, the absorbed object's full verbatim bytes, the survivor's pre-merge bytes, and whole-file snapshots of every third-party document the merge retargeted — including bodies whose sensitivity was `confidential` when they were frozen. Zip `bundle/` today and you ship the current concepts *and* everything a past `merge` folded away. This is what `forget`'s ledger sweep exists for, and it is worth checking before a bundle leaves the machine.
- **Text and binary sources get one treatment.** Both live in `raw/`; both are represented in the bundle by a Source concept carrying the hash and description. The bundle always holds the manifest, never the blob (see below).

The concept folders inside `bundle/` are grouped by type as one sensible convention; the layout itself is fixed — the engine always resolves `raw/` and `bundle/` beside `openkos.yaml`, and the config deliberately declares no layout keys it would not honor; making the layout genuinely configurable (for example a flat structure — in OKF the file path is the concept's identity, not its type) remains future work. `.openkos/` holds only derived, rebuildable state; it is what you `.gitignore`. What you version is `bundle/` plus the text-shaped `raw/` — which leads directly to the next section.

### The one bridge out of the bundle

A bundle that never points outside itself would lose its provenance, so exactly one document type is allowed to reach out: the **Source concept** in `bundle/sources/`. Its `resource` field names the original it summarizes (`raw/call-with-maria-2026-07-14.txt`, resolved from the workspace root) — which is precisely what OKF designed `resource` for: a URI identifying the underlying asset, normally outside the bundle.

Everything else stays inside. Derived objects link to their Source concepts with ordinary bundle-relative links (`[Call with Maria Salazar — 2026-07-14](/sources/call-with-maria-2026-07-14.md)`), never the raw file directly. OKF §6.3 describes this pattern exactly — a `references/`-style subdirectory that "conventionally mirrors external material... as first-class concepts" within the bundle. The result is that the bundle's internal links always resolve, and a single, well-defined seam connects it to the sources on disk. The machine-readable `provenance` list still records the original Source Concept IDs for the engine's own use, and `sources` (generated from it) gives an external OKF consumer the same lineage without any trust path inside the engine changing.

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

- **Desktop app** (Tauri/Electron/native; planned as MVP 6, shell technology undecided) — one installer and an icon, no terminal; the friendliest path for non-technical users, and where a runtime and model can be bundled. Note that a Tauri/Electron app *is* a web UI in a native shell, so "web vs desktop" is a false dichotomy at the technical level.
- **Local web UI (`localhost`)** — the engine would serve a browser UI from its own local API (planned with the desktop app in MVP 6; not built). Nothing leaves the machine; this is how Jupyter, Ollama, and most self-hosted tools work. Best wrapped inside the desktop app so the user never starts a server by hand.
- **Static HTML explorer** — a single self-contained HTML file that reads a bundle with no server (the approach of Google's OKF visualizer). Zero install, ideal for browsing knowledge read-only.
- **Editor plugin** — because the bundle is plain markdown, Obsidian and VS Code already act as a GUI over the knowledge; a plugin adds OpenKOS actions inside a tool the user already uses.
- **Chat / agent (MCP)** — the user "just talks to" OpenKOS from an AI client. For some non-technical users this is the lowest-friction interface of all.
- **CLI** — for technical users and automation.

The key architectural point: all of these are **thin adapters over the same local engine**. Today `cli` and `mcp` both exist; a local `api` is planned with the desktop app (MVP 6). The application services under `application/` are what let `mcp` be a thin adapter rather than a second implementation of the CLI's read verbs — the same reason a future `api` would extract nothing new. Adding a front-end never touches the core; UIs stack on top of one engine. For non-technical users the likely order is desktop app first, then chat/MCP, then an editor plugin.

The one thing outside the local-first spirit is a **cloud-hosted, multi-tenant** service holding users' knowledge. A legitimate middle ground is **self-hosting** — the user runs the local web UI on their *own* server or VPS: still their data and their machine, just remote, rather than someone else's cloud.

## Target architecture

Nothing in this section exists yet. It is kept separate from everything above so
a reader can never mistake a plan for a module, and it is deliberately short —
dates and scope belong to [`roadmap.md`](roadmap.md), not here.

- **`api/` (MVP 6).** A thin async adapter over the synchronous application
  services, following the same pattern `mcp/` already ships: an adapter built
  on Typer command internals would duplicate behaviour and drift.
- **A published extension surface.** `Producer`/`Consumer` interfaces and an
  entry-point group for third-party ingesters and exporters are a Horizon item
  (the stable Python API they build on is MVP 6). No interface, protocol, or entry point for them exists
  today, and adopting one would need its own ADR.
- **Format and store options.** A second vector backend, full OKF import/export
  (MVP 5), and memory projections (Horizon) are all named in the roadmap and unbuilt.

Two long-standing entries in this document turned out to be decisions rather
than pending work, and are recorded here so they are not re-proposed as gaps: a
single `engine.py` orchestrator was **replaced** by ADR-0018's per-use-case
services, and the consolidation of the five `.openkos/` SQLite files into one
`openkos.db` remains an open option that no change has adopted.

## State taxonomy

A workspace holds four kinds of state, and the difference matters the moment
something is lost: one kind is canonical, one rebuilds for free, one costs
model calls to recreate, and one is operational bookkeeping that nothing else
depends on.

**Canonical, versioned, never reconstructible.** `bundle/` — `index.md`,
`log.md`, and every concept document — plus the sidecars under `bundle/.state/`:
the merge ledgers (`bundle/.state/ledger/<id>.ledger.okf`, [ADR-0013](adr/0013-relocate-merge-ledger-to-bundle-state.md))
and the operator-decision records (`bundle/.state/decisions/<id>.decisions.okf`,
[ADR-0014](adr/0014-durable-pending-work-stores.md)). These hold human judgments
and the bytes needed to reverse a merge. They are committed with the bundle, and
nothing regenerates them. Nothing under `bundle/.state/` is named `*.md`, and it
is also a dot-directory ([ADR-0019](adr/0019-dot-directories-are-not-knowledge.md)),
which keeps it outside every bundle `*.md` walk on either ground and therefore
outside OKF §11 by construction rather than by convention.

**Derived, under `.openkos/`, git-ignored, deleted wholesale by `purge`.** All
of these are SQLite, all are reconstructible in principle, and they differ in what
reconstruction costs:

| Store | Written by | Rebuild cost | Posture |
| --- | --- | --- | --- |
| `fts.db` | `reindex`, and every bundle-writing verb | free, local | manifest-hash gated, refreshed per document with a whole rebuild as the fallback; `purge` rebuilds it in line |
| `graph.db` | `reindex`, and every bundle-writing verb | free, local | manifest-hash gated, refreshed per document with a whole rebuild as the fallback; `purge` rebuilds it in line |
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

**The pending-work queue is a tenant of `findings.db`.** It holds the proposals
the unattended runner computed and a human has not yet decided (`openkos
pending` lists them). Like a verdict it is not an index and cannot be
re-derived for free, so it rides the same erasure and sweep paths; unlike a
verdict it is only ever a proposal: applying one still goes through the verb
and the confirm gate that would have made the change by hand.

**Per-document refresh, whole-rebuild fallback.** `fts.db` and `graph.db` keep
a per-document manifest next to the bundle-level hash, so a verb that changed a
few documents refreshes only those rows. The whole rebuild stays the fallback
for anything the incremental path cannot vouch for (a store without the
manifest, a version mismatch, a manifest that does not reproduce the hash, a
failure mid-refresh), and an incrementally refreshed store must equal a whole
rebuild's rows. That is what makes the cache disposable in practice.

`insight_questions.db` sits at the other end: losing it costs nothing but time,
which is exactly why `query --save` degrades to "could not check for
near-duplicates" and files the insight anyway rather than refusing.

**Operational, under `.openkos/`, disposable and non-authoritative.** `jobs.db`
is the first store of this kind: the unattended runner's log of what each job
did and how it ended (completed, budget exhausted, timed out, failed). It is not
an index of anything and no answer depends on it; deleting it loses only the
history of unattended runs. `purge` deletes it with the rest of `.openkos/`.

**Ephemeral, outside the workspace entirely.** The interprocess mutation lock
([#925](https://github.com/jasonssdev/openkos/issues/925)) lives in a `locks`
directory under the account's OpenKOS state directory (`userstate.py`), keyed
by the workspace's real path, not under `.openkos/`. The directory is resolved
from the account database rather than the environment, because a lock is a
rendezvous that every process of the user must find in the same place. It holds
no content and survives nothing; a refusing command must leave the workspace
byte- and structure-identical, and a lock file created inside it would break
that ([ADR-0036](adr/0036-lock-a-short-commit-phase-in-a-per-user-state-directory.md)).
The unattended runner's logs live in the sibling `logs` directory of the same
per-user state directory, for the same reason: a log is written on every run and
must never appear in the workspace's tree or its version history.

### Locking a commit phase, not a verb

A verb that writes the bundle does not hold the workspace lock for its whole
run. It splits into a **compute phase**, which reads the bundle, calls the model
and builds a plan with no lock held, and a **commit phase**, which takes the
lock, re-validates every file the plan depends on against current disk state,
writes, and releases. Contention is therefore bounded by the short write rather
than by a model call, which is what lets a daemon and a person share a
workspace: whichever arrives second refuses (exit 3) or retries within `--wait`,
and a plan that went stale while it was being computed is a drift refusal, never
a silent overwrite. `purge` is the exception and holds the lock for its whole
run, because it rewrites history. Plain `query` takes no lock at all; only
`--save`'s write is a commit phase
([ADR-0036](adr/0036-lock-a-short-commit-phase-in-a-per-user-state-directory.md)).

### The unattended runner

The runner is an ordinary synchronous use-case service in `application/`
([ADR-0037](adr/0037-unattended-work-computes-and-enqueues-only.md)), not a
second orchestrator and not a framework: the `daemon` verb owns the process
concerns (signals, the poll loop, the log file, the exit code) and drives the
runner one job at a time, with the effects the runner cannot own handed in as
ports. A job ends with exactly one recorded outcome. The runner performs no
consequential write: it refreshes the derived stores, counts lint findings and
runs the advisors, and every proposal becomes pending work rather than a change
to `bundle/`. Only the runner is budgeted (model calls per pass and per day,
sources per pass, a job deadline); a command run from the CLI is never limited.
A watched inbox folder is outside the workspace and read-only to OpenKOS
([ADR-0038](adr/0038-a-watched-folder-is-an-external-inbox.md)).

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
- **MVP 3 (The Ask Surface)** — delivered: the application-service extraction
  for the read verbs (`application/concept_read.py`, `application/consistency.py`,
  `application/backends.py`), and the `mcp` adapter built on it. A local REST
  API and full OKF import/export were split out to their own arcs; see
  [`roadmap.md`](roadmap.md).
- **MVP 4 (The Unattended Engine)** — delivered: the runner, call budget and
  inbox watch (`application/runner.py`, `budget.py`, `watch.py`), the
  `state/jobs.py` job record, the pending-work queue in `findings.db`, the
  commit-phase workspace lock in a per-user state directory, per-document
  refresh of the derived FTS and graph stores, and the `daemon` and `pending`
  verbs. See [The unattended runner](#the-unattended-runner).
