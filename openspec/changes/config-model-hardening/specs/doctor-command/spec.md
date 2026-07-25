# Delta for Doctor Command

## ADDED Requirements

### Requirement: Doctor Never Raises On A Malformed Model Config

`openkos doctor` MUST NOT raise an uncaught exception when the configured
`model` value is malformed at the type level — specifically when
`openkos.yaml` contains a `model:` value that PyYAML resolves to a non-`str`
type (for example the YAML 1.1 boolean literal `yes`, which resolves to
Python `True`). This contract is on the user-observable outcome, not on
which internal mechanism catches the malformed value: whether the guard
lives in the config-valid check (via `read_config` raising `ValueError`,
already wrapped in `try/except (OSError, ValueError)`) or in an independent
guard around the model-installed checks, the end state MUST be identical —
doctor reports a `[FAIL]` line with actionable remediation pointing at
fixing `openkos.yaml`, reuses the existing accumulated-never-raised
`CheckResult` convention and the standard `[PASS]/[FAIL]/[SKIP] <label>` +
optional indented `-> <remediation>` output shape, and every other
applicable check still runs and prints its own result. No new output shape
is introduced by this requirement.

#### Scenario: Non-str model value fails cleanly instead of crashing

- GIVEN an initialized workspace whose `openkos.yaml` contains `model: yes`
  (parsed by PyYAML as the Python `bool` `True`)
- WHEN `openkos doctor` runs
- THEN it does not raise an uncaught exception and prints no traceback

#### Scenario: Malformed model reports FAIL with actionable remediation

- GIVEN an initialized workspace whose `openkos.yaml` contains `model: yes`
- WHEN `openkos doctor` runs
- THEN at least one check prints `[FAIL]` followed by an indented
  `-> <remediation>` line that points the user at fixing `openkos.yaml`'s
  `model:` value

#### Scenario: Other applicable checks still run despite the malformed model

- GIVEN an initialized workspace whose `openkos.yaml` contains `model: yes`
  and Ollama is reachable
- WHEN `openkos doctor` runs
- THEN every other applicable check (Ollama-reachable, embedding-model
  installed, bundle readable, vector-extension loadable) still prints its
  own `[PASS]`/`[FAIL]`/`[SKIP]` result

#### Scenario: Output shape is unchanged

- GIVEN an initialized workspace whose `openkos.yaml` contains `model: yes`
- WHEN `openkos doctor` runs
- THEN every printed line still matches the existing
  `[PASS]`/`[FAIL]`/`[SKIP] <label>` format, with remediation (when present)
  as an indented `-> <fix command>` line — no new line shape is introduced
