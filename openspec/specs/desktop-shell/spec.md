# Desktop Shell Specification

## Purpose

The desktop app's native shell (ADR-0055): a Tauri 2 application that starts
the engine as a sidecar running `openkos serve`, supervises it, and stops it, so
a person never starts a server by hand. The shell holds no product behaviour.
It conforms to the engine's launch contract (`local-http-api`: a per-launch token
on stdin, one JSON ready line on stdout, EOF on stdin as the engine's lifeline),
restarts a crashed engine within a bounded budget, leaves no engine behind when
it quits or is killed, and gives its web view the engine's base URL and token so
the page calls the local API itself.

Which engine runs is decided by the engine locator alone: an executable named
by an environment variable; inside an app bundle, the frozen engine the bundle
ships (ADR-0056, the `desktop-packaging` domain); otherwise the development
engine of the source checkout.

## Requirements

### Requirement: The Engine Is Located Before It Is Started

The shell MUST decide which engine to run in one place, the engine locator.
The workspace MUST be the directory named by `OPENKOS_WORKSPACE`, resolved
against the shell's working directory when relative, or the working directory
when the variable is unset. When `OPENKOS_ENGINE` is set, the engine MUST be that
executable run as `serve --workspace <workspace>`. Otherwise, when the shell's
own executable is inside a macOS app bundle (its directory is `MacOS` inside
`Contents`), the engine MUST be the frozen engine shipped in that bundle,
`Contents/Resources/engine/openkos-engine`, run as `serve --workspace
<workspace>`; when that file is missing, the shell MUST NOT spawn anything, MUST
NOT fall back to another engine, and MUST report the failure as final, naming
the path. Otherwise the engine MUST be the development engine of the source
checkout the shell was built from, run as `uv run --project <checkout> openkos
serve --workspace <workspace>`. The engine MUST run with the workspace as its
working directory. When the workspace is not a directory, the shell MUST NOT
spawn anything and MUST report the failure as final.

#### Scenario: The development engine is the default

- GIVEN neither `OPENKOS_ENGINE` nor `OPENKOS_WORKSPACE` is set
- AND the shell's executable is not inside an app bundle
- WHEN the shell locates the engine
- THEN the command is `uv run --project <checkout> openkos serve --workspace <cwd>`

#### Scenario: A bundled shell runs its frozen engine

- GIVEN the shell's executable is `<App>.app/Contents/MacOS/<shell>`
- AND `<App>.app/Contents/Resources/engine/openkos-engine` is a file
- AND `OPENKOS_ENGINE` is not set
- WHEN the shell locates the engine
- THEN the command is that frozen engine with `serve --workspace <workspace>`

#### Scenario: A bundle without its engine is final

- GIVEN the shell's executable is inside an app bundle
- AND the bundle has no `Contents/Resources/engine/openkos-engine`
- AND `OPENKOS_ENGINE` is not set
- WHEN the shell starts
- THEN no process is spawned and `uv` is not run
- AND the state is `failed` with reason `engine_not_located`, naming the missing path

#### Scenario: An explicit engine overrides the default

- GIVEN `OPENKOS_ENGINE` names an executable
- WHEN the shell locates the engine, inside a bundle or not
- THEN the command is that executable with `serve --workspace <workspace>`

#### Scenario: A missing workspace is final

- GIVEN `OPENKOS_WORKSPACE` names a path that is not a directory
- WHEN the shell starts
- THEN no process is spawned
- AND the state is `failed` with reason `engine_not_located`, naming the variable

### Requirement: The Token Goes Over Stdin And The Port Comes From The Ready Line

The shell MUST generate a fresh token for every engine launch, restarts
included: 32 bytes from the operating system's random generator, written as 64
hexadecimal characters. It MUST write the token followed by a newline as the
first line of the engine's stdin and MUST NOT place it in the engine's
arguments, environment, any file or any URL. It MUST then read the engine's
first stdout line and accept it only when it is a JSON object whose `event` is
`ready`, whose `host` is exactly `127.0.0.1`, whose `port` is between 1 and
65535, whose `base_path` starts with `/`, and whose `api_version` is the major
version the shell speaks (1); unknown keys MUST be ignored. The engine is ready
when that line is accepted, and its base URL is
`http://127.0.0.1:<port><base_path>`. Any other first line, an exit before the
line, or no line within the ready timeout MUST fail the start and MUST leave no
engine process running.

#### Scenario: The handshake succeeds

