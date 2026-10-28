# Desktop Packaging Specification

## Purpose

How the desktop app is built for a person without Python (ADR-0056): the engine
frozen with PyInstaller in one-directory mode from `uv.lock` on a pinned
interpreter, shipped inside the macOS app bundle as a resource, the bundle deep
ad-hoc re-signed and verified, and every build checked by running the engine it
ships. The frozen engine keeps the engine's launch contract (`local-http-api`)
and runs as one process, so the desktop shell supervises it exactly as it
supervises a development engine (`desktop-shell`).

## Requirements

### Requirement: The Engine Is Frozen From The Lock On A Pinned Interpreter

The repository MUST provide one command that freezes the engine with PyInstaller
in one-directory mode, never one-file, from an environment synced from
`uv.lock` with `--locked`, holding the `openkos` distribution installed
non-editable with its `[api]` extra and the `freeze` dependency group, on a
uv-managed CPython 3.14. PyInstaller MUST be a member of the `freeze` dependency
group only, never a runtime dependency or an extra of the `openkos` package. The
frozen executable MUST be named `openkos-engine` and MUST accept the same
arguments as the `openkos` command line. The build MUST fail unless the frozen
tree carries exactly one interpreter library, `libpython3.14.dylib`, and no
`Python.framework`.

#### Scenario: The freeze is built from the lock

- GIVEN a checkout whose `uv.lock` matches `pyproject.toml`
- WHEN a contributor runs `desktop/scripts/build-engine.sh`
- THEN `build/freeze/dist/openkos-engine/openkos-engine` exists
- AND its `_internal` directory carries `libpython3.14.dylib`

#### Scenario: A wrong interpreter fails the build

- GIVEN the frozen tree carries no `libpython3.14.dylib`, another interpreter library, or a `Python.framework`
- WHEN the build verifies the interpreter
- THEN the build fails naming what it found

#### Scenario: The package metadata is unchanged

- GIVEN the `freeze` dependency group exists
- WHEN the wheel and the sdist are built
- THEN neither declares PyInstaller as a dependency or an extra

### Requirement: The Frozen Engine Keeps The Engine Contract

A frozen engine MUST serve the local API exactly as `openkos serve` does: it
reads the token from the first line of stdin, writes one ready line, and
answers `GET /v1/status` with the token. It MUST load the `sqlite-vec`
extension. It MUST run as a single process, with no launcher process between
the shell and the engine, so EOF on its stdin and the death of the process that
holds the other end of that pipe end it. A smoke check MUST verify each of these
against a given frozen engine, using only the Python standard library and no
model.

#### Scenario: The frozen engine starts and answers

- GIVEN a frozen engine and a fresh workspace
- WHEN the smoke check starts it with a token on stdin
- THEN one ready line arrives within 30 seconds
- AND `GET /v1/status` with the token returns 200 and a JSON object with `concepts`

#### Scenario: The frozen engine is one process and exits on EOF

- GIVEN a ready frozen engine
- WHEN the smoke check lists its process group and then closes its stdin
- THEN the group holds exactly the engine's own PID
- AND the engine exits with status 0 within 10 seconds

#### Scenario: A killed parent leaves no frozen engine

- GIVEN a frozen engine started by a holder process that keeps its stdin
- WHEN the holder is killed with SIGKILL
- THEN the engine is gone within 10 seconds

#### Scenario: sqlite-vec loads in the frozen engine

- GIVEN a frozen engine and no reachable model backend
- WHEN the smoke check runs its `doctor`
- THEN the output reports the vector extension as loadable

### Requirement: The App Bundle Ships The Freeze Under Resources, Deep Ad-Hoc Signed

The repository MUST provide one command that builds the macOS app bundle with
the frozen engine as the resource `Contents/Resources/engine`, never under
`Contents/MacOS`. Before it bundles, the command MUST install the desktop
package from its committed lockfile without updating it and build the page into
`desktop/dist/`, and MUST fail if the built page has no `index.html`. After the
bundle is built the command MUST deep ad-hoc re-sign the whole `.app` and MUST
fail unless `codesign --verify --deep --strict` passes. It MUST fail unless the
engine inside the `.app` passes the interpreter check and the smoke check. It
MUST build a `.dmg` from the re-signed and verified `.app`. Every output MUST be
written under directories the repository ignores.

#### Scenario: The app carries a verified frozen engine

- GIVEN a checkout on macOS with the Rust toolchain, the Tauri CLI, Node and pnpm
- WHEN a contributor runs `desktop/scripts/build-app.sh`
- THEN the page is built into `desktop/dist/` before the bundle
- AND `OpenKOS.app/Contents/Resources/engine/openkos-engine` exists
- AND `codesign --verify --deep --strict` passes on the `.app`
- AND the smoke check passes against the engine inside the `.app`
- AND a `.dmg` holding that `.app` is written

#### Scenario: A broken signature fails the build

- GIVEN the re-signed `.app` does not verify
- WHEN the build checks the signature
- THEN the build fails and no `.dmg` is written

#### Scenario: A page that did not build fails the build

- GIVEN the page build leaves no `desktop/dist/index.html`
- WHEN the build checks the page
- THEN the build fails before it bundles

### Requirement: CI Builds The App And Smoke-Checks Its Engine On macOS

Continuous integration MUST build the frozen engine and the app bundle on an
Apple Silicon macOS runner and run the smoke check against the engine inside
the bundle, on every pull request and push the CI workflow runs on, beside
the existing jobs and without changing them.

#### Scenario: A freeze that breaks the engine fails CI

- GIVEN a change after which the frozen engine no longer answers `/v1/status` or no longer loads `sqlite-vec`
- WHEN CI runs on the pull request
- THEN the macOS bundle job fails

### Requirement: A Release Build Signs The Update Archive Made From The Verified App

`desktop/scripts/build-app.sh --release VERSION` MUST refuse, before building
anything, a version that is not semver without a leading `v`, and a missing
`TAURI_SIGNING_PRIVATE_KEY` or `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`. It MUST
build the app as `VERSION`, and after the deep ad-hoc re-sign, the verification
and the engine smoke check MUST make `build/desktop/OpenKOS.app.tar.gz` from
that same `.app` with `COPYFILE_DISABLE=1`, unpack it and verify the copy with
`codesign --verify --deep --strict`, then sign it with
`cargo tauri signer sign --app-version VERSION` (ADR-0081). `createUpdaterArtifacts`
MUST stay off. CI's macOS bundle job MUST run this path on every pull request
with a key generated for the run, never the project's.

The `desktop-release` job MUST run in the `release` environment, read the two
signing secrets only there, fail before building when either is empty or
`tauri.conf.json` still carries the placeholder public key, never print a
secret, and upload the archive, its `.sig` and a `latest.json` written by
`desktop/scripts/update_feed.py` (the version, the notes, the time and the
`darwin-aarch64` url and signature) with the `.dmg`. A tag with a suffix MUST
be released as a prerelease, which `releases/latest` never offers.

#### Scenario: A release without the key builds nothing

- GIVEN `TAURI_SIGNING_PRIVATE_KEY` is unset
- WHEN `build-app.sh --release 0.7.0` runs
- THEN it exits 1 naming the variable, before the first build step

#### Scenario: The archive holds the app that was checked

- GIVEN a release build
- WHEN the archive is made
- THEN it is unpacked and its copy of the app passes `codesign --verify --deep
  --strict` before it is signed
