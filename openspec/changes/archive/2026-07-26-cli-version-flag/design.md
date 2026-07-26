# Design: CLI `--version` Flag (Closes #181)

## Technical Approach

One eager Typer option on the existing bare top-level callback
(`src/openkos/cli/main.py:106-108`), backed by a single formatting helper that
`doctor` also consumes. No new module, no new dependency, no workspace
resolution on the read path.

Both `is_eager` (`typer/params.py:241`) and `expose_value` (`typer/params.py:229-240`,
forwarded to the Click parameter at `typer/params.py:954`) are real, supported
options in this venv's Typer. See D5 for which one this design uses and why.

## Architecture Decisions

### D1 — Helper returns the full line, not the bare version

**Choice**: `_version_line() -> str` returning `"openkos 0.2.0"` / `"openkos unknown"`.
**Alternatives**: `_resolve_version() -> str` returning `"0.2.0"`; a two-helper split.
**Rationale**: the spec contract is the *line*, not the number, and both consumers
emit that identical line. A bare-version helper would duplicate the `f"openkos {v}"`
format at two call sites — exactly the drift the shared helper exists to prevent.
`_render_check` (`main.py:5882`) is the in-repo precedent for naming an
output-shaping private helper after what it produces; `_resolve_model`
(`main.py:111`) is the precedent for the `_`-prefixed private-helper convention.

### D2 — Module-local alias import, not `metadata.version`

**Choice**: `from importlib.metadata import PackageNotFoundError, version as _pkg_version`.
**Alternatives**: `from importlib import metadata` + `metadata.version(...)`.
**Rationale**: patching `openkos.cli.main.metadata.version` mutates the shared
stdlib module object for every importer; `_pkg_version` is a module-local name
that monkeypatch can rebind with zero global blast radius. `as _`-aliased
imports already exist in this module (`main.py:45-47`).

### D3 — `--version` only, no `-V`

**Choice**: single long flag.
**Rationale**: verified — `grep '"-[a-zA-Z]"' src/openkos/cli/main.py` returns
**zero** matches. This CLI has no short flags anywhere across 19 verbs. Adding
one here would make `--version` the sole exception.

### D4 — Doctor banner is the bare `openkos {version}` line, first

**Choice**: reuse `_version_line()` verbatim, printed before the existing
`openkos doctor: checking environment at {root}` header.
**Alternatives**: prefixed (`openkos doctor: version ...`); a tenth `CheckResult`.
**Rationale**: one string, one format, one test target — a pasted bug report is
grep-identical to `--version` output. An extra check would renumber the checks
documented at `docs/cli.md:325-337` and could touch the exit code.

### D5 — `is_eager=True` alone; `expose_value` omitted

**Choice**: `typer.Option(False, "--version", help=..., is_eager=True, callback=_version_callback)`.
**Alternative**: add `expose_value=False`.
**Rationale** — the decisive fact is that `expose_value=False` does **not** let
`callback()` drop the `version` parameter, which is the only benefit it is
usually reached for. In Typer (unlike raw Click) the function signature *is* the
option declaration: `typer/main.py:1507-1513` seeds `use_params` from
`get_params_from_function(callback)` and then calls `callback(**use_params)` at
`:1524`, so the parameter must exist in the signature or the `--version` option
does not exist at all. With `expose_value=False` the callback still receives
`version` — just the declared default (`param.default`, `:1513`) instead of the
parsed value, and the name is kept out of `ctx.params`. Neither difference is
observable here, because the body ignores the value entirely; the eager
`_version_callback` has already printed and exited.

Given equal behavior, the tiebreakers all point one way:

| Criterion | Verdict |
|---|---|
| Removes the unused parameter? | **No** — required in the signature either way (`typer/main.py:1507-1513`) |
| Typer's own guidance | `typer/params.py:233`: "you probably shouldn't use this parameter, it is inherited from Click and supported for compatibility" |
| Repo precedent | Zero — `grep -c expose_value src/openkos/cli/main.py` returns 0 across all 19 verbs |
| ruff (E,F,I,UP,B,SIM,S,DTZ,PT,PTH,RUF) | `ARG` is not selected (`pyproject.toml:97-109`), so the unused `version` parameter is clean with no `noqa` |
| mypy `strict = true` | Unused arguments are not errors; `version: bool` is fully annotated |

So `expose_value` would add a knob its own maintainers discourage, break this
module's uniform option style, and buy nothing. Omit it.

## Placement and Code Shape

