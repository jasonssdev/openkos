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
engine of the source checkout. Which workspace it serves is the one named by an
environment variable, or else the one the user chose in the app, which the shell
remembers in its own configuration directory; a workspace is created by running
the engine's own `init` (#1487). The shell keeps the remote backend's API key in
the operating system's keychain and hands it to the engine as its environment
(ADR-0070).

## Requirements

### Requirement: The Engine Is Located Before It Is Started

The shell MUST decide which engine to run in one place, the engine locator.
The workspace MUST be the directory named by `OPENKOS_WORKSPACE`, resolved
against the shell's working directory when relative; when the variable is unset,
it MUST be the workspace the shell remembers the user choosing, as long as that
folder still holds `openkos.yaml`. When neither gives a workspace, the shell
MUST NOT spawn anything and the state MUST be `no_workspace`; that is not a
failure, and a requested restart locates again. When `OPENKOS_ENGINE` is set, the engine MUST be that
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

- GIVEN `OPENKOS_ENGINE` is not set and `OPENKOS_WORKSPACE` names a workspace
- AND the shell's executable is not inside an app bundle
- WHEN the shell locates the engine
- THEN the command is `uv run --project <checkout> openkos serve --workspace <workspace>`

#### Scenario: No workspace is chosen yet

- GIVEN `OPENKOS_WORKSPACE` is not set
- AND no workspace is remembered, or the remembered folder has no `openkos.yaml`
- WHEN the shell starts
- THEN no process is spawned
- AND the state is `no_workspace`

#### Scenario: The remembered workspace is served

- GIVEN `OPENKOS_WORKSPACE` is not set
- AND the shell remembers a folder that holds `openkos.yaml`
- WHEN the shell locates the engine
- THEN the engine serves that folder, with it as its working directory

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

A restart the user asks for (a changed API key, a workspace opened or created)
MUST NOT spend the crash budget: a running engine MUST be stopped and started
again, a pending backoff MUST be cut short, and after a final state (`failed` or
`no_workspace`) supervision MUST start again. After the shell's shutdown it MUST
do nothing.

#### Scenario: A crash is restarted with a fresh token

- GIVEN a ready engine
- WHEN its process is killed
- THEN the shell starts a new engine process
- AND the new engine's token differs from the old one

#### Scenario: A requested restart starts the chosen workspace

- GIVEN the state is `no_workspace`
- WHEN a workspace is chosen and a restart is requested
- THEN the shell locates again and starts the engine on it
- AND the crash budget is untouched

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
`detail`), `no_workspace` or `stopped`. The page MUST show the engine's state in English and,
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

### Requirement: The User Chooses The Workspace In The App

The shell MUST remember the chosen workspace as its absolute, symlink-free path
in one file in its own application configuration directory, never inside the
workspace, written so that a crash never leaves half a choice. A missing,
unreadable or malformed file, or a relative path in it, MUST count as no choice.
The shell MUST expose four commands to the web view:

- `workspace_choice_status` MUST answer `source` (`environment`, `remembered`
  or `none`), `path` (or null) and `valid`, whether that folder holds
  `openkos.yaml`.
- `open_workspace(path)` MUST refuse unless `path` is absolute and is a folder
  holding `openkos.yaml`; otherwise it MUST remember it and request a restart.
- `create_workspace(path)` MUST create the folder when it is missing and its
  parent exists, and MUST refuse a folder that is already a workspace or holds
  anything but Finder's `.DS_Store`. It MUST then run the located engine's
  `init` with that folder as its working directory and an empty, non-terminal
  stdin, so `init` writes its recommended models without prompting, killing it
  after a bounded time; an `init` killed at that bound MUST refuse with
  `init_timed_out`, any other failure with `init_failed`. Only when `init`
  succeeds and `openkos.yaml` exists MUST it remember the folder and request a
  restart.
- `pick_workspace_folder` MUST show the system's folder picker from the shell
  and answer the chosen path, or null when cancelled. The web view MUST NOT be
  granted the dialog plugin's own permissions.

When `OPENKOS_WORKSPACE` is set, `open_workspace` and `create_workspace` MUST
refuse with `set_by_environment` before writing anything. A refusal MUST answer
a `reason` (`set_by_environment`, `invalid_path`, `not_a_folder`,
`not_a_workspace`, `already_a_workspace`, `folder_not_empty`,
`engine_not_located`, `init_failed`, `init_timed_out` or `store_failed`) and an
English `detail`;
a refused command MUST NOT change the remembered choice or restart the engine.

#### Scenario: A first-run user creates a workspace

- GIVEN no workspace is chosen and the state is `no_workspace`
- WHEN `create_workspace` is called with a new folder
- THEN the engine's `init` runs there without prompting and writes `qwen3:8b` and `bge-m3`
- AND the folder is remembered and the engine becomes ready serving it

#### Scenario: A folder with the user's files in it is refused

- GIVEN a folder holding a file of the user's
- WHEN `create_workspace` is called with it
- THEN the answer is `folder_not_empty`
- AND `init` does not run and the file is untouched

#### Scenario: Opening a folder that is not a workspace is refused

- GIVEN a folder with no `openkos.yaml`
- WHEN `open_workspace` is called with it
- THEN the answer is `not_a_workspace`
- AND nothing is remembered and the engine is not restarted

#### Scenario: The variable wins

- GIVEN `OPENKOS_WORKSPACE` is set
- WHEN `workspace_choice_status` is called
- THEN `source` is `environment`
- AND `open_workspace` and `create_workspace` answer `set_by_environment`

### Requirement: The Shell Opens The Model Runtime's Download Page

The shell MUST expose one command, `open_runtime_download`, taking no argument,
that opens Ollama's official download page (ADR-0057), the address the engine's
setup readiness reports as `download_url`, in the system's default browser. The
address MUST be fixed in the shell, so the web view cannot open any other. The
shell MUST run the operating system's own opener with the address as a single
argument and no shell in between; it MUST NOT add the opener plugin or grant the
web view a permission to open URLs. When the opener fails, the command MUST
refuse with an English detail.

#### Scenario: The page asks for the download page

- GIVEN setup reports that Ollama does not answer
- WHEN the page calls `open_runtime_download`
- THEN the default browser opens `https://ollama.com/download`
- AND the web view stays on the app

### Requirement: The Remote Backend's Key Is Kept By The Shell

The shell MUST keep the remote backend's API key in the operating system's
keychain and MUST hand it to the engine only as the `OPENKOS_OPENAI_API_KEY`
environment variable of the engine process (ADR-0070). It MUST expose
`set_api_key`, `clear_api_key` and `api_key_status`; none of them MUST return
the key, and setting or clearing it MUST request a restart that does not spend
the crash budget. Where the shell has no keychain store the three commands MUST
answer `not_supported`.

#### Scenario: A changed key restarts the engine with it

- GIVEN a ready engine
- WHEN `set_api_key` is called with a valid key
- THEN the engine is restarted with `OPENKOS_OPENAI_API_KEY` set to it
- AND the crash budget is untouched
