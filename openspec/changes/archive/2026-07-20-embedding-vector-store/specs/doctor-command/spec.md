# Delta for Doctor Command

## MODIFIED Requirements

### Requirement: Doctor Runs And Prints All Applicable Checks

`doctor` MUST execute all checks applicable to the current context —
workspace initialized, `openkos.yaml` valid, Ollama reachable, configured
chat model installed, configured embedding model installed, bundle
readable, vector extension loadable — and print exactly one
`[PASS]`/`[FAIL]`/`[SKIP]` line per applicable check. It MUST NOT stop or
skip remaining checks after any single check fails.
(Previously: 6 checks, no vector-extension check.)

#### Scenario: Healthy workspace prints all applicable checks

- GIVEN an initialized workspace, valid config, reachable Ollama, both
  configured models installed, a readable bundle, and a loadable vector
  extension
- WHEN `openkos doctor` runs
- THEN it prints one `[PASS]` line per check, covering all 7 checks

#### Scenario: A failing check does not stop later checks from running

- GIVEN Ollama is unreachable AND `openkos.yaml` is malformed
- WHEN `openkos doctor` runs
- THEN both the config-valid and Ollama-reachable checks print `[FAIL]`,
  and every other applicable check still prints its own result

### Requirement: Exit Code Reflects Critical Failures Only

`doctor` MUST exit with code 1 if any CRITICAL check (config valid, Ollama
reachable, chat model installed) failed, and MUST exit 0 otherwise.
Informational checks (workspace initialized, bundle readable, embedding
model installed, vector extension loadable) failing alone MUST NOT cause a
non-zero exit.
(Previously: vector-extension-loadable did not exist as a check.)

#### Scenario: Informational-only failure still exits zero

- GIVEN the vector-extension check fails while every critical check passes
- WHEN `openkos doctor` runs
- THEN the process exits with code 0

#### Scenario: Any critical failure causes exit one

- GIVEN one critical check fails while all other checks pass
- WHEN `openkos doctor` runs
- THEN the process exits with code 1

### Requirement: Doctor Works Outside An Initialized Workspace

Outside an initialized workspace, `doctor` MUST still run: the
workspace-initialized check reports an informational `[FAIL]` with init
remediation, the config-valid and bundle-readable checks are skipped as not
applicable, and the Ollama-reachable and chat-model-installed checks MUST
still run and determine the exit code. The embedding-model-installed and
vector-extension-loadable checks MUST also still run pre-init, both as
informational checks — the latter depends only on the local SQLite/Python
environment, not on workspace state.
(Previously: no mention of the vector-extension check outside a workspace.)

#### Scenario: Unhealthy pre-init environment exits one

- GIVEN no initialized workspace and Ollama unreachable
- WHEN `openkos doctor` runs
- THEN it prints results for workspace, Ollama-reachable, chat
  model-installed, embedding model-installed, and vector-extension-loadable,
  and exits with code 1

#### Scenario: Healthy pre-init environment exits zero

- GIVEN no initialized workspace, Ollama reachable, both default models
  installed, and a loadable vector extension
- WHEN `openkos doctor` runs
- THEN every applicable check passes and the process exits with code 0

## ADDED Requirements

### Requirement: Vector-Extension-Loadable Check

`doctor` MUST report whether the `sqlite-vec` extension is loadable on the
current Python/SQLite environment, reusing the same accumulate-never-raise
`CheckResult` pattern as the other checks. This check MUST be informational
(its failure alone MUST NOT affect the exit code), MUST NOT depend on
workspace state or Ollama reachability, and MUST print exactly one
`[PASS]`/`[FAIL]` line without duplicating a root cause already reported by
another check.

#### Scenario: Extension loadable passes

- GIVEN the current environment can `enable_load_extension` and load
  `sqlite-vec`
- WHEN `openkos doctor` runs
- THEN the vector-extension check prints `[PASS]`

#### Scenario: Extension not loadable shows an extension-capable remediation

- GIVEN the current environment cannot load extensions (e.g. system or
  Homebrew Python without `enable_load_extension`)
- WHEN `openkos doctor` runs
- THEN the vector-extension check prints `[FAIL]` followed by an indented
  fix line naming an extension-capable Python (e.g. a uv-managed
  interpreter), and the process still exits 0 if every critical check
  otherwise passes

#### Scenario: Check runs independently of Ollama's state

- GIVEN Ollama is unreachable
- WHEN `openkos doctor` runs
- THEN the vector-extension check still runs and reports its own
  `[PASS]`/`[FAIL]` result, rather than being skipped
