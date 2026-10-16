# Archive Report: engine-freeze

**Archived**: 2026-10-16 (S4, the last slice of the change)
**Archive folder**: `openspec/changes/archive/2026-10-16-engine-freeze/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issue**: #1451

## Final State At Close

- **Implementation**: three slices before this one merged to `main`, each one squash-merged PR, stacked to `main` in order. S4 (this archive) closes the issue.

| Slice | PR | Squash commit | Authored lines (additions + deletions; `uv.lock` excluded) | CI `test (3.14)` | CI `desktop bundle (macOS)` |
|-------|----|---------------|-------------------------------------------------------------|------------------|-----------------------------|
| S1 plan | #1453 | `274d8de6` | about 565 (docs) | passed | (not yet present) |
| S2 the freeze, smoke check, `freeze` group, CI job | #1454 | `a90008fb` | about 565 | 14m24s | 56s (freeze and smoke only) |
| S3 locator, bundle overlay, build-app script, CI bundle, docs | #1455 | `6cd5e1db` | about 430 | 9m52s | 12m27s (every cache cold) |
| S4 CHANGELOG, roadmap, spec merge, ADRs, archive | this PR | | docs only | | |

- **Shipped surface**: `desktop/engine/` (the PyInstaller recipe `openkos-engine.spec` and its entry point `openkos_engine.py`), `desktop/scripts/` (`build-engine.sh`, `build-app.sh`, `smoke_engine.py`), `desktop/src-tauri/tauri.bundle.conf.json`, the bundled branch of the engine locator (`EngineSource`, `engine_source`, `bundled_engine`, `LocateError::BundledEngineMissing`), the `freeze` dependency group, the `desktop bundle (macOS)` CI job, `exclude_gitignore` for MyPy, and the `.gitignore` negation for the recipe.
- **Engine**: unchanged. No file under `src/` was touched. The parent-PID poll was not added (see below).
- **ADR-0056**: Accepted, in all three places (frontmatter, the `**Status:**` body line, the `docs/adr/README.md` row), with the implementation's evidence added to its Context and its Decision stated as implemented. It carries `Amends: ADR-0055`, and ADR-0055 is `Amended by ADR-0056` in its three places.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| desktop-packaging | Created | A new domain. The delta's 4 ADDED requirements became the living spec under a new Purpose section: The Engine Is Frozen From The Lock On A Pinned Interpreter; The Frozen Engine Keeps The Engine Contract; The App Bundle Ships The Freeze Under Resources, Deep Ad-Hoc Signed; CI Builds The App And Smoke-Checks Its Engine On macOS. |
| desktop-shell | Modified | The Engine Is Located Before It Is Started replaced by the delta's text (the bundled branch, its missing-engine refusal); two scenarios added (A bundled shell runs its frozen engine; A bundle without its engine is final). The Purpose paragraph on the locator updated. The delta's "(Previously: ...)" note was not carried into the living spec. |

### Heading Name-Match

Every `### Requirement:` and `#### Scenario:` heading of each delta was compared as an exact line against the living spec after the merge: desktop-packaging 14 headings (4 requirements, 10 scenarios), desktop-shell 6 headings (1 requirement, 5 scenarios); 0 missing, no duplicate heading in either living spec. No REMOVED or RENAMED headings.

## Requirement-vs-Code Check

Each requirement was read against the shipped files, not against the task list.

| Requirement | Result |
|-------------|--------|
| The Engine Is Frozen From The Lock On A Pinned Interpreter | Met. `build-engine.sh` runs `uv sync --locked --no-dev --extra api --group freeze --no-editable --managed-python --python 3.14` into `build/freeze/venv`, then the recipe, which is `--onedir` (an `EXE` with `exclude_binaries=True` and a `COLLECT`) and names both the executable and the directory `openkos-engine`. The entry point is `openkos.cli.main:app`. `check_interpreter` fails unless `_internal` holds exactly `libpython3.14.dylib` and no `Python.framework` exists in the tree. `tests/unit/test_freeze_group.py` pins PyInstaller to the `freeze` group and out of the dependencies, every extra and the `dev` group. |
| The Frozen Engine Keeps The Engine Contract | Met. `smoke_engine.py` uses the standard library only and points `OLLAMA_HOST` at a closed port: ready line within 30 s, `GET /v1/status` 200 with `concepts`, the process group equal to the engine's PID and no child, exit 0 within 10 s of stdin EOF, the engine gone within 10 s of its holder's SIGKILL, and `doctor` printing `[PASS] Vector extension loadable`. |
| The App Bundle Ships The Freeze Under Resources, Deep Ad-Hoc Signed | Met. `tauri.bundle.conf.json` maps `build/freeze/dist/openkos-engine/` to `engine/`; `build-app.sh` refuses an engine in `Contents/MacOS`, runs `codesign --force --deep --sign -` and `codesign --verify --deep --strict`, the interpreter check and the smoke check on the engine inside the `.app`, verifies again, then writes `build/desktop/OpenKOS.dmg` with `hdiutil` from a `ditto` copy. `set -euo pipefail` stops it before the `.dmg` on any failure. Outputs are under `build/` and `target/`, both ignored; the staging copy is a removed temporary directory. |
| CI Builds The App And Smoke-Checks Its Engine On macOS | Met. The `desktop bundle (macOS)` job runs `build-app.sh` on `macos-latest` (Apple Silicon) in `ci.yml`, whose triggers are every pull request into `main` or `tracker/**` and every push to `main`; it has no path filter and changes no other job. |
| The Engine Is Located Before It Is Started (modified) | Met. `locate` takes `OPENKOS_ENGINE` first; `EngineSource::Bundled` comes from `engine_source`, which requires the executable's directory to be `MacOS` inside `Contents`, and runs `Contents/Resources/engine/openkos-engine` with `serve --workspace`, or returns `BundledEngineMissing` naming the path when it is not a file, with no `uv` fallback; otherwise the checkout's `uv run`. The supervisor maps any locator error to a final `failed` with `engine_not_located`. |

