# Delta for Workspace Init

## MODIFIED Requirements

### Requirement: Static openkos.yaml Template

`openkos.yaml` MUST be byte-identical to the packaged template except for
the `model:` line, which is the single user-selectable field; there MUST
be no other per-workspace substitution. It MUST NOT contain a `name` field
or any other field derived from the current directory; the directory
itself remains the single source of truth for the workspace's identity,
and nothing in `openkos.yaml` duplicates it. The packaged template pins
`review: true`, `default_sensitivity: private`, `freshness_window: 7d`,
`raw: raw/`, and `bundle: bundle/` — these MUST remain byte-identical to
the template regardless of the chosen model. The `model:` value MUST
resolve with precedence flag > interactive selection > default, default
`qwen3:8b`, and MUST be written into the template via constrained
plain-text token replacement of a single placeholder, never a YAML dumper
or serializer. On an interactive TTY run with no `--model` flag, "interactive
selection" is the numbered picker (see Interactive Model Picker Over
Installed Chat Models) when its preconditions hold, or the typed prompt
otherwise (see Graceful Degradation When Ollama Unreachable Or No Chat
Models). A colon `:` MUST be allowed in the value, since the default
`qwen3:8b` and Ollama `name:tag` tags contain one. An empty or blank
(post-trim) value, or a value containing whitespace, a quote (`'` or `"`),
`#`, or a newline, MUST be rejected before any file is written.
`validate_model` MUST additionally reject, case-insensitively, any value
that is EXACTLY one of the YAML 1.1 boolean/null literals recognized by
PyYAML's default resolver: `yes`, `no`, `true`, `false`, `on`, `off`,
`null`, and `~`, in any casing PyYAML accepts for those words. This
rejection MUST be exact-token, not substring — a value that merely
contains a reserved word as part of a longer token (e.g. `yesmodel`,
`notus`) MUST still be accepted.
(Previously: on a TTY, resolution always used a free-text
`typer.prompt("Model", default=DEFAULT_MODEL)`, accepting any validated
token typed by the user, regardless of whether that model was installed
or was a chat model.)

#### Scenario: Byte-identical template except model, default path

- GIVEN a successful init with no `--model` flag on a non-TTY stdin
- WHEN the generated `openkos.yaml` is compared to the packaged template
- THEN the content is identical except the `model:` line resolves to
  `qwen3:8b`, written with no prompt or picker shown

#### Scenario: Flag override selects the model

- GIVEN an empty current directory
- WHEN `openkos init --model gemma3` runs
- THEN `openkos.yaml` contains `model: gemma3` and every other field is
  byte-identical to the packaged template

#### Scenario: TTY, picker preconditions hold, accept the default

- GIVEN an empty current directory, no `--model` flag, stdin is a TTY,
  Ollama is reachable, and at least one chat model is installed
- WHEN `openkos init` runs and the user presses Enter at the picker
- THEN `openkos.yaml` contains `model: qwen3:8b` (the marked-recommended
  default)

#### Scenario: TTY, picker preconditions hold, custom selection

- GIVEN an empty current directory, no `--model` flag, stdin is a TTY,
  and the picker lists `qwen3:8b` and `llama3.1:8b` as chat candidates
- WHEN the user selects `llama3.1:8b` by its list number
- THEN `openkos.yaml` contains `model: llama3.1:8b`

#### Scenario: Non-TTY, no flag, silent default

- GIVEN an empty current directory, no `--model` flag, and stdin is not a
  TTY
- WHEN `openkos init` runs
- THEN no prompt or picker is shown, and `openkos.yaml` contains
  `model: qwen3:8b`

#### Scenario: Blank input is rejected

- GIVEN an empty current directory
- WHEN `openkos init` is run with `--model` set to an empty or
  whitespace-only string
- THEN init exits non-zero, no workspace artifact is created, and
  `openkos.yaml` does not exist

#### Scenario: Unsafe token is rejected

- GIVEN an empty current directory
- WHEN `openkos init --model` is passed a value containing whitespace,
  a quote, `#`, or a newline
- THEN init exits non-zero, no workspace artifact is created, and
  `openkos.yaml` does not exist

#### Scenario: Reserved YAML boolean/null word is rejected, case-insensitively

- GIVEN an empty current directory
- WHEN `openkos init --model` is passed any of `yes`, `no`, `true`,
  `false`, `on`, `off`, `null`, `~`, or a case variant
