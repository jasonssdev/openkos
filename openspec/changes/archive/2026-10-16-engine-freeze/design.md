# Design: The Engine Ships Frozen Inside The App Bundle

Inputs: `proposal.md`, `specs/desktop-packaging/spec.md` (a new domain),
`specs/desktop-shell/spec.md` (a delta), ADR-0056, ADR-0055, ADR-0054, the
archived `2026-10-16-desktop-shell` change (its archive report names what this
stage owns), and measurements taken on the development machine (Apple Silicon,
macOS 27.0, PyInstaller 6.22.3, Tauri CLI 2.12.3) while writing this plan.

## D1 The freeze recipe is committed; the build is a script

```
desktop/
├── engine/
│   ├── openkos-engine.spec   the PyInstaller recipe (one-directory)
│   └── openkos_engine.py     its entry point: the `openkos` CLI app
├── scripts/
│   ├── build-engine.sh       sync the build env, freeze, verify the interpreter
│   ├── build-app.sh          freeze, bundle, re-sign, verify, smoke, dmg
│   └── smoke_engine.py       the smoke check of one frozen engine
└── src-tauri/
    └── tauri.bundle.conf.json  the bundle overlay (resources, signing)
```

The entry point is the whole `openkos` command line (`app(prog_name="openkos")`),
not a serve-only script: the frozen executable then accepts exactly what the
console script accepts, so the locator runs it the same way as an
`OPENKOS_ENGINE` override (`serve --workspace <dir>`), and the smoke check can
run `init` and `doctor` against the very tree that ships.

The recipe uses `collect_all` for `openkos` and `sqlite_vec`, as the spike's
`--collect-all` flags did. `sqlite_vec` ships its loadable extension as package
data, and `openkos` imports `openkos.localapi` lazily inside `serve` and reads
its templates as package data; import analysis sees neither. Uvicorn's
string-named loop and protocol modules are covered by the hook PyInstaller's
contributed hooks ship for it (measured: `serve` answers with no extra hidden
import). `setuptools` and `pkg_resources` are excluded: they reach the build
environment only as PyInstaller's own dependencies. One-directory, never
one-file, as ADR-0056 decides.

The root `.gitignore` ignores `*.spec` (the gitignore.io Python template); a
negation re-includes `desktop/engine/openkos-engine.spec`.

## D2 The build environment is synced from the lock on a uv-managed 3.14

`build-engine.sh` syncs a dedicated environment, never the contributor's
`.venv`:

```
UV_PROJECT_ENVIRONMENT=build/freeze/venv \
  uv sync --locked --no-dev --extra api --group freeze --no-editable \
          --managed-python --python 3.14
```