The three new symbols go **between `_LOCK_CONTENTION_MSG` (`main.py:100-103`) and
`@app.callback()` (`main.py:106`)** — mandatory, because `_version_callback` is
referenced as a parameter default and must exist at definition time.

```python
def _version_line() -> str:
    """The single `openkos {version}` line emitted by both `--version` and
    `doctor`'s banner, read from installed distribution metadata (never from a
    constant, so it cannot drift from the built artifact). `PackageNotFoundError`
    -- realistically only a raw `sys.path` run with no install step -- degrades to
    `openkos unknown`: `unknown` cannot be misread as a released build the way
    `0.0.0-dev` would. Staleness (a bumped `pyproject.toml` without `uv sync`) is
    an explicit NON-GOAL: this reports what is installed, not what is checked out."""
    try:
        return f"openkos {_pkg_version('openkos')}"
    except PackageNotFoundError:
        return "openkos unknown"


def _version_callback(value: bool) -> None:
    """Eager `--version` handler: print and exit 0 before Typer resolves any
    subcommand, so the flag works standalone and outside a workspace."""
    if value:
        typer.echo(_version_line())
        raise typer.Exit(code=0)


@app.callback()
def callback(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the installed openkos version and exit.",
        is_eager=True,
        callback=_version_callback,
    ),
) -> None:
    """openkos: local-first engine that compiles text into a portable knowledge base."""
```

**Why eager.** Click processes eager params before non-eager ones and before
subcommand dispatch; without `is_eager=True`, `openkos --version` would fail
Typer's "missing command" check before the callback ever ran. The callback also
never calls `config.require_workspace`, so `--version` is structurally immune to
the workspace guard — the same reason `doctor` runs outside a workspace.

**Callback signature.** Typer introspects `_version_callback`
(`typer/main.py:1812-1839`) and supports `(value)`, `(ctx, value)`, or
`(ctx, param, value)`. The one-argument form is enough here and keeps the
annotation trivial for mypy strict: `value: bool -> None`.

**Exit mechanism.** `raise typer.Exit(code=0)`. The module contains 114
`raise typer.Exit(code=` sites and **zero** bare `typer.Exit()`
(`main.py:6182` is the nearest:
`raise typer.Exit(code=1)`). Explicit `code=0` matches house style exactly.

**Doctor banner.** Insert one line immediately before `main.py:6176`:

```python
typer.echo(_version_line())
typer.echo(f"openkos doctor: checking environment at {root}")  # existing :6176
```

The accumulate-then-render-then-exit-once flow (`main.py:6178-6182`) and the
critical-failure exit are untouched; existing substring assertions on the
header line still pass.

## Static-Analysis Conformance

| Gate | Requirement | How the design satisfies it |
|---|---|---|
| mypy `strict` | every param + return annotated | `_version_line() -> str`; `_version_callback(value: bool) -> None`; `callback(version: bool = ...) -> None` |
| mypy `warn_unreachable` | no dead branch | `try/except` on a real raising call — not narrowing-based |
| ruff `B008` | function call in default | `typer.Option` is in `extend-immutable-calls` (`pyproject.toml:127`) |
| ruff `S` (bandit) | no untrusted input | pure metadata read, no subprocess, no I/O |
| ruff `I` | import order | new import joins the stdlib block near `main.py:3-14` |
| ruff `SIM`/`RUF` | — | no `noqa` needed anywhere |

`version` is an unused parameter — required in the signature for Typer to
declare the option at all (D5) — and `ARG` is not in the selected rule set
(`pyproject.toml:97-109`), so no suppression is required.

## Testing Strategy (Strict TDD — RED first)

| Behavior | File | Test | Mechanism |
|---|---|---|---|
| Prints `openkos <semver>`, exit 0, no subcommand | `tests/unit/test_main.py` | `test_version_flag_prints_version_and_exits_zero` | `CliRunner().invoke(app, ["--version"])`; assert `exit_code == 0` and `stdout.strip()` matches `^openkos \d+\.\d+\.\d+` |
| Reads real installed metadata | `tests/unit/test_main.py` | `test_version_flag_matches_installed_distribution_metadata` | compare against `metadata.version("openkos")` (already imported at `test_main.py:9`) |
| Works outside a workspace | `tests/unit/test_main.py` | `test_version_flag_works_outside_workspace` | `monkeypatch.chdir(tmp_path)` then invoke; exit 0 |
| Single output line only | `tests/unit/test_main.py` | `test_version_flag_prints_exactly_one_line` | `len(result.stdout.strip().splitlines()) == 1` |
| `PackageNotFoundError` → `openkos unknown` | `tests/unit/test_main.py` | `test_version_flag_falls_back_when_distribution_missing` | see below |
| Doctor banner precedes checks | `tests/unit/cli/test_doctor.py` | `test_doctor_prints_version_banner_first` | existing `_fake_ollama_client` stub; assert `stdout.splitlines()[0]` starts with `"openkos "` and the check block is unchanged |

