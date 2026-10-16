# Delta for Desktop Shell

## MODIFIED Requirements

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

(Previously: the only defaults were `OPENKOS_ENGINE` and the development engine;
a bundled shell had no engine of its own.)

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
