# Query Command Specification (delta)

## ADDED Requirements

### Requirement: The Sufficiency Check Runs On The `sufficiency` Task Model

WHEN `sufficiency_check` is enabled, every Ask surface (`openkos query`, the MCP
`query` tool and the Python API `ask`) MUST run the pre-synthesis check on the
model `config.resolve_task_model(cfg, "sufficiency")` resolves, and MUST run
synthesis on the global `model:`. The `sufficiency` task defaults to
`gemma4:26b-a4b` on the `ollama` backend only; an explicit `models:` entry wins
on any backend, and an explicit null follows `model:`. WHEN the resolved tag
equals the synthesis tag, or the check is disabled, no second client MUST be
built and the check MUST share the synthesis client.

The confidential local exemption MUST hold only when EVERY chat client that
receives the context is local and the workspace has not opted out, because
confidential bodies travel to the check model as well as the synthesis model.

#### Scenario: A stock workspace checks on the judge model

- GIVEN an `ollama` workspace with no `models:` entry and the check enabled
- WHEN `openkos query "<question>"` runs
- THEN the check is sent to `gemma4:26b-a4b` and synthesis to `model:`

#### Scenario: The null opt-out shares the synthesis client

- GIVEN `models: {sufficiency: null}`
- WHEN `openkos query "<question>"` runs
- THEN the check runs on the synthesis client and no second client is built

#### Scenario: A non-local check client withdraws the exemption

- GIVEN a local synthesis client and a check client that is not local
- WHEN the exemption is resolved for the call
- THEN `local_exemption` is `False`

### Requirement: A Missing Sufficiency Model Degrades The Check, Not The Query

WHEN the `sufficiency` task model is not installed, `query` MUST still answer
(exit `0`) and MUST announce on stderr that the check could not run because its
model is not available, naming the model tag, the backend's install hint
(`ollama pull <tag>` on `ollama`) and the opt-out `models: {sufficiency: null}`.
A transient check failure MUST keep the generic degraded notice. An unreachable
server MUST remain a fatal error.

#### Scenario: The notice names the model and how to fix it

- GIVEN the resolved `sufficiency` model is not installed
- WHEN `openkos query "<question>"` runs
- THEN the answer is produced, exit code is `0`, and stderr names the model,
  `ollama pull <tag>` and `models: {sufficiency: null}`

### Requirement: `--json` Names The Sufficiency Model

The `llm` object of `query --json` MUST carry `sufficiency_model` immediately
after `model`: the resolved `sufficiency` task model when `sufficiency_check` is
enabled, `null` when it is disabled. The `schema_version` stays `1`; the field
is additive.

#### Scenario: The report names both models

- GIVEN a stock `ollama` workspace
- WHEN `openkos query "<question>" --json` runs
- THEN `llm.model` is the global model and `llm.sufficiency_model` is
  `gemma4:26b-a4b`
