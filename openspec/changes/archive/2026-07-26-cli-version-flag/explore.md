# Exploration: cli-version-flag (Closes #181)

## Current State

- `src/openkos/cli/main.py:106-108` — the top-level Typer callback is bare
  (`@app.callback()` + a docstring only), no options, no workspace resolution.
  This is the exact attachment point for an eager `--version` option.
- Workspace resolution (`config.require_workspace`, `src/openkos/config.py:251`)
  is opt-in per command; the callback never calls it, so an eager `--version`
  declared there is automatically immune to any workspace guard — the same
  reason `doctor` works outside a workspace.
- `doctor` (`src/openkos/cli/main.py:5895-6182`) accumulates `CheckResult`s and
  renders them via `_render_check` in a loop (`main.py:6178`), exiting 1 only if
  a critical check failed. No `--json` mode exists anywhere in `main.py`. A
  version line fits best as a leading `typer.echo` at `main.py:6176`, before the
  check rendering — a banner line avoids renumbering the checks documented
  in `docs/cli.md:325-337`. (Review later found that enumeration listed only
  nine of `doctor`'s ten checks; the banner decision is unaffected, but the
  docs were corrected as part of this change.)
- No version constant or metadata read exists anywhere in `src/`
  (`grep -rn "importlib.metadata\|__version__" src/` returns nothing). This
  confirms the issue's premise: the version lives only in `pyproject.toml:3`
  (`version = "0.2.0"`).
- `tests/unit/test_main.py:9` already imports `from importlib import metadata,
  resources` and uses `metadata.entry_points(...)` — the natural home for the
  new `--version` tests, with the import pattern already in place.
- CLI test pattern (`tests/unit/cli/test_doctor.py:21,27`): `from typer.testing
  import CliRunner`, module-level `runner = CliRunner()`, `runner.invoke(app,
  [...])`. `tests/unit/test_main.py:23-29` already does `runner.invoke(app,
  ["--help"])`, which extends directly to `["--version"]`.
- CI wheel smoke test (`.github/workflows/ci.yml:117`): `uv run --isolated
  --no-project --with dist/openkos-*.whl openkos --help` installs ONLY the built
  wheel. Adding `openkos --version` there is the strongest available proof that
  the flag reads real packaged metadata rather than a hardcoded string.
- Docs needing updates: `docs/testing.md:172-175` **already references #181 by
  name** ("There is no `openkos --version` yet") and must be removed;
  `docs/testing.md:169`'s `openkos --help | head -3` build-identification step
  should become `openkos --version`; `docs/cli.md` needs a `--version` /
  global-options entry; `CHANGELOG.md:15`'s empty `[Unreleased]` section is
  where the entry lands.

## Affected Areas

| Path | Change |
| --- | --- |
| `src/openkos/cli/main.py:106-108` | Attach the eager `--version` option to `callback()` |
| `src/openkos/cli/main.py:5895-6182` | Add a version line to `doctor` output |
| `tests/unit/test_main.py` | `--version` tests: happy path, fallback, standalone exit, combined with a subcommand |
| `tests/unit/cli/test_doctor.py` | Assert the version line is present in `doctor` output |
| `.github/workflows/ci.yml:117` | Candidate `openkos --version` smoke-test addition |
| `docs/testing.md:169-175`, `docs/cli.md`, `CHANGELOG.md:15` | Documentation |

## Approaches Considered

1. **Eager `typer.Option(..., callback=..., is_eager=True, expose_value=False)`
   on the existing `callback()`**, backed by a small shared helper
   `_resolve_version()` wrapping `importlib.metadata.version("openkos")` in
   `try/except PackageNotFoundError`.
   - Pros: matches Typer's idiomatic eager-option pattern (the same mechanism
     `--help` uses); trivially testable with `CliRunner`; a single source of
     truth reusable by `doctor`.
   - Cons: none material.
   - Effort: Low.
2. **Module-level `__version__` computed at import time**, referenced by the
   callback and by `doctor`.
   - Pros: exposes `openkos.__version__` as a plain attribute for future
     consumers.
   - Cons: unconditional metadata lookup on every CLI invocation; adds a second
     "location" for the version; marginal benefit over approach 1.
   - Effort: Low-Medium.
3. **Dedicated `openkos version` subcommand.** Explicitly rejected by the issue;
   recorded only for completeness — it fails the "must work standalone, without
   a subcommand" requirement.

## Recommendation

Approach 1: an eager `--version` on `callback()` at `main.py:106-108`, backed by
one shared `_resolve_version()` helper reused by both `--version` and `doctor`.
Least code, satisfies every issue constraint, and reuses existing test
scaffolding.

## Risks

- **Corrected framing of "metadata unavailable".** Verified that
  `.venv/lib/python3.13/site-packages/openkos-0.2.0.dist-info/` and
  `openkos.pth` both exist, so `importlib.metadata.version("openkos")` resolves
  successfully (`"0.2.0"`) in this repo's normal dev loop and will NOT raise
  `PackageNotFoundError` in the common case. The real risk is **staleness**, not
  unavailability: bumping `pyproject.toml`'s version without rerunning
  `uv sync` / `pip install -e .` leaves the old dist-info in place, so
  `--version` would silently print a stale-but-plausible number.
  `PackageNotFoundError` realistically fires only with a raw `sys.path` hack and
  no install step. Test coverage should treat the stale-version case as
  explicitly out of scope rather than implying `PackageNotFoundError` is the
  primary failure mode.
- The exact unknown-fallback string (`"unknown"` vs `"0.0.0-dev"`) is undecided —
  defer to the design phase.
- `doctor`'s version-line placement (banner vs. a `CheckResult`) affects
  `docs/cli.md`'s check enumeration if implemented as a check; a banner
  line avoids renumbering.
- `docs/testing.md:172-175`'s existing `#181` callout must be removed as part of
  this change, or it becomes stale and self-contradictory the moment the flag
  ships.

## Ready for Proposal

Yes. Attachment point, degrade path, test pattern, and documentation touch
points are all concretely identified with `file:line` citations.
