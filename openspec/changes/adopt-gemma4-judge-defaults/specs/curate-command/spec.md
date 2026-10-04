# Curate Command Specification (delta)

## MODIFIED Requirements

### Requirement: Each Stage Resolves Its Own Task Model

Every `needs_llm` stage MUST declare which measured task its LLM calls
belong to, and MUST contact the model resolved for that task by this
precedence: an explicit `models:` entry, then the packaged per-task default
(`DEFAULT_TASK_MODELS`, applied on the `ollama` backend only), then the global
`model:`. The packaged defaults are `gemma4:26b-a4b` for `adjudication`
(Identity) and `contradiction` (Contradictions) and nothing for the other
tasks (ADR-0047). An explicit
YAML null in `models:` MUST decline a packaged default and resolve to the
global `model:` — the operator's stated choice always wins over a shipped
one, and a packaged default that costs a large download MUST have an
opt-out that does not require restating the global tag. The packaged
defaults MUST NOT apply when `cfg.backend` is `openai-compatible`: a model
tag is meaningful only to its own provider, so those stages resolve the
global `model:` unless `models:` names them.
Stage tasks are `adjudication` (Identity), `edge_typing` (Structure),
`volatility_typing` (Metadata), and `contradiction` (Contradictions);
Preconditions makes no LLM calls and declares no task.

Tasks are keyed by TASK, never by stage or verb: Structure and the
standalone `suggest-relations` verb both run `suggest_edge_types`, and a
per-verb key would let the two drift onto different models.

WHEN a stage resolves a model other than the global `model:`, its cost gate
MUST disclose that model before asking for consent — the same item count
means a materially different spend depending on which model runs it. That
disclosure MUST NOT alter the `cost_line` literal itself, so a workspace
that names no per-task model produces byte-identical gate output (see
"Below-Cap Cost-Line Output Is Byte-Identical To Pre-Change Behavior").

WHEN a named model is not installed (for the `ollama` backend) or not
available on the configured server (for the `openai-compatible` backend),
ONLY the stage that named it MUST fail, and its remediation MUST name that
model rather than the global default, worded per the configured backend —
an `ollama pull` command for `ollama`, or advice to make the model available
on the configured server for `openai-compatible`, with no `ollama pull`
reference in that case. Falling back to the global model MUST NOT happen:
the operator would keep writing relation types believing they came from the
model they named.

#### Scenario: A stage runs on its own task model

- GIVEN `openkos.yaml` sets `model: qwen3:8b` and `models.edge_typing:
  gemma2:27b`
- WHEN `curate` reaches the Structure stage
- THEN Structure contacts `gemma2:27b` and every other stage contacts
  `qwen3:8b`

#### Scenario: The packaged judge default applies without any config

- GIVEN `cfg.backend == "ollama"` and `openkos.yaml` has no `models:` key
- WHEN `curate` reaches the Identity and Contradictions stages
- THEN both contact `gemma4:26b-a4b` and every other stage contacts the
  global `model:`

#### Scenario: The packaged judge default does not apply on openai-compatible

- GIVEN `cfg.backend == "openai-compatible"` and `openkos.yaml` has no
  `models:` key
- WHEN `curate` reaches the Identity and Contradictions stages
- THEN both contact the global `model:`

#### Scenario: An explicit null declines the packaged default

- GIVEN `openkos.yaml` sets `models.contradiction: null`
- WHEN `curate` reaches the Contradictions stage
- THEN Contradictions contacts the global `model:`

#### Scenario: The cost gate discloses a non-default model

- GIVEN Structure resolves `gemma2:27b` while the global default is
  `qwen3:8b`
- WHEN Structure's cost gate asks for consent
- THEN the printed output names `gemma2:27b` alongside the unchanged
  `"{n} untyped edge(s) -> {n} LLM call(s)"` line

#### Scenario: No per-task model leaves gate output unchanged

- GIVEN a stage that resolves the global `model:` (a stage with no packaged
  default and no `models:` entry, or any stage on the `openai-compatible`
  backend with no `models:` entry)
- WHEN that stage's cost gate asks for consent
- THEN the printed output is byte-identical to the wording without per-task models

#### Scenario: A missing packaged judge fails only its own stage

- GIVEN `cfg.backend == "ollama"`, no `models:` key, and `gemma4:26b-a4b` is
  not installed
- WHEN `curate` runs
- THEN Identity and Contradictions each report unavailable with
  `ollama pull gemma4:26b-a4b`, never a fallback to the global `model:`, and
  Structure and Metadata still run

#### Scenario: A missing task model fails only its own stage

- GIVEN `cfg.backend == "ollama"` and `models.edge_typing` names a model
  that is not installed
- WHEN `curate` runs
- THEN Structure reports unavailable with an `ollama pull` remediation
  naming THAT model, and Metadata and Contradictions still run

#### Scenario: A missing task model on the openai-compatible backend fails only its own stage

- GIVEN `cfg.backend == "openai-compatible"` and `models.edge_typing` names
  a model that is not available on the configured server
- WHEN `curate` runs
- THEN Structure reports unavailable with a remediation naming THAT model
  and advising the operator to make it available on the configured server,
  with no `ollama pull` reference, and Metadata and Contradictions still run
