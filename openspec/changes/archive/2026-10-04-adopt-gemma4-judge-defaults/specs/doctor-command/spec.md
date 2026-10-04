# Doctor Command Specification (delta)

## MODIFIED Requirements

### Requirement: Task-Models-Installed Check

`doctor` MUST report whether every per-task model the workspace resolves
(`models:` entries and packaged `DEFAULT_TASK_MODELS` defaults, the latter
resolving only on the `ollama` backend) is installed, using the same tag-normalized `model_tag_matches()`
comparison and `[PASS]`/`[FAIL]`/`[SKIP]` + remediation pattern as the chat
model-installed check.

This MUST be exactly ONE check regardless of how many tasks resolve a
model, so the total check count stays fixed. It MUST examine only models
DIFFERING from the global `model:` — a task resolving the global tag is
already covered by the model-installed check, and reporting it twice would
double-count one root cause. WHEN no task resolves a differing model, this
check MUST print `[PASS]`.

This check MUST be informational: its failure alone MUST NOT affect the
exit code, because a missing per-task model fails only the stage that named
it while every other verb still works. WHEN Ollama is unreachable, it MUST
print `[SKIP]` with a blocked-by-unreachable detail rather than `[FAIL]`,
for the same one-root-cause reason as the embedding-model check.

#### Scenario: A missing per-task model is reported without failing the run

- GIVEN Ollama is reachable and the packaged judge model `gemma4:26b-a4b`
  (the default for `adjudication` and `contradiction`) is not installed
- WHEN `openkos doctor` runs
- THEN the task-models check prints `[FAIL]`, names the task and its
  model, and offers a pull command for that exact tag, and the process
  still exits 0 if every critical check otherwise passes

#### Scenario: Declining a packaged default leaves nothing to check

- GIVEN `models.adjudication` and `models.contradiction` are explicit nulls
  and no other task resolves a differing model
- WHEN `openkos doctor` runs
- THEN the task-models check prints `[PASS]`

#### Scenario: Ollama unreachable skips the task-models check

- GIVEN Ollama is unreachable and a task resolves a differing model
- WHEN `openkos doctor` runs
- THEN the task-models check prints `[SKIP]` with a
  blocked-by-unreachable detail, not `[FAIL]`
