# AGENTS.md — working on OpenKOS

Operating manual for AI coding agents (and humans) working **on the OpenKOS codebase**. This is the canon: read it first and keep every change consistent with it. (It is distinct from the `AGENTS.md` *inside* a knowledge bundle, which tells an agent how to operate that bundle.)

OpenKOS is an open-source, **local-first engine** that compiles a person's text into a living, portable knowledge base, built on the **Open Knowledge Format (OKF)**. It is alpha; **MVP 1 (The Compiler)** and **MVP 2 (The Graph and Memory)** are complete, and **MVP 3 (The Runtime and Interoperability)** is next.

## Read these first

- `docs/vision.md`, `docs/philosophy.md` — what and why.
- `docs/architecture.md` — repository + bundle structure, conventions, delivery.
- `docs/knowledge-object-model.md` — the data model (the Knowledge Object).
- `docs/roadmap.md` — MVP scoping; `docs/cli.md` — the command surface.
- `docs/tech_stack.md` — technology choices and rationale.
- `examples/good-life-demo/` — a real workspace; the concrete **target output** of MVP 1's `ingest`, and the fixture for the conformance tests.

## Non-negotiable principles

Every change must respect these. A technically good change that violates one is wrong.

- **Local-first & private.** Runs on the user's machine, offline; local models (Ollama). No mandatory cloud, no accounts, no API keys.
- **Adopt OKF, don't invent a format.** Output is always a conformant OKF bundle ([v0.1 spec](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)). OpenKOS is the engine / reference implementation, never its own "standard." This extends to definitions: where OKF has decided something, do not decide it again. Identity is the **Concept ID** — the file path minus `.md` (§2); there is no `id` field. Links are **bundle-relative** and **untyped** — the kind of relationship lives in the prose (§5.1, §5.3). Citations use the `# Citations` heading (§8). Conformance is exactly the three rules of §9 (parseable frontmatter on every non-reserved `.md`; non-empty `type`; reserved files follow §6/§7) — and **the lint is not a conformance checker**: it is our opinion about knowledge health, never OKF's verdict about validity, since OKF explicitly tolerates broken links and missing indexes. Anything we add is an extension carried in frontmatter (legal under §4.1) that degrades gracefully.
- **Immutable sources, living objects.** `raw/` is read-only; concept documents are rewritten over time; history via git + `log.md`.
- **`raw/` sits outside the bundle; `bundle/` is pure OKF.** A workspace is `raw/` (input material, any extension, untouched) + `bundle/` (the OKF bundle root) + the engine's files. Sources are not concepts, so they do not live in the concept tree — which is what makes §9 conformance hold *by construction*, even for files a user drops in by hand. Never put non-concept files inside `bundle/`.
- **Reconstructible.** Every index / embedding / graph rebuilds from the canonical files (markdown + SQLite + git). Derived stores are caches, never the source of truth.
- **Provenance & freshness are first-class.** Every derived object cites its sources; volatile facts carry an `as of` stamp (timeless / snapshot / pointer).
- **Sensitivity across boundaries.** `public | private | confidential` (default `private`); confidential never leaves the device; high-water-mark propagation.
- **Representation, not truth.** OpenKOS preserves representations; it does not validate them and is not an epistemic authority.
- **Human curates, engine maintains.** Consequential changes stay reviewable, not silently automatic.

## Repository conventions

- **Specs are the contract.** Behavior is agreed in `openspec/` before it is built: `openspec/specs/{domain}/spec.md` is the living per-domain contract; `openspec/changes/{change-name}/` carries a change in flight (proposal, delta specs, design, tasks) until archive merges its deltas into the main spec; `openspec/config.yaml` holds the per-phase rules. It is tracked and reviewed like any other file. Required for anything touching the knowledge model, the OKF conformance surface, the ingestion pipeline, or public interfaces (CLI, API, MCP); below that bar, see `CONTRIBUTING.md`.
- **Python 3.12+**, `src/` layout, package `openkos`, `uv` for envs/deps.
- **`pyproject.toml` is the single config source** — deps, the console entry point (`openkos = "openkos.cli.main:app"`), and Ruff / MyPy / Pytest settings.
- **Ship types:** keep `src/openkos/py.typed`.
- **Start lean, grow by MVP.** Create a package when its code arrives — do not scaffold empty folders. MVP 1 needs: `model`, `bundle`, `state`, `llm`, `producers`, `compiler`, `retrieval` (lexical + context), `lint`, `lifecycle`, `config`, `cli`.
- **Extension interfaces are `typing.Protocol`** (`Producer`, `Consumer`, `VectorStore`, `GraphStore`, `LLMBackend`); plugins via entry points.
- **`engine.py` stays thin** (wiring / composition only); behavior lives in subpackages.
- **The core is synchronous.** Async only at the MVP 3 API/MCP edge (which calls the sync engine via a thread pool). Do not make the core async.
- **Layering:** the canonical layer (`model`, `bundle`, `state`) never depends on the derived layer (`retrieval`, `graph`, `memory`).
- **The OKF adapter is one seam.** All knowledge of the format's on-disk shape — frontmatter parsing/emission, reserved files, §9 conformance — lives in `model/okf.py` and nowhere else; the rest of the engine handles Knowledge Objects. OKF is a v0.1 **draft** whose §11 permits breaking major bumps, so this containment is what lets us adopt it safely. Do not spread format knowledge across the codebase.
- **LLM calls** go behind `LLMBackend` and talk to Ollama's OpenAI-compatible endpoint; use Pydantic-validated structured output (e.g. `instructor`) with retry. The compiler is a **deterministic pipeline with LLM steps** — no agent framework in the core.

