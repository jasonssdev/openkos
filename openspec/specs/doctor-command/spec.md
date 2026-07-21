# Doctor Command Specification

## Purpose

`openkos doctor` is a read-only environment health scan: a fixed set of
checks against the local workspace and the local Ollama server, printed as
`[PASS]`/`[FAIL]` lines with actionable remediation, usable even before
`openkos init`.

## Requirements

### Requirement: Doctor Runs And Prints All Applicable Checks

`doctor` MUST execute all checks applicable to the current context —
workspace initialized, `openkos.yaml` valid, Ollama reachable, configured
model installed, bundle readable — and print exactly one `[PASS]`/`[FAIL]`
line per applicable check. It MUST NOT stop or skip remaining checks after
any single check fails.

#### Scenario: Healthy workspace prints all applicable checks

- GIVEN an initialized workspace, valid config, reachable Ollama, the
  configured model installed, and a readable bundle
- WHEN `openkos doctor` runs
- THEN it prints one `[PASS]` line per check, covering all 5 checks

#### Scenario: A failing check does not stop later checks from running

- GIVEN Ollama is unreachable AND `openkos.yaml` is malformed
- WHEN `openkos doctor` runs
- THEN both the config-valid and Ollama-reachable checks print `[FAIL]`,
  and every other applicable check still prints its own result

### Requirement: Failed Checks Print Actionable Remediation

Each `[FAIL]` line MUST be immediately followed by an indented
`-> <fix command>` line naming the user's own next command: Ollama
unreachable points to starting the server or installing it, depending on
whether the `ollama` binary is resolvable on the current process's PATH; a
missing model points to pulling that model tag; an uninitialized workspace
points to initializing it. `doctor` MUST NOT run these commands itself.

For the Ollama-unreachable case specifically, the system MUST use
`shutil.which("ollama")` as a non-authoritative signal to select the
remediation wording: WHEN `shutil.which("ollama")` returns `None`, the
remediation MUST state that no `ollama` binary was found on PATH and point
to https://ollama.com for installation, and MUST NOT claim "Ollama is not
installed"; WHEN a binary is found but the endpoint still refuses the
connection, the remediation MUST point to `ollama serve`, unchanged from
prior behavior; WHEN the signal cannot be read confidently, the remediation
MUST cover both remedies rather than asserting either state as certain.
(Previously: any `OllamaUnavailable` failure produced the same generic
`ollama serve` remediation regardless of whether the binary was present on
PATH.)

#### Scenario: Binary found, endpoint refuses — start-server remediation

- GIVEN `shutil.which("ollama")` resolves to a path, but the endpoint
  refuses the connection
- WHEN `openkos doctor` runs
- THEN the Ollama-reachable check prints `[FAIL]` followed by a
  `-> ollama serve` remediation line

#### Scenario: No binary on PATH — install remediation, no over-claim

- GIVEN `shutil.which("ollama")` returns `None`
- WHEN `openkos doctor` runs
- THEN the Ollama-reachable check prints `[FAIL]` followed by a remediation
  line stating no `ollama` binary was found on PATH and pointing to
  https://ollama.com
- AND the remediation text never states "Ollama is not installed"

#### Scenario: Uncertain signal covers both remedies

- GIVEN the `shutil.which("ollama")` signal cannot be read confidently
- WHEN `openkos doctor` runs
- THEN the Ollama-reachable check's remediation covers both installing and
  starting Ollama, rather than asserting either state as certain

#### Scenario: Missing model shows a pull remediation naming the tag

- GIVEN Ollama is reachable but the configured model tag is not installed
- WHEN `openkos doctor` runs
- THEN the model-installed check prints `[FAIL]` followed by an indented
  fix line naming a pull command for that exact configured tag

#### Scenario: Outside a workspace shows an init remediation

- GIVEN the current directory is not an initialized workspace
- WHEN `openkos doctor` runs
- THEN the workspace-initialized check prints `[FAIL]` followed by an
  indented fix line naming the init command

### Requirement: Exit Code Reflects Critical Failures Only

`doctor` MUST exit with code 1 if any CRITICAL check (config valid, Ollama
reachable, model installed) failed, and MUST exit 0 otherwise. Informational
checks (workspace initialized, bundle readable) failing alone MUST NOT
cause a non-zero exit.

#### Scenario: Informational-only failure still exits zero

- GIVEN the workspace-initialized check fails but every critical check
  passes
- WHEN `openkos doctor` runs
- THEN the process exits with code 0

#### Scenario: Any critical failure causes exit one

- GIVEN one critical check fails while all other checks pass
- WHEN `openkos doctor` runs
- THEN the process exits with code 1

### Requirement: Doctor Works Outside An Initialized Workspace

Outside an initialized workspace, `doctor` MUST still run: the
workspace-initialized check reports an informational `[FAIL]` with init
remediation, the config-valid and bundle-readable checks are skipped as
not applicable, and the Ollama-reachable and model-installed checks MUST
still run — checked against the default model — and MUST still determine
the exit code.

#### Scenario: Unhealthy pre-init environment exits one

- GIVEN no initialized workspace and Ollama unreachable
- WHEN `openkos doctor` runs
- THEN it prints results for workspace, Ollama-reachable, and
  model-installed, and exits with code 1

#### Scenario: Healthy pre-init environment exits zero

- GIVEN no initialized workspace, Ollama reachable, and the default model
  installed
- WHEN `openkos doctor` runs
- THEN every applicable check passes and the process exits with code 0

### Requirement: Model-Installed Check Uses Tag-Normalized Matching

The configured (or default, outside a workspace) model MUST count as
installed if it matches an installed tag exactly, or matches that tag's
`<name>:latest` form.

#### Scenario: Bare configured tag matches a :latest installed entry

- GIVEN the configured model tag has no explicit version suffix and an
  installed entry reports it as `<name>:latest`
- WHEN `openkos doctor` runs
- THEN the model-installed check passes

#### Scenario: Non-matching tag fails with pull remediation

- GIVEN no installed entry matches the configured tag under either
  normalization
- WHEN `openkos doctor` runs
- THEN the model-installed check prints `[FAIL]` with a pull remediation

### Requirement: Doctor Is Read-Only

`doctor` MUST NOT create, modify, or delete any file, and MUST NOT execute
any remediation command on the user's behalf; it only diagnoses and
advises.

#### Scenario: Doctor run leaves the workspace unchanged

- GIVEN any combination of passing and failing checks
- WHEN `openkos doctor` runs
- THEN no file in the workspace is created, modified, or deleted, and no
  fix command is executed by `doctor` itself
