# Delta for Doctor Command

## MODIFIED Requirements

### Requirement: Doctor Runs And Prints All Applicable Checks

`doctor` MUST execute all checks applicable to the current context —
workspace initialized, `openkos.yaml` valid, Ollama reachable, configured
chat model installed, configured embedding model installed, bundle
readable — and print exactly one `[PASS]`/`[FAIL]`/`[SKIP]` line per
applicable check. It MUST NOT stop or skip remaining checks after any
single check fails.
(Previously: 5 checks, no embedding-model check.)

#### Scenario: Healthy workspace prints all applicable checks

- GIVEN an initialized workspace, valid config, reachable Ollama, the
  configured chat and embedding models both installed, and a readable
  bundle
- WHEN `openkos doctor` runs
- THEN it prints one `[PASS]` line per check, covering all 6 checks

#### Scenario: A failing check does not stop later checks from running

- GIVEN Ollama is unreachable AND `openkos.yaml` is malformed
- WHEN `openkos doctor` runs
- THEN both the config-valid and Ollama-reachable checks print `[FAIL]`,
  and every other applicable check still prints its own result

### Requirement: Exit Code Reflects Critical Failures Only

`doctor` MUST exit with code 1 if any CRITICAL check (config valid, Ollama
reachable, chat model installed) failed, and MUST exit 0 otherwise.
Informational checks (workspace initialized, bundle readable, embedding
model installed) failing alone MUST NOT cause a non-zero exit.
(Previously: embedding model installed did not exist as a check.)

#### Scenario: Informational-only failure still exits zero

- GIVEN the workspace-initialized check fails, or the embedding-model
  check fails, while every critical check passes
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
not applicable, and the Ollama-reachable and chat-model-installed checks
MUST still run — checked against the default chat model — and MUST still
determine the exit code. The embedding-model-installed check MUST also
still run pre-init, checked against the default `embedding_model`, as an
informational (non-exit-code-affecting) check.
(Previously: no mention of the embedding-model check outside a workspace.)

#### Scenario: Unhealthy pre-init environment exits one

- GIVEN no initialized workspace and Ollama unreachable
- WHEN `openkos doctor` runs
- THEN it prints results for workspace, Ollama-reachable, chat
  model-installed, and embedding model-installed, and exits with code 1

#### Scenario: Healthy pre-init environment exits zero

- GIVEN no initialized workspace, Ollama reachable, and the default chat
  and embedding models both installed
- WHEN `openkos doctor` runs
- THEN every applicable check passes and the process exits with code 0

## ADDED Requirements

### Requirement: Embedding-Model-Installed Check

`doctor` MUST report whether the configured (or, outside a workspace,
default) `embedding_model` is installed, using the same tag-normalized
`model_tag_matches()` comparison and `[PASS]`/`[FAIL]`/`[SKIP]` +
remediation pattern as the chat model-installed check. This check MUST be
informational (its failure alone MUST NOT affect the exit code). WHEN
Ollama is unreachable, this check MUST print `[SKIP]` with a
blocked-by-unreachable detail rather than `[FAIL]`, to avoid
double-reporting the same root cause already surfaced by the
Ollama-reachable check.

#### Scenario: Embedding model installed passes

- GIVEN Ollama is reachable and the configured `embedding_model` tag is
  installed (exact or `:latest`-normalized match)
- WHEN `openkos doctor` runs
- THEN the embedding-model check prints `[PASS]`

#### Scenario: Embedding model missing shows a pull remediation

- GIVEN Ollama is reachable but the configured `embedding_model` tag is
  not installed
- WHEN `openkos doctor` runs
- THEN the embedding-model check prints `[FAIL]` followed by an indented
  fix line naming a pull command for that exact tag, and the process still
  exits 0 if every critical check otherwise passes

#### Scenario: Ollama unreachable skips the embedding-model check

- GIVEN Ollama is unreachable
- WHEN `openkos doctor` runs
- THEN the embedding-model check prints `[SKIP]` with a
  blocked-by-unreachable detail, not `[FAIL]`, and the Ollama-reachable
  check alone reports the root cause
