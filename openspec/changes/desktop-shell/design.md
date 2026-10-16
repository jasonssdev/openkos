# Design: The Desktop Shell Supervises The Engine Sidecar

Inputs: `proposal.md`, `specs/desktop-shell/spec.md`, ADR-0055, ADR-0058,
ADR-0061, `src/openkos/localapi/launch.py` (the engine's side of the handshake,
which this design conforms to and does not change).

## D1 The shell lives in `desktop/`, a sibling of `src/`

```
desktop/
├── src-tauri/          # Rust crate `openkos-desktop` (Tauri's conventional name)
│   ├── src/engine.rs       one engine process: spawn, token, ready line, stop
│   ├── src/supervisor.rs   start, watch, bounded restarts, stop; EngineState
│   ├── src/locator.rs      which engine to run
│   ├── src/lib.rs main.rs  the Tauri app
│   └── tests/live_engine.rs
└── ui/                 # static HTML, CSS and JavaScript; no bundler
```

**Why here.** The shell is a delivery path, like the CLI, but it is not Python:
putting it under `src/openkos/` would put Rust inside the package that the wheel,
Ruff, MyPy and the import guards all walk. A top-level directory named for what
it is (`desktop/`, the "Desktop app" of `docs/architecture.md`'s delivery list)
keeps the engine's tree and gates untouched, and is what a contributor looking
for the app would open. `src-tauri/` is Tauri's own layout, so its CLI and
documentation apply without translation. `app/` was rejected as ambiguous in a
codebase where `app` already names the Typer and FastAPI objects.

Verified: `uv build` produces an sdist with no `desktop/` member (uv_build ships
the module, the metadata and the license only), and `target/` is already
ignored by the root `.gitignore`.

## D2 The handshake is the engine's, unchanged

`launch.py` fixes the order: open the workspace, read one token line from stdin,
bind `127.0.0.1` on an ephemeral port, and once uvicorn listens, write exactly
one JSON line to stdout:

```json
{"event": "ready", "host": "127.0.0.1", "port": 51234, "api_version": 1, "base_path": "/v1"}
```

Nothing else is written to stdout. EOF on stdin, SIGTERM or SIGINT ends the
server; exit 1 is a runtime failure (workspace refused, bind failed), exit 2 a
usage failure (no or bad token, or the `[api]` extra missing).

The shell:

1. Generates the token: 32 bytes from the operating system's generator
   (`getrandom`), as 64 lowercase hex characters, a subset of the engine's
   accepted alphabet (`[A-Za-z0-9\-._~+/]{32,1024}`). Fresh on every launch,
   restarts included. It is never put in argv, the environment, a file or a URL,
   and its `Debug` form is redacted.
2. Writes `<token>\n` to the engine's stdin and keeps stdin open.
3. Reads the first stdout line and validates it: `event` is `"ready"`, `host` is
   exactly `127.0.0.1`, `port` is 1..65535, `base_path` starts with `/`, and
   `api_version` equals the one major version this shell speaks (1). Unknown keys
   are ignored, so the engine may add fields. Any other first line is refused and
   the engine is stopped. Later stdout lines are drained and ignored.
4. Treats the ready line as readiness. The engine writes it only once uvicorn is
   listening, so no extra health probe is needed; the page's first request is
   the round-trip check.
5. Collects stderr into a bounded tail (the last 40 lines) so a failure can be
   explained without growing memory.

A start fails as one of: `spawn_failed`, `token_handover_failed`,
`exited_before_ready` (with the exit status and stderr tail, e.g. the engine's
"refusing to serve" message), `bad_ready_line`, `not_ready` (no line within the
ready timeout, 60 s, generous because `uv run` may first sync the environment),
or `cancelled` (the shell quit during the start).

## D3 The engine runs with `std::process`, not the shell plugin

ADR-0055 names Tauri's `externalBin` sidecar mechanism for the frozen engine.
`externalBin` only decides where a binary is placed in the bundle; spawning it
through `tauri-plugin-shell` would cost the one control this design depends on:
the plugin's child handle can write to stdin and kill, but cannot close stdin,
so a graceful stop through the engine's own lifeline would be impossible and
every stop would be a kill. The shell therefore spawns with `std::process`, which
gives the stdin handle to the supervisor to keep or drop. When the freeze lands,
the locator resolves the `externalBin` wrapper's path next to the shell's
executable and the supervisor runs it the same way. No shell-plugin permission
is granted to the web view.

## D4 Orphan prevention: the stdin lifeline, then the process group

```
shell (Tauri)                       engine (uv run → python: openkos serve)
  │ spawn, own process group ─────────▶│
  │ "<token>\n" on stdin ─────────────▶│ read_token
  │◀──────────── {"event":"ready",…}   │ uvicorn listening
  │   publish Ready{base_url, token}   │
  │          … stdin stays open …      │ watches stdin for EOF
  │ quit: RunEvent::Exit               │
  │ close stdin ──────────────────────▶│ EOF → should_exit → exit 0
  │ wait ≤ 8 s; then SIGKILL group     │
  │ reap                               │
```

- **Force-quit (SIGKILL of the shell).** The kernel closes the shell's file
  descriptors, including the write end of the engine's stdin pipe; the engine
  reads EOF and exits. Rust creates the pipes close-on-exec, so no other child
  holds a copy of the write end. Under `uv run`, the Python process inherits
  that same stdin, so it sees the same EOF, and `uv` exits with it.
- **Graceful quit.** On `RunEvent::Exit` the shell calls `Supervisor::shutdown`:
  close stdin, wait up to 8 s (above the engine's own 5 s graceful bound), then
  `SIGKILL` the engine's process group (`process_group(0)` at spawn) and reap.
  The group kill also runs after a graceful exit, so nothing the engine started
  survives it. A SIGTERM to the shell takes the force-quit path.
- **Evidence.** A unit test re-executes the test binary as a "holder" that
  supervises a fake engine, SIGKILLs it, and asserts the engine exits; with a
  fake that ignores EOF the same test fails. Against the real `uv run openkos
  serve`, a SIGKILLed and a SIGTERMed shell both left no engine process.

ADR-0055 also records a parent-PID poll in the engine as part of the spike's
guard. This stage does not add it: the engine already has the stdin lifeline,
the measured force-quit leaves nothing behind without it, and adding it would
change the engine, which the proposal keeps out of scope. If the frozen
launcher of the freeze stage turns out to hold a copy of the pipe, that stage
adds the poll.

## D5 Restarts are bounded

An engine that exits while the shell is running, or that fails to start, counts
as one failure. The policy allows 3 restarts within a 60 s window, with a
backoff of 0.5 s doubling to a 4 s cap; a failure older than the window is
forgotten. The next failure inside the window is final: the state becomes
`failed`, with the reason and the stderr tail, and nothing restarts on its own.
A locator failure (the workspace is not a directory) is final at once, because
nothing would change on a retry. Each restart is a fresh launch: a new token, a
new port, a new process. Restart numbers are tunables in `RestartPolicy`, not a
contract the page relies on.

## D6 One state, read by the web view

```
starting{attempt} → ready{attempt, base_url, token, pid}
                  → restarting{attempt, delay_ms, reason, detail} → starting …
                  → failed{reason, detail}
any → stopped (on shutdown)
```

The state is serialised as JSON with a `state` tag and returned by one Tauri
command, `engine_state`. The page polls it once a second; when it sees a ready
engine it has not read yet (a new pid or base URL), it requests
`GET {base_url}/status` with `Authorization: Bearer <token>`. The engine accepts
the web view's origin and answers the CORS preflight the `Authorization` header
triggers (ADR-0058's allowlist). The page writes every value with `textContent`.
Its content security policy allows scripts, styles and images from the page
itself and connections to `ipc:` and `http://127.0.0.1:*` only.

Polling instead of events keeps the page free of event permissions and is cheap
at one IPC call a second. The token reaches the web view because the web view is
the API's client; it stays in memory there, as ADR-0058 intends.

## D7 The engine locator

```rust
locate(env, cwd, dev_checkout) -> Result<EngineCommand, LocateError>
```

- Workspace: `OPENKOS_WORKSPACE` (relative values resolved against the working
  directory), else the working directory. It must be a directory, or the locator
  fails without spawning. Validating it further is the engine's job: it refuses a
  path that is not a workspace, or has a symlinked segment, and that refusal
  reaches the page through the stderr tail.
- Program: `OPENKOS_ENGINE` if set, run as `<engine> serve --workspace <dir>`;
  otherwise `uv run --project <dev checkout> openkos serve --workspace <dir>`,
  where the dev checkout is the repository the crate was compiled in
  (`CARGO_MANIFEST_DIR/../..`).
- The engine runs with the workspace as its working directory.

The freeze stage adds a third source, the bundled engine, and makes it the
default of a release build. Nothing outside `locator.rs` changes.

## D8 CI

A `desktop` job on `ubuntu-latest`, beside the Python jobs, with no path filter
(the workflow's existing rule). It installs WebKitGTK's development packages,
uses the runner's rustup, caches the build on `Cargo.lock`, and runs
`cargo fmt --check`, `cargo clippy --locked --all-targets -- -D warnings` and
`cargo test --locked`. It then installs the engine with `uv sync --locked` and
runs the ignored live test, which starts the real engine through the supervisor
and sends the web view's request shape. There is no frontend lint or typecheck,
because there is no frontend toolchain: the page is three static files.

## D9 ADR gate

No new ADR. The shell technology and the sidecar model are ADR-0055's; the
transport, token and origin checks are ADR-0058's. The choices made here (the
directory, `std::process` over the shell plugin, the restart numbers, polling)
are reversible in one change each. D3 and D4 are recorded here and in the
archive report as how this stage realised ADR-0055.
