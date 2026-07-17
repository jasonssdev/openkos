# Proposal: `openkos init` — create an OKF workspace

## Intent

Nothing runs yet: `src/openkos/__init__.py` is a stub printing `Hello from openkos!`. Every other MVP 1 command is structurally dependent on a workspace that only `init` creates — `ingest` copies into `raw/` and updates `bundle/index.md` and `bundle/log.md`, all of which are init's output. `init` is therefore the thinnest vertical slice, and the first real code the project ships.

## Scope

### In Scope

- `openkos init` creates, in the current directory: `raw/`, `bundle/index.md`, `bundle/log.md`, `openkos.yaml`, `AGENTS.md`.
- Packages, each arriving with its code: `model/okf.py` (the single OKF seam, `AGENTS.md:41`), `bundle/{bundle,index,log}.py`, `cli/main.py` (Typer `app`), `config.py` (module, not subpackage).
- **Console-entry migration** (`pyproject.toml:20` → `openkos.cli.main:app`), plus the two files that reference the stub as a string: `tests/unit/test_main.py`, `.github/workflows/ci.yml:116`.
- **Correct `AGENTS.md:64`**, which names `ingest` as the first slice and contradicts this change.
- Note init's unmet Ollama promise in `docs/cli.md:48`, and correct `docs/cli.md:99`'s stale model line so that file stays self-consistent with what init writes.

### Out of Scope (deferred, named)

| Deferred | Follow-up |
|---|---|
| Ollama model pick/pull (`docs/cli.md:48`, `docs/tech_stack.md:114`) | `add-model-selection` — pulls in `llm/`; CI has no Ollama |
| `git init` in the workspace | `add-workspace-git` |
| Target-directory argument (`init [PATH]`) | `init` operates on cwd only, per `docs/cli.md:37-40` |
| Repo-wide model-guidance refresh — stale `qwen3`/`qwen3:8b` in `docs/tech_stack.md:80,106`, `docs/user-journey.md`, `docs/faq.md`, `docs/roadmap.md` | `refresh-model-guidance` — six docs, no relation to init's code. `docs/cli.md:99` is corrected here only because that file is already in scope. `examples/good-life-demo/openkos.yaml` stays untouched: it is the ingest fixture, not init's output |
| `state/`, `llm/`, `producers/`, `compiler/`, `retrieval/`, `graph/`, `memory/`, `lint/`, `lifecycle/`, `engine.py`, `bundle/provenance.py`, `bundle/git.py` | Arrive with their code |

**Honest gap:** `docs/cli.md:48` contracts that init "Helps you pick a local model (via Ollama)." **This slice does not satisfy that** — there is no hardware probe, no interactive pick, no pull. It does ship a working default, which is not a shortcut but a documented requirement: `docs/tech_stack.md:108` states that "Until that spike runs, the config ships a working default and swapping it is one line in `openkos.yaml`." `docs/cli.md:48` also lists "concept folders" under `bundle/`; **this slice does not create them** (see below). Both stay documented promises, unmet, until their follow-ups land.

## Capabilities

### New Capabilities
- `workspace-init`: creating, validating, and refusing to clobber an OpenKOS workspace; the on-disk shape of a fresh OKF bundle.

### Modified Capabilities
- None (`openspec/specs/` is empty).

## Resolved decisions

