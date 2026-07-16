# AGENTS.md — working on OpenKOS

Operating manual for AI coding agents (and humans) working **on the OpenKOS codebase**. This is the canon: read it first and keep every change consistent with it. (It is distinct from the `AGENTS.md` *inside* a knowledge bundle, which tells an agent how to operate that bundle.)

OpenKOS is an open-source, **local-first engine** that compiles a person's text into a living, portable knowledge base, built on the **Open Knowledge Format (OKF)**. It is pre-alpha; **MVP 1** is the first target.

## Read these first

- `docs/vision.md`, `docs/philosophy.md` — what and why.
- `docs/architecture.md` — repository + bundle structure, conventions, delivery.
- `docs/knowledge-object-model.md` — the data model (the Knowledge Object).
- `docs/roadmap.md` — MVP scoping; `docs/cli.md` — the command surface.
- `docs/tech_stack.md` — technology choices and rationale.
- `examples/vector-db-demo/` — a real bundle; the concrete **target output** of MVP 1's `ingest`.

## Non-negotiable principles

Every change must respect these. A technically good change that violates one is wrong.

- **Local-first & private.** Runs on the user's machine, offline; local models (Ollama). No mandatory cloud, no accounts, no API keys.
- **Adopt OKF, don't invent a format.** Output is always a conformant OKF bundle. OpenKOS is the engine / reference implementation, never its own "standard."
- **Immutable sources, living objects.** `raw/` is read-only; concept documents are rewritten over time; history via git + `log.md`.
- **Reconstructible.** Every index / embedding / graph rebuilds from the canonical files (markdown + SQLite + git). Derived stores are caches, never the source of truth.
- **Provenance & freshness are first-class.** Every derived object cites its sources; volatile facts carry an `as of` stamp (timeless / snapshot / pointer).
- **Sensitivity across boundaries.** `public | private | confidential` (default `private`); confidential never leaves the device; high-water-mark propagation.
- **Representation, not truth.** OpenKOS preserves representations; it does not validate them and is not an epistemic authority.
- **Human curates, engine maintains.** Consequential changes stay reviewable, not silently automatic.

## Repository conventions

- **Specs are the contract.** Behavior is agreed in `openspec/` before it is built: `openspec/specs/{domain}/spec.md` is the living per-domain contract; `openspec/changes/{change-name}/` carries a change in flight (proposal, delta specs, design, tasks) until archive merges its deltas into the main spec; `openspec/config.yaml` holds the per-phase rules. It is tracked and reviewed like any other file. Required for anything touching the knowledge model, the OKF conformance surface, the ingestion pipeline, or public interfaces (CLI, API, MCP); below that bar, see `CONTRIBUTING.md`.
- **Python 3.13+**, `src/` layout, package `openkos`, `uv` for envs/deps.
- **`pyproject.toml` is the single config source** — deps, the console entry point (currently `openkos = "openkos:main"`, a pre-MVP stub; it moves to `openkos.cli.main:app` when the `cli` package lands in MVP 1), and Ruff / MyPy / Pytest settings.
- **Ship types:** keep `src/openkos/py.typed`.
- **Start lean, grow by MVP.** Create a package when its code arrives — do not scaffold empty folders. MVP 1 needs: `model`, `bundle`, `state`, `llm`, `producers`, `compiler`, `retrieval` (lexical + context), `lint`, `lifecycle`, `config`, `cli`.
- **Extension interfaces are `typing.Protocol`** (`Producer`, `Consumer`, `VectorStore`, `GraphStore`, `LLMBackend`); plugins via entry points.
- **`engine.py` stays thin** (wiring / composition only); behavior lives in subpackages.
- **The core is synchronous.** Async only at the MVP 3 API/MCP edge (which calls the sync engine via a thread pool). Do not make the core async.
- **Layering:** the canonical layer (`model`, `bundle`, `state`) never depends on the derived layer (`retrieval`, `graph`, `memory`).
- **LLM calls** go behind `LLMBackend` and talk to Ollama's OpenAI-compatible endpoint; use Pydantic-validated structured output (e.g. `instructor`) with retry. The compiler is a **deterministic pipeline with LLM steps** — no agent framework in the core.

## Quality gates

- Tests with **pytest** (unit / integration / e2e). Test the deterministic parts thoroughly; spike-then-test the fuzzy extraction parts.
- **Ruff** (lint + format) and **MyPy** (types) must pass. CI runs all three; nothing merges without green CI + review.
- **Conventional Commits** with project scopes: `okf, ingest, extract, graph, retrieval, memory, lint, cli, api, mcp, docs, ci`.

## Architecture Decision Records (ADRs)

When you make a significant, hard-to-reverse decision (a technology, a pattern, an interface, a trade-off), record it as an ADR:

1. Copy `docs/adr/template.md` to `docs/adr/NNNN-short-title.md` using the next number (`0001`, `0002`, …).
2. Fill in context, decision, consequences, and alternatives; set status `Proposed`, add `description`, date, and timestamp.
3. Add a row to the index in `docs/adr/README.md`.

Write it while the forces are still fresh — when the change's design settles the decision, or during implementation if it only emerges there — never afterwards. It is accepted when the change merges, and from then on it is append-only: a later ADR supersedes it; the old one is never edited. Only significant decisions get an ADR — not every change. The log starts with the first code-time decision.

**Spec = what, ADR = why.** They are not duplicates but opposite mechanisms. A spec is a *living* document — archive merges each change's deltas (ADDED / MODIFIED / REMOVED / RENAMED) into `openspec/specs/{domain}/spec.md`, so it is rewritten over time and always describes the present. An ADR is *immutable* once accepted and project-wide, so the log preserves the past. "Ingest MUST copy the source into `raw/` preserving the original" is a spec; "we adopt SQLite + FTS5 over a vector store because local-first and reconstructible" is an ADR. A decision belonging to no single change — "the core does not use LangChain" — has no home in `openspec/` at all: it is an ADR, or a principle above.

## MVP 1 — start here

Build the thinnest vertical slice first: `openkos ingest <path>` → copy the source into `raw/` → compile it with the local model into one or more OKF concept documents (with provenance + freshness) → update `index.md` and `log.md`. Lexical retrieval (FTS5) and a cited `query` come next. No graph, no vectors, no reconcile yet. Aim for the shape in `examples/vector-db-demo/`.

## Do not

- Invent a competing format, or add a cloud / multi-tenant dependency.
- Make the core async, or pull a heavy agent framework (LangChain, ADK) into the core.
- Add server databases (Postgres, Qdrant, Milvus) or abandoned deps; prefer boring, durable, permissively-licensed tools (SQLite, NetworkX).
- Document personal or single-vendor dev tooling in the repo (a specific AI agent, a memory tool, a local skill cache) — keep it tool-agnostic so anyone can build with just Python + `uv` + these docs. The line is not "no tooling", it is *personal*: an open, interoperable format the project has adopted — OKF for the product, OpenSpec for the process — is tool-agnostic by construction and belongs here; the assistant you happen to drive it with does not. Test a new one by asking whether a contributor on a completely different setup could read and use it. If it only works with yours, it stays out — and stays gitignored.
