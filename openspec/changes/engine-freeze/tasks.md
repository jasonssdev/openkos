# Tasks: The Engine Ships Frozen Inside The App Bundle

Tracking issue: #1451. Inputs: `proposal.md`, `design.md` (authoritative),
`specs/desktop-packaging/spec.md` (a new domain), `specs/desktop-shell/spec.md`
(a delta) and ADR-0056 (Proposed until the archive slice).

PR text names the change in prose ("the `engine-freeze` change") and uses
`Refs #1451` on every slice but the last, which uses `Closes #1451`.

Spec shorthand: `P1` The Engine Is Frozen From The Lock On A Pinned
Interpreter; `P2` The Frozen Engine Keeps The Engine Contract; `P3` The App
Bundle Ships The Freeze Under Resources, Deep Ad-Hoc Signed; `P4` CI Builds The
App And Smoke-Checks Its Engine On macOS; `L1` (desktop-shell, modified) The
Engine Is Located Before It Is Started. `D1`..`D8` are the design decisions.

Strict TDD for every behaviour with a test: observe RED (a failing test or a
compile failure on the missing item), then GREEN, then refactor. New guards are
mutation-checked on their exact line. The build scripts are checked by running
them, and their failure paths by feeding them a broken input.

## Review Workload Forecast

Authored changed lines are additions plus deletions; `uv.lock` is excluded. The
~400-line figure is advisory.

| Slice | Title | Forecast (authored) | Depends on |
|-------|-------|---------------------|------------|
| S1 | Plan: proposal, design, delta specs, tasks | ~600 (docs) | none |
| S2 | The freeze: recipe, build-engine script, smoke check, `freeze` group | ~400 | S1 |
| S3 | The bundle: locator, overlay, build-app script, macOS CI job, docs | ~450 | S2 |
| S4 | CHANGELOG, roadmap, spec merge, ADR-0056 Accepted, archive | ~250 | S3 |

Delivery strategy: stacked-to-main (each slice branches from `main` after the
previous one is squash-merged).

## S1 Plan

- [x] S1.1 Write `proposal.md`, `design.md`, `tasks.md` and the delta specs,
  with the measurements D2, D3 and D5 cite.

## S2 The Freeze

- [x] S2.1 Add the `freeze` dependency group (PyInstaller) to `pyproject.toml`
  and relock; confirm `uv build` metadata is unchanged (P1). RED:
  `tests/unit/test_freeze_group.py` failed with `KeyError: 'freeze'` before the
  group existed; each of its three exclusion tests failed when PyInstaller was
  added to the core dependencies, the `watch` extra and the `dev` group. The
  built wheel's `Requires-Dist` and `Provides-Extra` are unchanged; the sdist
  holds no `desktop/` file.
- [x] S2.2 `desktop/engine/openkos-engine.spec` and `openkos_engine.py`; the
  `.gitignore` negation for the spec (D1).
- [x] S2.3 `desktop/scripts/build-engine.sh`: sync, freeze, interpreter check
  (P1, D2). The check failed, naming what it found, on a tree with no
  interpreter library, with `libpython3.12.dylib`, with both 3.12 and 3.14, and
  with a `Python.framework`.
- [x] S2.4 `desktop/scripts/smoke_engine.py` (P2, D6). Against fake engines each
  check failed on its own fault: one that never writes a ready line, one that
  forks a child, one that ignores stdin EOF (also orphaned by the killed-parent
  check run alone), one whose `doctor` omits the vector line. Against the real
  freeze: ready in 0.47 s, `/v1/status` in 0.49 s, all five checks pass.
- [x] S2.5 CI: the `desktop-bundle` macOS job, building the freeze and running
  the smoke check (P4; S3 extends it to the app bundle). `mypy .` now honours
  `.gitignore` (`exclude_gitignore`), since the freeze under `build/` carries a
  second copy of the `openkos` package.
- [x] S2.6 Gates: Ruff, Ruff format, MyPy, eval self-tests (58 of 58); the
  Python suite in CI.

## S3 The Bundle

- [ ] S3.1 RED: locator tests for the bundled engine, the missing bundled engine,
  the override inside a bundle, the bundle detection from an executable path,
  and the overlay configuration naming the locator's directory (L1, D4).
- [ ] S3.2 GREEN: the locator; mutation-check the bundle detection and the
  missing-engine guard.
- [ ] S3.3 `desktop/src-tauri/tauri.bundle.conf.json` and
  `desktop/scripts/build-app.sh`: bundle, re-sign, verify, interpreter check,
  smoke against the bundled engine, dmg (P3, D3).
- [ ] S3.4 Re-run the orphan measurement on the built app with the new locator
  (no `OPENKOS_ENGINE`): SIGKILL and SIGTERM of the shell (D5).
- [ ] S3.5 CI: extend the `desktop-bundle` job to `build-app.sh` (the Rust
  toolchain, the Tauri CLI, the bundle); record its time (P4, D7).
- [ ] S3.6 Docs: `CONTRIBUTING.md` (building the app), `docs/architecture.md`
  (the new directories).
- [ ] S3.7 Gates: `cargo fmt --check`, `cargo clippy -- -D warnings`,
  `cargo test` and the live test; Ruff, MyPy, eval self-tests.

## S4 Archive

- [ ] S4.1 CHANGELOG, the roadmap's MVP 6 status line.
- [ ] S4.2 Merge the delta specs into `openspec/specs/` (a new
  `desktop-packaging` domain; `desktop-shell` R1 modified); name-match every
  heading; check every requirement against the shipped code.
- [ ] S4.3 ADR-0056 Accepted, with its deviations and `Amends: ADR-0055`;
  ADR-0055 `Amended by ADR-0056` (frontmatter, body, index row).
- [ ] S4.4 Move the change to `openspec/changes/archive/<date>-engine-freeze/`
  with `archive-report.md`; confirm on `origin/main` that the original folder is
  gone.