- `--locked` fails on a lock that has drifted, as every CI job does.
- `--no-dev` keeps pytest, mypy and the rest out of the freeze; `--extra api`
  brings FastAPI, Pydantic and uvicorn; the new `freeze` group brings
  PyInstaller. Dependency groups are not package metadata, so the wheel and the
  sdist are unchanged (checked in the build slice by diffing the built
  artifacts' metadata).
- `--no-editable` installs `openkos` as a real distribution, so its
  `dist-info` (read by `openkos --version`) ships in the freeze.
- `--managed-python` uses a uv-managed CPython (python-build-standalone), the
  same on a contributor's machine and on a CI runner, instead of whichever
  framework Python is first on `PATH`.

**Deviation from the spike, measured.** The spike froze a framework Python, so
the freeze carried `Python.framework` with symlinks, which Tauri's resource copy
dereferenced (the 5.4 MB interpreter stored more than once, `.app` 57 MB). A
uv-managed interpreter is not a framework build: the freeze carries
`_internal/libpython3.14.dylib` and has **zero symlinks**, so nothing is
duplicated. Measured with this recipe: freeze 40 MB, `.app` 51 MB, `.dmg`
29 MB (spike on 3.14: 34, 57 and 30 MB). The interpreter check of ADR-0056
therefore becomes: the bundled engine carries exactly one interpreter library,
`libpython3.14.dylib`, and no `Python.framework`. The build fails otherwise, both
on the freeze and on the copy inside the `.app`, which is where the spike's
wrong interpreter hid.

## D3 The bundle: an overlay configuration, the freeze as a resource, no wrapper

`tauri.conf.json` keeps `bundle.active: false`, so `cargo build`, `cargo test`
and the existing `desktop` CI job never need a freeze on disk (`tauri-build`
checks that resources exist at compile time). `build-app.sh` passes an overlay:

```json
{ "bundle": { "active": true, "targets": ["app"],
  "resources": { "../../build/freeze/dist/openkos-engine/": "engine/" },
  "macOS": { "signingIdentity": "-", "minimumSystemVersion": "13.0" } } }
```

The freeze lands at `OpenKOS.app/Contents/Resources/engine/openkos-engine`,
under `Resources` and never `MacOS` (ADR-0056). Tauri's copy keeps the
executable bits (measured).

**No `externalBin` wrapper.** ADR-0055 planned a small executable declared as
Tauri's `externalBin`, with the freeze beside it, because the spike launched the
engine through `tauri-plugin-shell`'s sidecar API, which only runs an
`externalBin`. The shell stage replaced the plugin with `std::process`
(desktop-shell D3: the plugin's child handle cannot close stdin, the engine's
lifeline). With `std::process` the locator can name the frozen executable inside
`Resources` directly, so the wrapper has no job left. Dropping it also removes the
failure ADR-0055 records for a script wrapper (its signature stripped by the
update tarball). This amends ADR-0055's wrapper clause; ADR-0056 records it on an
`Amends:` line when it is accepted (D8).

**Re-sign and verify, then the dmg.** After `cargo tauri build --bundles app`:

1. `codesign --force --deep -s - OpenKOS.app`, then
   `codesign --verify --deep --strict OpenKOS.app` must pass (ADR-0056).
2. The interpreter check of D2 on `Contents/Resources/engine`.
3. The smoke check (D6) against the engine inside the `.app`.
4. The `.dmg` is made from the re-signed `.app` with `hdiutil create`
   (`-format UDZO`, with an `Applications` link), so the image carries exactly
   the verified bundle. Tauri's own dmg target would be built before the
   re-sign, and its Finder scripting is fragile on headless runners.

Outputs stay in ignored directories: the freeze under `build/freeze/`, the
`.app` under `desktop/src-tauri/target/release/bundle/macos/`, the `.dmg` under
`build/desktop/`.

## D4 The locator: bundled, development, or an override

```
OPENKOS_ENGINE set          -> that executable, `serve --workspace <ws>`
shell inside an .app bundle -> <App>.app/Contents/Resources/engine/openkos-engine,
                               `serve --workspace <ws>`; missing -> final failure
otherwise                   -> `uv run --project <checkout> openkos serve ...`
```

"Inside a bundle" is read from the shell's own executable path: a parent
directory named `MacOS` whose parent is `Contents` gives
`Contents/Resources/engine/openkos-engine`, which is where Tauri resolves
resources on macOS. It is a pure function of a path, so it is unit-tested
without building a bundle, and it needs no compile-time flag: a debug build run
from `target/` is development, the same binary copied into a bundle is
bundled.

A bundled shell whose frozen engine is missing or not a file fails with
`engine_not_located`, naming the path, and never falls back to `uv`: a bundle
with no engine is a broken build, and running a checkout's engine from an
installed app would hide it. The workspace rule is unchanged.

The locator owns two names the bundle overlay and the recipe also spell:
`engine/` and `openkos-engine`. A unit test reads `tauri.bundle.conf.json` and
asserts its resource target and source match the locator's constants, so a
renamed directory fails `cargo test` instead of shipping an app that cannot find
its engine (the class of the spike's silently wrong interpreter).

## D5 Orphan prevention with the frozen launcher; no parent-PID poll

ADR-0055's Decision names a stdin-EOF plus parent-PID guard; the engine has the
stdin lifeline only, and the shell stage asked this stage to recheck it with the
frozen launcher, which could hold its own copy of the pipe.

Measured on the bundled app (the shell binary inside `OpenKOS.app`, the frozen
engine inside its `Resources`, workspace from `OPENKOS_WORKSPACE`):

- **PyInstaller's one-directory bootloader adds no process.** The engine's PID is
  the process the shell spawned, in its own process group, with no child. Only
  one-file mode forks a second process (to unpack); one-directory runs Python
  in-process. So no launcher holds a copy of the pipe.
- **SIGKILL of the shell:** the engine was gone in about 0.3 s.
- **SIGTERM of the shell:** the engine was gone in about 0.4 s.

**Decision: no parent-PID poll.** The engine's stdin-EOF lifeline reaches the
real engine with the frozen launcher, and the shell's process-group kill on a
graceful stop reaches it too (it is the group leader). A poll would add a thread
and a timing constant to the engine for no case the evidence shows. The engine
is not changed. The smoke check (D6) keeps this a check rather than a claim: it
asserts the frozen engine is a single process and that killing its parent ends
it, on every CI run. ADR-0056 records this as amending ADR-0055's guard clause.

## D6 The smoke check

`desktop/scripts/smoke_engine.py <engine-executable>` uses the standard library
only, so any Python 3.14 runs it. Against a fresh temporary workspace (made with
the frozen engine's own `init`, with a pinned git identity) it checks:

1. **Starts and answers.** `serve --workspace` with a fresh token on stdin; one
   ready line within 30 s; `GET /v1/status` with the bearer token returns 200
   and a JSON object with `concepts`. Spawn-to-ready and spawn-to-status times
   are printed.
2. **One process.** The engine's process group holds exactly the spawned PID.
3. **Stdin EOF ends it.** Closing stdin ends the engine with exit 0 within
   10 s.
4. **A killed parent leaves no engine.** An intermediate holder process starts
   the engine (its own session, stdin piped from the holder) and is then
   SIGKILLed; the engine must be gone within 10 s.
5. **`sqlite-vec` loads.** `doctor` (with `OLLAMA_HOST` pointed at a closed
   port, so no model is needed) must print `[PASS] Vector extension loadable`;
   its exit code is ignored because the model checks fail without Ollama.

It exits non-zero naming the first failed check. It is linted and type-checked
by the repository's Ruff and MyPy gates like any other Python file.

## D7 CI: one macOS job, on every pull request

```
desktop-bundle (macOS): checkout -> setup-uv (3.14) -> rust toolchain + cache
  -> cargo install tauri-cli (cached binary) -> desktop/scripts/build-app.sh
```

`build-app.sh` already runs the interpreter check, the re-sign, the verification
and the smoke check against the engine inside the `.app`, so the job's steps are
the script. Measured locally: the freeze takes about 12 s, the release build of
the shell about 30 s on a warm machine, the dmg 16 s. On a cold runner the
release compile and the Tauri CLI install dominate; the Rust cache is keyed on
`Cargo.lock` and also keeps the installed CLI. The job runs on `macos-latest`
(Apple Silicon) beside the Python suite, with no path filter, for the reason the
workflow's header gives (a filtered required check never reports). If its
measured time on a pull request exceeds about 15 minutes, the build slice moves
it to a path filter plus a schedule and says so.

## D8 ADR gate

No new ADR. The decisions here are ADR-0056's, implemented; the two places the
implementation differs are recorded in ADR-0056 when it is accepted at archive,
which this repository's lifecycle allows for a Proposed ADR:

- the uv-managed, non-framework interpreter and the libpython check (D2);
- the frozen engine run directly from `Resources` with no `externalBin` wrapper,
  and no parent-PID poll (D3, D5), which amend ADR-0055. ADR-0056 carries an
  `Amends: ADR-0055` line, and ADR-0055's status becomes `Amended by ADR-0056`
  in its frontmatter, body and index row.