- THEN `validate_model` raises `ValueError`, init exits non-zero, and
  `openkos.yaml` does not exist

## ADDED Requirements

### Requirement: Interactive Model Picker Over Installed Chat Models

On an interactive (TTY) `openkos init` run where `--model` was not given,
and a reachable Ollama server reports at least one installed chat model
(after family-based embedding exclusion), the system MUST present a
numbered selection list of those chat models, with the packaged default
(`DEFAULT_MODEL`) visibly marked as recommended. Pressing Enter with no
input MUST select the marked default. The selected tag MUST be persisted
into `openkos.yaml` under the existing `model:` resolution and validation
rules.

#### Scenario: Picker lists installed chat models with default marked

- GIVEN a reachable Ollama server with chat models `qwen3:8b` and
  `llama3.1:8b` installed
- WHEN the picker is shown on a TTY `openkos init` run
- THEN both models are listed as numbered options and `qwen3:8b` is
  visibly marked "(recommended)"

#### Scenario: Selecting a number picks that model

- GIVEN the picker lists `qwen3:8b` as option 1 and `llama3.1:8b` as
  option 2
- WHEN the user enters `2`
- THEN `llama3.1:8b` is the selected model

#### Scenario: Empty input picks the default

- GIVEN the picker is shown with `qwen3:8b` marked recommended
- WHEN the user presses Enter without typing a number
- THEN `qwen3:8b` is the selected model

#### Scenario: Selection is persisted to openkos.yaml

- GIVEN the user selects a model from the picker
- WHEN init completes
- THEN `openkos.yaml`'s `model:` line contains exactly that selected tag

### Requirement: Graceful Degradation When Ollama Unreachable Or No Chat Models

WHEN Ollama is unreachable during the picker's probe, OR Ollama is
reachable but reports zero chat models after family-based embedding
exclusion, `init` MUST fall back to the typed-prompt/packaged-default
resolution behavior instead of showing a picker, and MUST NOT hard-fail:
the workspace MUST still be created and the process MUST exit 0 (subject
to the existing Refusal Idempotency and Write Failure Handling
requirements). The picker's probe MUST run before any file is written
(Phase A) and MUST NOT alter Phase A's existing refusal semantics.

#### Scenario: Unreachable Ollama falls back, workspace still created

- GIVEN Ollama is unreachable at the picker's probe time, no `--model`
  flag, and stdin is a TTY
- WHEN `openkos init` runs
- THEN no picker is shown, the typed-prompt/default flow resolves the
  model, the workspace is created, and the process exits 0

#### Scenario: Only embedding models installed falls back, no crash

- GIVEN Ollama is reachable but every installed model classifies as an
  embedding model (e.g. only `bge-m3`, family `"bert"`)
- WHEN `openkos init` runs on a TTY with no `--model` flag
- THEN no picker is shown, the typed-prompt/default flow resolves the
  model, no exception propagates, and the workspace is created

### Requirement: Non-Interactive Paths Bypass The Picker

An explicit `--model <tag>` flag MUST resolve the model outright with no
picker and no typed prompt shown, even when stdin is a TTY. WHEN stdin is
not a TTY and no `--model` flag was given, `init` MUST silently resolve to
the packaged default with no picker and no prompt shown.

#### Scenario: --model flag wins, no picker even on a TTY

- GIVEN stdin is a TTY and `--model mistral` is passed
- WHEN `openkos init` runs
- THEN no picker or prompt is shown, and `openkos.yaml` contains
  `model: mistral`

#### Scenario: Non-TTY silently takes the default, no picker

- GIVEN stdin is not a TTY and no `--model` flag is passed
- WHEN `openkos init` runs
- THEN no picker or prompt is shown, and `openkos.yaml` contains the
  packaged default

### Requirement: Embedding Models Excluded From Picker Candidates

Installed models classified as embedding models (per the llm-client
family-based classification, e.g. `bge-m3` with family `"bert"`) MUST NOT
appear as selectable entries in the picker's numbered list.

#### Scenario: Embedding model never offered as a picker choice

- GIVEN Ollama has both `qwen3:8b` (chat) and `bge-m3` (embedding,
  family `"bert"`) installed
- WHEN the picker is shown
- THEN only `qwen3:8b` appears in the numbered list; `bge-m3` is absent