- GIVEN an engine that reads a token line and announces ready on port 4242
- WHEN the shell starts it
- THEN the engine received the token on stdin
- AND the token does not appear in the engine's arguments
- AND the base URL is `http://127.0.0.1:4242/v1`

#### Scenario: A non-loopback ready line is refused

- GIVEN an engine whose ready line names host `0.0.0.0`
- WHEN the shell starts it
- THEN the start fails with reason `bad_ready_line`
- AND the engine process is gone

#### Scenario: An engine that refuses the workspace is explained

- GIVEN an engine that writes "refusing to serve" to stderr and exits 1 before
  its ready line
- WHEN the shell starts it
- THEN the start fails with reason `exited_before_ready`
- AND the failure detail carries the engine's stderr

#### Scenario: A silent engine times out

- GIVEN an engine that never writes a ready line
- WHEN the ready timeout passes
- THEN the start fails with reason `not_ready`
- AND the engine process is gone

### Requirement: A Crashed Engine Is Restarted Within A Bounded Budget

When the engine exits while the shell is running, or a start fails, the shell
MUST restart it after a backoff, at most 3 times within any 60-second window.
The backoff MUST start at 0.5 s and double up to a 4 s cap. A failure that would
exceed the budget MUST be final: the state MUST become `failed` with the reason
and the engine's stderr tail, and the shell MUST NOT restart the engine on its
own. A restarted engine MUST receive a new token. A locator failure MUST be final
without any restart.

#### Scenario: A crash is restarted with a fresh token

- GIVEN a ready engine
- WHEN its process is killed
- THEN the shell starts a new engine process
- AND the new engine's token differs from the old one

#### Scenario: Repeated failures stop being restarted

- GIVEN an engine that always exits before its ready line
- WHEN the shell has started it once and restarted it as many times as the
  budget allows
- THEN the state is `failed` with reason `exited_before_ready`
- AND no further start is made

### Requirement: Quitting Stops The Engine And A Killed Shell Leaves None

When the shell exits normally it MUST stop the engine: close the engine's stdin,
wait up to a grace period of 8 seconds for the engine to exit, then kill the
engine's whole process group and reap it. The engine MUST run in a process group
of its own so that the kill reaches every process it started. The shell MUST
keep the engine's stdin open for the engine's whole life and MUST NOT let any
other process inherit its write end, so that when the shell is killed by any
signal the engine reads EOF and exits.

#### Scenario: A graceful quit

- GIVEN a ready engine that exits on EOF
- WHEN the shell shuts down
- THEN the engine exits on its own before the grace period ends
- AND the state is `stopped`

#### Scenario: An engine that ignores EOF is killed with its children

- GIVEN a ready engine that ignores EOF and SIGTERM and has started a child
- WHEN the shell shuts down and the grace period ends
- THEN the engine and its child are both gone

#### Scenario: A force-quit leaves no engine

- GIVEN a shell supervising a ready engine
- WHEN the shell is killed with SIGKILL
- THEN the engine exits

### Requirement: The Web View Reads The Engine State And Calls The Local API

The shell MUST expose the engine's state to the web view through one command,
`engine_state`, as a JSON object tagged by `state`: `starting` (with `attempt`),
`ready` (with `attempt`, `base_url`, `token` and `pid`), `restarting` (with
`attempt`, `delay_ms`, `reason` and `detail`), `failed` (with `reason` and
`detail`) or `stopped`. The page MUST show the engine's state in English and,
once per ready engine, MUST read the workspace status through the desktop
client created from that ready state (`GET <base_url>/status` with
`Authorization: Bearer <token>`) and show the workspace's folder, source count,
concept count and number of open decisions. When the read fails the page MUST
say so in English without showing the token. The page MUST write engine and
workspace values as text, never as markup. The page MUST be built from
`desktop/ui/` into `desktop/dist/`, the shell's `frontendDist`. The web view's
content security policy MUST restrict connections to the shell's IPC and
`http://127.0.0.1` ports.

#### Scenario: The status page reads the workspace through the engine

- GIVEN the shell started the real engine of the checkout on a fresh workspace
- WHEN a request carries the web view's origin `tauri://localhost` and the token
- THEN the CORS preflight for `GET /v1/status` is answered 204
- AND `GET /v1/status` is answered 200 with the workspace's counts
- AND the same request without the token is answered 401

#### Scenario: The page words a failed read without the token

- GIVEN a ready engine whose status read fails with an error envelope, a body that is not the envelope, or no connection
- WHEN the page shows the failure
- THEN it shows an English sentence saying the status could not be read
- AND the sentence does not contain the token
