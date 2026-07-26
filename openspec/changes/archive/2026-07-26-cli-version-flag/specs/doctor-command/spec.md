# Delta for Doctor Command

## ADDED Requirements

### Requirement: Doctor Prints A Leading Version Banner

`openkos doctor` MUST print a version banner line — the same
`openkos {version}` string produced by `openkos --version` — before any
check output, using the same resolution and `PackageNotFoundError` fallback
(`openkos unknown`) as `--version`. This banner is informational only: it is
NOT a `CheckResult` at all (doctor emits ten of those), MUST NOT be counted
among the applicable checks, and MUST NOT affect the exit code.

#### Scenario: Banner precedes all check lines

- GIVEN any workspace state (initialized or not)
- WHEN `openkos doctor` runs
- THEN the first printed line is `openkos {version}`, followed by the
  existing per-check `[PASS]`/`[FAIL]`/`[SKIP]` lines

#### Scenario: Check count and exit code are unaffected by the banner

- GIVEN an initialized workspace where every applicable check passes
- WHEN `openkos doctor` runs
- THEN the same number of check lines print as before this change, and the
  process still exits 0

## MODIFIED Requirements

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
optional indented `-> <remediation>` output shape for every check line, and
every other applicable check still runs and prints its own result. No new
check-line shape is introduced by this requirement; the one exception is the
single leading version banner line (see "Doctor Prints A Leading Version
Banner"), which precedes the checks and is not itself a check line.
(Previously: the requirement stated no new line shape at all was introduced,
with no carve-out for a non-check banner line.)

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

#### Scenario: Check-line shape is unchanged; only the leading banner is new

- GIVEN an initialized workspace whose `openkos.yaml` contains `model: yes`
- WHEN `openkos doctor` runs
- THEN every check line still matches the existing
  `[PASS]`/`[FAIL]`/`[SKIP] <label>` format, with remediation (when present)
  as an indented `-> <fix command>` line, and the only new line in the
  entire output is the single leading `openkos {version}` banner preceding
  all checks
