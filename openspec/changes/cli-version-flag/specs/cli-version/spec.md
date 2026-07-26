# CLI Version Specification

## Purpose

`openkos --version` reports the installed distribution's version in one
read-only, workspace-independent command, so bug reports, CI, and `doctor`
carry a verifiable build identity.

## Requirements

### Requirement: Version Flag Prints Exact Output And Exits Zero

`openkos --version` MUST print exactly `openkos {version}` on a single line,
with nothing else written to stdout or stderr, and MUST exit 0.

#### Scenario: Bare version flag

- GIVEN the `openkos` distribution is installed
- WHEN a user runs `openkos --version`
- THEN the process prints exactly `openkos {version}` on one line
- AND exits with status 0

### Requirement: Version Read From Installed Distribution Metadata

The version string MUST be read via `importlib.metadata` from the installed
distribution's metadata, not from any hardcoded constant. `pyproject.toml`
(compiled into the dist-info at build/install time) MUST remain the single
source of truth; no second version constant (e.g. `__version__`) MUST exist
in `src/`.

Staleness is an explicit non-goal: if `pyproject.toml` is bumped without
re-running the install/sync step, the installed dist-info — and therefore
`--version`'s output — MAY report an old-but-plausible version. This
requirement governs only where the value comes from, not detecting drift
between `pyproject.toml` and installed metadata.

#### Scenario: Version matches installed dist-info

- GIVEN the installed `openkos` dist-info reports version `X.Y.Z`
- WHEN a user runs `openkos --version`
- THEN the printed version is exactly `X.Y.Z`, sourced from
  `importlib.metadata`, not a literal in the source code

### Requirement: Eager Evaluation Short-Circuits Before Any Subcommand Or Workspace Resolution

`--version` MUST be evaluated eagerly, before any subcommand executes and
before any workspace resolution runs, so it succeeds outside a workspace and
regardless of workspace read/write state.

#### Scenario: Works outside any workspace

- GIVEN the current directory is not an initialized `openkos` workspace
- WHEN a user runs `openkos --version`
- THEN it prints `openkos {version}` and exits 0, without attempting
  workspace resolution

#### Scenario: Short-circuits a combined subcommand invocation

- GIVEN any valid or invalid subcommand name
- WHEN a user runs `openkos --version doctor` (or `--version` with any other
  subcommand)
- THEN only the version is printed, the subcommand never executes, and the
  process exits 0

### Requirement: Unknown Version Fallback On Missing Package Metadata

WHEN `importlib.metadata` raises `PackageNotFoundError` while resolving the
`openkos` distribution, the command MUST print exactly `openkos unknown` and
MUST exit 0. It MUST NOT raise an uncaught exception or print a traceback.

#### Scenario: Package metadata unavailable

- GIVEN `importlib.metadata.version("openkos")` raises `PackageNotFoundError`
- WHEN a user runs `openkos --version`
- THEN the process prints exactly `openkos unknown`
- AND exits with status 0

### Requirement: Version Resolution Is Read-Only

Resolving and printing the version MUST NOT write to the filesystem, MUST
NOT make any network call, and MUST NOT contact a local Ollama server.

#### Scenario: No side effects from version resolution

- GIVEN an environment with no writable workspace and no reachable Ollama
  server
- WHEN a user runs `openkos --version`
- THEN no file is created or modified, no network request is made, and the
  command still prints `openkos {version}` and exits 0

### Requirement: Version Flag Is Discoverable In Help Text

`openkos --help` MUST list `--version` among the top-level options.

#### Scenario: Help text lists the flag

- WHEN a user runs `openkos --help`
- THEN the output includes a `--version` entry describing it as printing the
  installed version
