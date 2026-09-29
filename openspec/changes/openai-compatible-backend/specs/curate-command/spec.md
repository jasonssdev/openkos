# Delta for Curate Command

## MODIFIED Requirements

### Requirement: Each Stage Resolves Its Own Task Model

Every `needs_llm` stage MUST declare which measured task its LLM calls
belong to, and MUST contact the model resolved for that task by this
precedence: an explicit `models:` entry, then the packaged per-task default
(`DEFAULT_TASK_MODELS`), then the global `model:` (#515, #513). An explicit
YAML null in `models:` MUST decline a packaged default and resolve to the
global `model:` — the operator's stated choice always wins over a shipped
one, and a packaged default that costs a large download MUST have an
opt-out that does not require restating the global tag.
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
(Previously: the missing-model remediation was worded only as
`ollama pull`, because only the `ollama` backend existed.)

#### Scenario: A stage runs on its own task model

- GIVEN `openkos.yaml` sets `model: qwen3:8b` and `models.edge_typing:
  gemma2:27b`
- WHEN `curate` reaches the Structure stage
- THEN Structure contacts `gemma2:27b` and every other stage contacts
  `qwen3:8b`

#### Scenario: The packaged default applies without any config

- GIVEN `openkos.yaml` has no `models:` key
- WHEN `curate` reaches the Structure stage
- THEN Structure contacts the packaged `edge_typing` model and every other
  stage contacts the global `model:`

#### Scenario: An explicit null declines the packaged default

- GIVEN `openkos.yaml` sets `models.edge_typing: null`
- WHEN `curate` reaches the Structure stage
- THEN Structure contacts the global `model:`

#### Scenario: The cost gate discloses a non-default model

- GIVEN Structure resolves `gemma2:27b` while the global default is
  `qwen3:8b`
- WHEN Structure's cost gate asks for consent
- THEN the printed output names `gemma2:27b` alongside the unchanged
  `"{n} untyped edge(s) -> {n} LLM call(s)"` line

#### Scenario: No per-task model leaves gate output unchanged

- GIVEN a workspace with no `models:` key
- WHEN any stage's cost gate asks for consent
- THEN the printed output is byte-identical to its pre-#515 wording

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

### Requirement: Availability Is Tracked Per Model, Not Per Run

An availability failure — `OllamaUnavailable`/`OllamaModelNotFound` for the
`ollama` backend, or `OpenAICompatibleUnavailable`/
`OpenAICompatibleModelNotFound` for the `openai-compatible` backend — MUST
skip only the later `needs_llm` stages that resolve the SAME model. A stage
resolving a different model MUST still be attempted (#515).

This replaces the run-scoped skip: one failed connection no longer settles
reachability for models it never contacted. The deliberate cost is that a
genuinely dead server is contacted once per DISTINCT model rather than once
per run; clients MUST be cached by model so stages sharing a tag share one
connection. In a workspace with no `models:` override every stage resolves
the same tag, so the observable behavior is unchanged.
(Previously: only `OllamaUnavailable`/`OllamaModelNotFound` were named,
because only the `ollama` backend existed.)

#### Scenario: Failure on one model does not skip a stage on another

- GIVEN Structure resolves `gemma2:27b`, Metadata resolves the global
  default, and Structure fails with an availability error
- WHEN `curate` continues
- THEN Metadata is still attempted

#### Scenario: Failure still skips a later stage on the same model

- GIVEN no `models:` override, so every stage resolves the same tag, and an
  early stage fails with an availability error
- WHEN `curate` continues
- THEN every later `needs_llm` stage is skipped as unavailable
