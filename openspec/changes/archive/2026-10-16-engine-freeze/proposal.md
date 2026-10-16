# Proposal: The Engine Ships Frozen Inside The App Bundle

Tracking issue: #1451. Governing decisions: ADR-0056 (the engine ships frozen
with PyInstaller one-directory under `Contents/Resources`, deep ad-hoc signed;
Proposed, accepted by this change for what it implements), ADR-0055 (the desktop
shell is Tauri 2 with the engine as a supervised sidecar; Accepted), ADR-0054
(Python 3.14 is the only supported interpreter). Related and not implemented
here: ADR-0057 (the model runtime).

## Intent

The desktop shell (archived as `2026-10-16-desktop-shell`) starts, supervises
and stops the engine, but the engine it runs is the development engine of a
source checkout (`uv run openkos serve`). A person without Python, `uv` or a
checkout cannot run it. The roadmap's MVP 6 needs the engine inside the app,
offline after setup, with no first-run download. ADR-0056 recorded how, from a
throwaway spike: a PyInstaller one-directory freeze shipped under
`Contents/Resources/engine`, the whole bundle deep ad-hoc re-signed.

This change makes that a reproducible build of the repository: a frozen engine
built from `uv.lock`, an app bundle that carries it, a shell that runs it, and a
check that the frozen engine still keeps the engine's contract and leaves no
orphan.

## Scope

### In scope

- **The freeze.** A PyInstaller one-directory build of the engine (the
  `openkos` command line, with the `[api]` extra, so `openkos serve` runs and
  `sqlite-vec` loads), for macOS on Apple Silicon, from a dedicated environment
  synced from `uv.lock` on a uv-managed CPython 3.14. PyInstaller is a new
  `freeze` dependency group: never a runtime dependency, so the wheel's and the
  sdist's metadata do not change.
- **The app bundle.** A Tauri bundle build that ships the freeze as the resource
  `Contents/Resources/engine`, then deep ad-hoc re-signs the whole `.app` and
  verifies it, checks that the bundled interpreter is 3.14, and builds a `.dmg`.
- **The engine locator.** In a bundled app the locator runs the frozen engine
  inside the bundle; outside a bundle it keeps the development `uv run` path;
  `OPENKOS_ENGINE` still overrides both. The handshake (token on stdin, one ready
  line) is unchanged.
- **Orphan prevention re-verified with the frozen launcher,** and a decision,
  with evidence, on ADR-0055's parent-PID poll.
- **A smoke check** of a frozen engine: it starts, answers `GET /v1/status`,
  loads `sqlite-vec`, is one process, exits on stdin EOF and on its parent's
  death.
- **Build commands for contributors,** documented in `CONTRIBUTING.md`:
  `desktop/scripts/build-engine.sh` and `desktop/scripts/build-app.sh`.
- **CI:** a macOS job that builds the freeze and the app and runs the smoke
  check against the engine inside the app.
- **ADR-0056 accepted** at archive, for what is implemented.

### Out of scope

- The signed updater and its minisign key, and the update tarball (ADR-0055).
- Notarization and a Developer ID (the project ships unsigned).
- Guided setup, choosing a workspace in the app, the review screen, the
  TypeScript client. A bundled app still serves `OPENKOS_WORKSPACE` or its
  working directory, as the shell stage left it.
- Windows and Linux freezes, and an Intel or universal macOS build.
- Engine behaviour. No change under `src/` is planned; a parent-PID poll is added
  only if the frozen launcher's evidence needs it, additively and test-first.

## Approach

`desktop/engine/openkos-engine.spec` is the PyInstaller recipe, run by
`desktop/scripts/build-engine.sh` from `build/freeze/venv`, an environment the
script syncs with `uv sync --locked --no-dev --extra api --group freeze
--no-editable --managed-python`. The result is `build/freeze/dist/openkos-engine/`.
`desktop/scripts/build-app.sh` builds it, runs `cargo tauri build` with an overlay
configuration (`tauri.bundle.conf.json`) that turns bundling on and maps the
freeze to `engine/` in the resources, re-signs and verifies the `.app`, checks
the interpreter inside it, runs the smoke check against the bundled engine and
writes the `.dmg`. Everything the build writes sits under ignored directories
(`build/`, `target/`).

## Risks And Rollback

- **Interpreter drift.** The spike shipped the wrong interpreter silently once.
  The build pins a uv-managed 3.14 and fails unless the bundled copy carries
  `libpython3.14.dylib` and no other interpreter.
- **A hidden import the freeze misses** fails only at run time. The smoke check
  starts the frozen engine and calls the API, and runs on every pull request.
- **CI time on macOS runners.** The job installs the Tauri CLI and compiles the
  shell in release mode; both are cached. It runs beside the Python suite.
- **Rollback:** delete `desktop/engine/`, `desktop/scripts/`, the overlay
  configuration, the `freeze` dependency group and the CI job, and revert the
  locator to its development default. The engine is untouched.