| # | Decision | Rationale |
|---|---|---|
| Q7.2 | Fresh `bundle/index.md` = `okf_version: "0.1"` frontmatter, **empty body** — no catalog headings. `log.md` = `# Directory Update Log`, `## YYYY-MM-DD`, `* **Initialization**: Created the bundle structure and the root [index](/index.md).` | The demo's headings are emergent from content (`bundle/index.md:5,10,14,18`); an empty index is that file with entries removed. Log wording transcribes `examples/good-life-demo/bundle/log.md:14`. **The index body shape is new ground** — no empty bundle exists in the repo. |
| Q7.3 | Do **not** pre-create `concepts/`, `people/`, `sources/`, `decisions/`. `ingest` creates each on first write. | `openspec/config.yaml:44` forbids empty scaffolding; `docs/architecture.md:154` calls the grouping configurable; git cannot track an empty directory, so pre-created folders would not survive a commit. Contradicts `docs/cli.md:48`'s "concept folders" — flagged above. |
| Q7.6 | No arguments, no flags. Exit 0 on success; **exit 1 and write nothing if `openkos.yaml` already exists**, or if `raw/`/`bundle/` exist and are non-empty. Non-empty cwd is otherwise allowed. Never overwrites. | `docs/cli.md:46-48` documents no surface at all. Idempotent-by-refusal, not by overwrite — "human curates, engine maintains." Adopting a folder of existing notes is a real workflow; clobbering one is not. |
| Q7.8 | No `chmod`. `raw/` gets default permissions. | Every doc frames immutability as engine discipline, not a filesystem guarantee (`docs/architecture.md:164`). Restrictive modes would block `ingest`'s own writes and behave differently on Windows — a promise no doc makes. |
| Q7.9 | `openkos.yaml` is **generated**: `name` = cwd directory name; `model` = **`qwen3.5:9b`**. `AGENTS.md` is a **static template copy**. | `examples/good-life-demo/openkos.yaml:1` `name: good-life-demo` matches its directory. `examples/good-life-demo/AGENTS.md` contains zero per-workspace variables — verified, it is literal. On the model tag, see below. |

> **Q7.9 — superseded in design (see D5).** `name` is not generated: the
> directory is the single source of truth, so `openkos.yaml` is a
> byte-identical copy of the template like `AGENTS.md`, not a generated file.
> `model` is pinned to `qwen3:8b`. The evidence cited here
> (`examples/good-life-demo/openkos.yaml:1`) had not itself been examined — that
> field was in the example by inertia, and a normative example silently made it
> a requirement. The model subsection below is likewise superseded.

### The model default

Shipping a default is required, not optional: `docs/tech_stack.md:108` mandates that "the config ships a working default" until the model spike runs. The spike decides which default is *best*; it never meant shipping none.

The tag is `qwen3.5:9b`, **verified against `https://ollama.com/library/qwen3.5/tags` on 2026-07-16 rather than written from memory**. Two facts came out of that check:

- **Qwen3.5 has no `8b` tag.** Real tags are `0.8b`, `2b`, `4b`, `9b` (which is `latest`), `27b`, `35b`, `122b`. The 7–8B tier the docs describe has moved to 9B in this family, so `qwen3.5:8b` would hand every new user a failing `ollama pull`.
- **`docs/cli.md:99`'s `qwen3:8b` is a Qwen3-era value** — correct in format, stale in version.

`qwen3.6` also exists now and is deliberately **not** chased: the spike settles the default by measurement, and it stays a one-line config value either way. `docs/tech_stack.md:20` predicted exactly this ("release names and version numbers are volatile facts — the fastest-moving in the project"), which is why the tag was checked against the runtime's own listing.

## Approach

