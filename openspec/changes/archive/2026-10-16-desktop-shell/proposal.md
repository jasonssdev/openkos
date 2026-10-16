# Proposal: The Desktop Shell Supervises The Engine Sidecar

Tracking issue: #1446. Governing decisions: ADR-0055 (the desktop shell is
Tauri 2, with the engine as a supervised sidecar; Proposed, accepted by this
change for what it implements), ADR-0058 (loopback HTTP, a per-launch token over
stdin, Host and Origin checks), ADR-0061 (the local API is a FastAPI adapter
started by `openkos serve`). Related and not implemented here: ADR-0056 (the
PyInstaller freeze) and ADR-0057 (the model runtime).

## Intent

MVP 6 ships OpenKOS as an application a person installs without a terminal
(`docs/roadmap.md`, MVP 6). The engine half is in place: `openkos serve` runs the
local API, reads its token from stdin and announces its port on one ready line.
Nothing yet starts it for a person. The roadmap's desktop deliverable says the
shell "starts, supervises and stops the engine", so the user never starts a
server by hand. This change builds that shell, and only the shell: the first
stage of the app, on which the freeze, guided setup and the review screen are
later stages.

## Scope

### In scope

- **A Tauri 2 shell in the repository,** under a new top-level `desktop/`
  directory: a Rust crate (`desktop/src-tauri/`) and the web view's page
  (`desktop/ui/`). `docs/architecture.md` records the directory and its shape.
- **Supervision of one engine,** running `openkos serve` exactly as
  `openkos.localapi.launch` defines it: a fresh token as the first line of
  stdin, the port read from the engine's single JSON ready line, readiness when
  that line arrives, a bounded restart policy when the engine exits on its own,
  a clean stop when the shell quits, and no orphaned engine when the shell is
  force-quit.
- **An engine locator,** one module that decides which engine runs. In this
  stage it runs the development engine of the source checkout the shell was
  built from (`uv run --project <checkout> openkos serve`), or an executable
  named by `OPENKOS_ENGINE`. The workspace is `OPENKOS_WORKSPACE`, or the
  shell's working directory. The freeze stage replaces the development default
  there and nowhere else.
- **A minimal page** that proves the round trip: it shows the engine's state and,
  once the engine is ready, reads `GET /v1/status` with the token and shows the
  workspace's folder, source and concept counts and the number of open decisions.
  Copy is English.
- **CI:** a `desktop` job that runs `cargo fmt --check`, `cargo clippy` with
  warnings denied and `cargo test` (the supervisor against fake engines), and then
  the shell against the real engine of the checkout. It runs beside the Python
  jobs.
- **Docs:** `docs/architecture.md` (the new directory), `CONTRIBUTING.md` (how to
  build and check the shell), the `desktop` commit scope in `AGENTS.md`,
  `CONTRIBUTING.md` and `openspec/config.yaml`, the CHANGELOG, and the roadmap's
  MVP 6 status line.
- **ADR-0055 accepted** at archive, for what this stage implements.

### Out of scope

- **The freeze** (ADR-0056): no PyInstaller build, no `externalBin` wrapper, no
  bundle resource, no signing. The locator is where it lands.
- **The updater** and its minisign key (ADR-0055's update half).
- **Guided setup, workspace choice in the UI, the review screen, the
  TypeScript client generated from the OpenAPI schema.** A person who is not a
  developer cannot use this stage yet: the workspace is chosen by an environment
  variable and the engine comes from a source checkout.
- **Any change to the engine.** The shell conforms to `openkos serve` as it is;
  no Python changes are planned. If one becomes strictly necessary it is
  additive and lands in its own slice.
- Windows. The shell compiles there, but the process-group stop and the
  force-quit guarantee are tested on macOS and Linux only.

## Approach

The shell is a thin supervisor, as ADR-0055 decides. One Rust thread owns the
engine process: it locates the engine, spawns it with stdin, stdout and stderr
piped and in a process group of its own, writes the token, waits for the ready
line, and publishes a state the web view reads through one Tauri command. The
web view then calls the local API itself, from an origin the engine already
allows (`tauri://localhost`, `http://tauri.localhost`).

The shell never closes the engine's stdin until it means to stop it. That pipe
is the engine's lifeline (`launch.py` exits on EOF), and the operating system
closes it when the shell dies by any route, so a force-quit leaves no engine
behind without any cooperation from the dying shell. A normal quit closes stdin
first, waits a grace period, and then kills the engine's process group, which
reaches the Python process `uv run` started.

## Risks And Rollback

- **Toolchain weight.** Contributors to the shell need Rust, and on Linux the
  WebKitGTK headers. The Python workflow is untouched: `desktop/` is outside the
  package, the wheel and the sdist, and the Python gates never read it.
- **CI time.** A Tauri build compiles a few hundred crates. The job is cached on
  `Cargo.lock` and runs in parallel with the Python suite, which remains the
  longest job.
- **A flaky process test.** The supervisor tests spawn real processes. Each
  waits on a bounded deadline, never a fixed sleep, and none needs a network
  or a model.
- **Rollback:** delete `desktop/` and the `desktop` CI job. Nothing else depends
  on them; the engine is unchanged.