**The unreachable branch.** `.venv/.../openkos-0.2.0.dist-info/` is present, so
`PackageNotFoundError` never fires in the dev loop — yet the 90 % **branch** gate
(`pyproject.toml:86,89`) counts both directions of the `try/except`. Force it by
rebinding the module-local name (D2), never the stdlib origin:

```python
def _raise_not_found(_name: str) -> str:
    raise metadata.PackageNotFoundError("openkos")

monkeypatch.setattr(openkos.cli.main, "_pkg_version", _raise_not_found)
result = runner.invoke(openkos.cli.main.app, ["--version"])
assert result.exit_code == 0
assert result.stdout.strip() == "openkos unknown"
```

Add a direct `_version_line()` unit assertion under the same monkeypatch so the
fallback is covered even if CLI wiring changes. Existing `PT` ruff rules apply:
`monkeypatch` as a fixture parameter, no bare `assert` suppressions needed
(`S101` already per-file-ignored, `pyproject.toml:115-117`).

## CI Change

`.github/workflows/ci.yml:117` — one line becomes two under the same step
(the existing `--isolated --no-project` comment at `:113-115` already explains why
this proves packaged metadata):

```yaml
        run: |
          uv run --isolated --no-project --with dist/openkos-*.whl openkos --help
          uv run --isolated --no-project --with dist/openkos-*.whl openkos --version
```

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/cli/main.py` | Modify | Alias import; `_version_line` + `_version_callback` before `:106`; `--version` on `callback()`; banner before `:6176` |
| `tests/unit/test_main.py` | Modify | 5 `--version` tests incl. forced fallback |
| `tests/unit/cli/test_doctor.py` | Modify | Banner-first assertion |
| `.github/workflows/ci.yml` | Modify | `:117` wheel smoke test extended |
| `docs/cli.md` | Modify | New `### Global options` at the end of `## Conventions` (before `:26`) documenting `--version`, its exact output, exit 0, workspace independence, and the staleness caveat; one sentence at `:323` noting `doctor` leads with the version banner. The banner adds no check, but review found the enumeration at `:325-337` listed only nine of `doctor`'s ten checks (`Workspace vector index present` was missing), so it is corrected to ten as part of this change |
| `docs/testing.md` | Modify | `:169` `openkos --help \| head -3` → `openkos --version`; **delete** the now-false #181 callout at `:172-175`, replaced by one line telling the tester to record that output in the findings log |
| `CHANGELOG.md` | Modify | `[Unreleased]` (`:15`) gains an `### Added` entry for `--version` and the `doctor` banner, citing #181 |
| `README.md` | **No change** | Its only invocation is the ephemeral `uvx openkos --help` (`:68`), an install-works smoke test, not a build-identification step. Adding `--version` there would duplicate `docs/cli.md` without serving the README's narrative. |

## Data Flow

    argv --version ──eager──> _version_callback ──> _version_line ──> _pkg_version("openkos")
                                     │                    │                    │
                                     │                    │            PackageNotFoundError
                                     │                    └──> "openkos unknown" <──┘
                                     └──> typer.echo ──> raise typer.Exit(code=0)

    doctor ──> ... ten checks ... ──> typer.echo(_version_line()) ──> header ──> _render_check loop ──> exit

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary. The change reads installed
distribution metadata already loaded by the running interpreter; no user input
reaches it (`--version` is a valueless flag).

## Migration / Rollout

No migration. Purely additive: no existing command signature, exit code, or
output line changes. Single-commit revert.

## Changed-Line Forecast (review budget 800)

| Area | Lines |
|---|---|
| `src/` | ~32 (helpers + docstrings 22, option 8, banner 1, import 1) |
| `tests/` | ~75 |
| `docs/` | ~30 (`cli.md` ~14, `testing.md` ~8 incl. 4 deletions, `CHANGELOG.md` ~8) |
| `ci/` | ~3 |
| **Total** | **~140** |

400-line budget risk: **Low**. Single PR, no slicing needed.

## Open Questions

None.