TDD (RED-GREEN-REFACTOR), 90% branch coverage. All frontmatter emission goes through `model/okf.py` and is never inlined in the CLI (`AGENTS.md:41`). `pathlib` only (Ruff PTH); timezone-aware timestamps for `log.md` (Ruff DTZ).

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/{cli,model,bundle}/`, `config.py` | New | The four packages init's code requires |
| `src/openkos/__init__.py` | Modified | Stub `main()` removed |
| `pyproject.toml:20` | Modified | `openkos:main` → `openkos.cli.main:app` |
| `tests/unit/test_main.py` | Modified | 2 of 3 tests assert the stub's identity/output; must be rewritten |
| `.github/workflows/ci.yml:116` | Modified | Wheel smoke test calls `openkos.main()` |
| `AGENTS.md:64` | Modified | Names `ingest` as first slice |
| `docs/cli.md` | Modified | Record the unmet model-pick and concept-folder promises; correct line 99's stale `qwen3:8b` to `qwen3.5:9b` |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| **Green locally, red in CI.** Keeping `main` as an alias for the Typer `app` makes `import openkos; openkos.main()` succeed, but Click exits non-zero with no subcommand. | High | The smoke-test invocation itself changes — exercise the installed console script (`openkos --help`), not a private symbol. Land all three files in one commit. |
| Exceeds the 400-line review budget | High | See below — decision needed before apply |
| Index-body shape has no precedent; `ingest` may want headings pre-seeded | Medium | Empty body is the least-committal choice; `ingest` appends its own sections |
| `qwen3.5:9b` **exists** (verified against Ollama's tag listing, 2026-07-16), but whether it is the **best** default is unverified — no benchmark has been run | Medium | Deciding the best default is precisely the model spike's job (`docs/tech_stack.md:108`). Until then a working default ships, and swapping it is one line in `openkos.yaml` |
| Until `refresh-model-guidance` lands, `docs/tech_stack.md` and `docs/user-journey.md` name `qwen3:8b` while init writes `qwen3.5:9b` | High | A known, time-boxed inconsistency, recorded rather than hidden. `docs/cli.md` is corrected here because it is already in scope; widening to six docs would be scope creep with no relation to init's code |

## Delivery — decision needed before apply

**Realistic estimate: ~550–750 changed lines** (4 new packages + workspace templates + TDD tests at 90% branch + the 3-file entry migration + doc corrections). **This exceeds the 400-line budget.** Recommended chain:

1. **PR 1** (~120): console-entry migration + bare Typer app. `openkos --help` works. pyproject, ci.yml, test_main.py, `cli/`.
2. **PR 2** (~300): `model/okf.py`, `config.py`, `bundle/{bundle,index,log}.py` + unit tests. No CLI wiring yet.
3. **PR 3** (~200): `init` command + templates + `AGENTS.md:64` + `docs/cli.md`.

## Rollback Plan

- **PR 1 is the risky one** — it is the only one that can break a green `main`. Rollback is `git revert` of that single commit, which restores `pyproject.toml:20`, `.github/workflows/ci.yml:116`, `tests/unit/test_main.py`, and the stub `main()` in `src/openkos/__init__.py` together, automatically. The stub is deleted (not aliased) in PR 1 per design decision D6 — keeping it as an alias for the Typer `app` is the evidenced green-local/red-CI trap (import succeeds, Click exits non-zero with no subcommand).
- PRs 2 and 3 are additive: `git revert` removes the new packages; nothing else imports them.
- No data migration, no persisted state, no published artifact. Nothing to un-migrate.

## ADR candidates (for design to weigh — not created here)

- **The console entry point and `cli` public surface** (`openkos.cli.main:app`) — an interface, and hard to reverse once a wheel is published.
- **Removing model selection from `init`'s contract** — a documented user-facing trade-off.

Per `openspec/config.yaml:21-27`, design decides whether either clears BOTH gate conditions.

## Dependencies

`typer` moves from the dev group to runtime dependencies. `python-frontmatter` / `ruamel.yaml` move if `model/okf.py` and `config.py` import them.

## Success Criteria

- [ ] `openkos init` in an empty directory produces `raw/`, `bundle/index.md`, `bundle/log.md`, `openkos.yaml`, `AGENTS.md`.
- [ ] `bundle/index.md` carries exactly `okf_version: "0.1"`; `log.md` carries no frontmatter.
- [ ] `openkos.yaml` is a byte-identical copy of the packaged template carrying `model: qwen3:8b`, with no `name` field (superseded Q7.9 — see D5).
- [ ] The output passes `model/okf.py`'s §9 conformance check (vacuously — zero non-reserved `.md` files).
- [ ] A second `openkos init` exits 1 and writes nothing.
- [ ] `uv run pytest --cov` ≥ 90% branch; `ruff check`/`format --check`/`mypy .` green; `uv build` + wheel smoke test green.
- [ ] `AGENTS.md:64` no longer contradicts the shipped first slice.
- [ ] `docs/cli.md` states plainly which parts of init's contract are not yet met, and its model line matches what init writes.