## Orphan Prevention And The Parent-PID Poll

The shell stage asked this stage to recheck ADR-0055's parent-PID guard with the frozen launcher, which could have held its own copy of the engine's stdin pipe. Measured on the built `OpenKOS.app` on the development machine (Apple Silicon, macOS 27.0), with the locator choosing the bundled engine:

- PyInstaller's one-directory bootloader runs Python in its own process: the engine was the shell's direct child, alone in its process group, with no child process. No launcher holds the pipe.
- SIGKILL of the shell: the engine was gone in 0.25 s (twice). SIGTERM: 0.26 s and 0.30 s. A graceful quit of the app opened with `open` and quit through Apple Events: no engine left.

Decision: **no parent-PID poll**. The stdin-EOF lifeline alone reaches the real engine. The smoke check keeps this true on every CI run (one process; exit when its parent is killed). ADR-0056 records it as amending ADR-0055.

## Measurements

| | This build (uv-managed CPython 3.14.6) | Spike on 3.14 (ADR-0056) |
|---|---|---|
| Frozen engine | 40 MB (41 MB on CI) | 34 MB |
| `.app` | 51 MB | 57 MB |
| `.dmg` | 28 MB | 30 MB |
| Bundled engine spawn to ready line / to `/v1/status`, local, warm | 0.33 s / 0.35 s | 0.078 s median to `/health` |
| The same on a CI runner, first run | 1.42 s / 1.48 s | not measured |

The freeze itself takes about 12 s. The spike's startup number was measured differently (spawn to `/health` through the app); the bar it was held to (3 s) is met by both. A true post-reboot cold start is still not measured; the pre-registration's R1 check owns it.

## Deviations From The Design And From ADR-0056

1. **The interpreter is uv-managed, not a framework build** (design D2). The spike's `Python.framework` check became a `libpython3.14.dylib` check, and the symlink duplication ADR-0056 recorded disappeared. ADR-0056's Decision and Consequences were updated before acceptance.
2. **No `externalBin` wrapper** (design D3). The shell spawns the frozen executable in `Resources` directly, which also removes the script wrapper's lost-signature problem from the update path.
3. **The CI job arrived in S2,** building the freeze and the smoke check, and S3 extended it to the whole app, instead of arriving in S3 alone. S2 was checked on macOS as a result.
4. **`mypy .` honours `.gitignore`** (`exclude_gitignore = true`), not foreseen by the design: the freeze under `build/` carries a second copy of the `openkos` package, which MyPy reported as a duplicate module.
5. **No `exploration.md`.** The plan was written from the archived shell change, ADR-0056 and measurements taken while planning.

## Verification Record

- Strict TDD. S2: `test_freeze_group.py` failed RED with `KeyError: 'freeze'`; each exclusion test failed when PyInstaller was moved into the dependencies, the `watch` extra and the `dev` group. S3: 5 locator tests failed RED against `todo!()` stubs.
- Mutations on the exact guarded lines, each restored from a byte copy: S3, 7 of 7 killed (the `MacOS` and `Contents` checks, `is_file` to `exists`, the missing-engine guard, the `Resources` name, the overlay's target and source names).
- The build scripts' failure paths: the interpreter check failed on four broken trees (no library, 3.12, 3.12 and 3.14 together, a `Python.framework`); each smoke check failed against a fake engine with its fault (no ready line, a forked child, ignored stdin EOF, also orphaned by the killed-parent check run alone, no vector line).
- Gates: `cargo fmt --check`, `cargo clippy --locked --all-targets -- -D warnings`, `cargo test --locked` (42) and the live test; `ruff check .`, `ruff format --check .`, `mypy .` (673 files) and `evals/run_self_tests.py` (58 of 58); `OPENKOS_REQUIRE_API_BASE_REF=1 uv run pytest --cov` locally on `main` after S3 (`6cd5e1db`, every code change of the change): 13,524 passed, 6 skipped, coverage 96.76%; the full suite in CI on every slice.
- Not verified: the Gatekeeper click-through and a quarantined launch of this build (unsigned, and every rebuild has a new cdhash); the page's rendering in the window.

## Later Stages

Not part of this change, and not claimed by ADR-0056's acceptance:

- **The updater** signed with a project-owned minisign key, and its update tarball (ADR-0055).
- **Notarization and a Developer ID** (the project ships unsigned; the download page's walk-through is product work).
- **Guided setup** (ADR-0057), **choosing a workspace in the app**, **the review screen**, and **the TypeScript client** generated from the OpenAPI schema. A bundled app still serves `OPENKOS_WORKSPACE` or its working directory.
- **Windows, Linux, Intel and universal macOS builds.**

## Follow-ups

- None filed. No product decision was left open by this stage; the open stages above are the roadmap's.
