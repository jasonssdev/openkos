# Contributing to OpenKOS

Thanks for your interest in OpenKOS. This project is **pre-alpha and being designed in the open**, which is the best moment to shape it. Contributions of all kinds are welcome — not just code, but ideas, critiques, documentation, and example knowledge bundles.

By participating, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

---

## Ways to contribute

You do not need to write code to make a real difference right now:

- **Shape the design.** React to [`docs/vision.md`](docs/vision.md), [`docs/roadmap.md`](docs/roadmap.md), and [`docs/knowledge-object-model.md`](docs/knowledge-object-model.md). Disagreement, edge cases, and prior art are especially valuable.
- **Improve the docs.** Clarify wording, fix errors, add examples.
- **Report issues and propose features.** Open an issue describing the problem or idea and the use case behind it.
- **Contribute code** once MVP 1 is underway — see the "community can contribute" notes under the current MVP in the [roadmap](docs/roadmap.md) for the clearest entry points (new producers, consumers, extraction strategies, retrieval rankers).
- **Share example OKF bundles** we can use as fixtures and living documentation.

---

## Guiding principles

Every contribution should be consistent with the project's core commitments. If a change conflicts with one of these, it probably needs discussion first:

- **Local-first.** Features must work on a user's machine, offline. The cloud is optional, never required.
- **OKF-conformant.** Output is always a valid [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf) bundle. We adopt the standard rather than fork it.
- **Reconstructible.** Indexes, embeddings, and graphs must be rebuildable from the canonical bundle plus immutable sources. Derived stores are never the source of truth.
- **Provenance and freshness are first-class.** Derived knowledge links back to its source; volatile facts are stamped or expressed as pointers.
- **Human in the loop.** Consequential changes to a user's knowledge stay reviewable, not silently automatic.

---

## Before you start work

**Open an issue before anything larger than a small, obvious fix.** This saves you from building something that cannot be merged. Small changes — typos, doc clarifications, a tightly scoped bug fix — can go straight to a pull request.

For anything that touches the knowledge model, the OKF conformance surface, the ingestion pipeline, or public interfaces (CLI, API, MCP), please discuss the approach in an issue first and reference it in your PR.

Those same changes are then specified before they are built. OpenKOS uses [OpenSpec](https://openspec.dev) ([repository](https://github.com/Fission-AI/OpenSpec)) — an open, tool-agnostic format for writing down what a change must do while the code does not exist yet. The artifacts are plain markdown: a change gets a folder under `openspec/changes/` holding a proposal, the delta specs (the behavior contract), a design, and a task list; when the work lands, those deltas merge into the living per-domain contract under `openspec/specs/`. `openspec/` is tracked in the repository and reviewed like any other file, so you can read the current contract before you start, and reviewers can settle *what* is being built before *how*. You do not need a particular editor or AI tool to take part — writing and reviewing these by hand is fine.

So for a change above that bar the flow is: **issue (discuss the approach) → openspec change → PR**.

---

## Development setup

AI coding agents: [`AGENTS.md`](AGENTS.md) is the project canon — principles, conventions, and quality gates. Read it first.

> The codebase is being bootstrapped. This section describes the intended toolchain so you can prepare; commands will be finalized as MVP 1 lands.

OpenKOS targets **Python 3.13+** and uses [`uv`](https://github.com/astral-sh/uv) for environment and dependency management.

```bash
git clone https://github.com/jasonssdev/openkos.git
cd openkos
uv sync            # create the environment and install dependencies
```

Quality gates (run before pushing):

```bash
uv run ruff check .      # lint
uv run ruff format .     # format
uv run mypy .            # static type checking
uv run pytest            # tests
```

We use pre-commit hooks to run these automatically:

```bash
uv run pre-commit install
```

---

## Pull request process

1. **Fork** the repository and create a branch from `main` with a descriptive name (for example `feat/text-ingester` or `fix/freshness-lint-edge-case`).
2. **Keep the change focused.** One logical change per PR. Smaller PRs are reviewed faster.
3. **Add or update tests** for any behavior change. New functionality needs tests; bug fixes need a regression test.
4. **Update docs** when you change behavior, interfaces, or the knowledge model.
5. **Pass the quality gates** — lint, format, type check, and tests must be green.
6. **Reference the issue** your PR addresses — and the `openspec/` change folder, if it has one — and describe what changed and why.
7. Be ready for review discussion. Maintainers may ask for changes to keep the project coherent.

### Commit messages

Please use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add markdown ingester for MVP 1
fix: stamp volatile facts missed by freshness lint
docs: clarify provenance chain in knowledge-object-model
```

Common types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`.

---

## Reporting bugs

Open an issue that includes:

- what you did, what you expected, and what actually happened;
- your OS and Python version, and how you installed OpenKOS;
- a minimal example or the smallest set of files that reproduces the problem, if possible.

## Reporting security issues

Please do **not** open a public issue for security vulnerabilities. See [SECURITY.md](SECURITY.md) for the private disclosure process (GitHub private reporting or email), and allow reasonable time for a fix before any public disclosure.

---

## License of contributions

OpenKOS is licensed under the [Apache License 2.0](LICENSE). By submitting a contribution, you agree that it will be licensed under the same terms.

---

Thank you for helping build a knowledge system that people actually own.