## Quality gates

- Tests with **pytest** (unit / integration / e2e). Test the deterministic parts thoroughly; spike-then-test the fuzzy extraction parts.
- **Ruff** (lint + format) and **MyPy** (types) must pass. CI runs all three plus a 90% branch-coverage gate (`pytest --cov`) and a packaging build (wheel smoke test); nothing merges without green CI + review. Reproduce the lint/format/type gate locally in one drift-free command — `uv run pre-commit run --all-files` (the hooks are version-pinned to `uv.lock`, so they match CI exactly) — then `uv run pytest --cov`. Note that **`ruff check` alone is not the gate**: `ruff format --check` is enforced separately, so verifying with the linter but skipping the formatter passes locally and fails CI.
- **Conventional Commits** with project scopes: `okf, model, bundle, config, ingest, extract, graph, retrieval, memory, lint, cli, api, mcp, sdd, docs, ci`. The scope is the subsystem or domain touched, not the command — an `init`/`ingest`/`query` change is scoped `cli`. Like packages, the list grows as code lands (line 36); the not-yet-built pipeline scopes are kept as known roadmap.
- **The PR and issue flow is exactly what `CONTRIBUTING.md` and `.github/` define — nothing stricter.** Branch `feat/…` or `fix/…` off `main`; a PR references its issue and any `openspec/` change in prose (`Closes #N` / `Refs #N`); issue-first applies only above the small-obvious-fix bar. This repo has **no `status:*` or `type:*` label gates and no issue-linkage CI check** — the only things that block a merge are green CI and review. A general PR workflow that assumes stricter label or issue machinery does not apply here; this repo's convention wins.

## Architecture Decision Records (ADRs)

When you make a significant, hard-to-reverse decision (a technology, a pattern, an interface, a trade-off), record it as an ADR:

1. Copy `docs/adr/template.md` to `docs/adr/NNNN-short-title.md` using the next number (`0001`, `0002`, …).
2. Fill in context, decision, consequences, and alternatives; set status `Proposed`, add `description`, date, and timestamp.
3. Add a row to the index in `docs/adr/README.md`.

Write it while the forces are still fresh — when the change's design settles the decision, or during implementation if it only emerges there — never afterwards. It is accepted when the change merges, and from then on it is append-only: a later ADR supersedes it; the old one is never edited. Only significant decisions get an ADR — not every change. The log starts with the first code-time decision.

**Spec = what, ADR = why.** They are not duplicates but opposite mechanisms. A spec is a *living* document — archive merges each change's deltas (ADDED / MODIFIED / REMOVED / RENAMED) into `openspec/specs/{domain}/spec.md`, so it is rewritten over time and always describes the present. An ADR is *immutable* once accepted and project-wide, so the log preserves the past. "Ingest MUST copy the source into `raw/` preserving the original" is a spec; "we adopt SQLite + FTS5 over a vector store because local-first and reconstructible" is an ADR. A decision belonging to no single change — "the core does not use LangChain" — has no home in `openspec/` at all: it is an ADR, or a principle above.

## MVP 1 — start here

Build the thinnest vertical slice first: `openkos init` → create the workspace (`raw/`, `bundle/`, `openkos.yaml`, `AGENTS.md`) → `openkos ingest <path>` → copy the source into `raw/` → compile it with the local model into one or more OKF concept documents (with provenance + freshness) → update `index.md` and `log.md`. Lexical retrieval (FTS5) and a cited `query` come next. No graph, no vectors, no reconcile yet. Aim for the shape in `examples/good-life-demo/`.

## Do not

- Invent a competing format, or add a cloud / multi-tenant dependency.
- Make the core async, or pull a heavy agent framework (LangChain, ADK) into the core.
- Add server databases (Postgres, Qdrant, Milvus) or abandoned deps; prefer boring, durable, permissively-licensed tools (SQLite, NetworkX).
- Document personal or single-vendor dev tooling in the repo (a specific AI agent, a memory tool, a local skill cache) — keep it tool-agnostic so anyone can build with just Python + `uv` + these docs. The line is not "no tooling", it is *personal*: an open, interoperable format the project has adopted — OKF for the product, OpenSpec for the process — is tool-agnostic by construction and belongs here; the assistant you happen to drive it with does not. Test a new one by asking whether a contributor on a completely different setup could read and use it. If it only works with yours, it stays out — and stays gitignored.
