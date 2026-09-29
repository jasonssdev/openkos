# Delta for Workspace Init

## MODIFIED Requirements

### Requirement: Static openkos.yaml Template

`openkos.yaml` MUST be byte-identical to the packaged template except for
the `model:` line and the `embedding_model:` line, which are the ONLY
user-selectable fields; there MUST be no other per-workspace substitution.
It MUST NOT contain a `name` field or any other field derived from the
current directory; the directory itself remains the single source of truth
for the workspace's identity, and nothing in `openkos.yaml` duplicates it.
The packaged template pins `review: true`, `default_sensitivity: private`,
`freshness_window: 7d`, `raw: raw/`, and `bundle: bundle/` — these MUST
remain byte-identical to the template regardless of the chosen model(s). The
packaged template MUST also document `backend`, `base_url`, and
`embedding_base_url` as commented-out keys with explanatory comments; `init`
MUST NOT write, substitute, or uncomment any of the three, for either the
default flow or any flag/picker path — a fresh workspace is always written
with `backend` absent, which resolves to `ollama` by `backend-selection`'s
default. The `model:` value MUST resolve with precedence flag > interactive
selection > default, default `qwen3:8b`, and MUST be written into the
template via constrained plain-text token replacement of a single
placeholder, never a YAML dumper or serializer. The `embedding_model:`
value MUST resolve with the SAME precedence shape — `--embedding-model`
flag > interactive picker over the vetted allowlist > `DEFAULT_EMBEDDING_MODEL`
— written via a second, independent plain-text placeholder token,
never a YAML dumper or serializer. On an interactive TTY run with no
`--model`/`--embedding-model` flag, "interactive selection" is the
numbered picker (see Interactive Model Picker Over Installed Chat Models,
and Interactive Embedding Model Picker Over The Vetted Allowlist) when its
preconditions hold, or the typed prompt/silent default otherwise (see
Graceful Degradation When Ollama Unreachable Or No Chat Models). A colon
`:` MUST be allowed in either value, since the defaults and Ollama
`name:tag` tags contain one. An empty or blank (post-trim) value, or a
value containing whitespace, a quote (`'` or `"`), `#`, or a newline, MUST
be rejected before any file is written, for both fields. `validate_model`
MUST additionally reject, case-insensitively, any value that is EXACTLY
one of the YAML 1.1 boolean/null literals recognized by PyYAML's default
resolver: `yes`, `no`, `true`, `false`, `on`, `off`, `null`, and `~`, in
any casing PyYAML accepts for those words. This rejection MUST be
exact-token, not substring — a value that merely contains a reserved word
as part of a longer token (e.g. `yesmodel`, `notus`) MUST still be
accepted. `validate_embedding_model` MUST apply this SAME YAML-safety and
reserved-word rejection, independent of allowlist membership: an
off-allowlist value passed via `--embedding-model` MUST still pass this
YAML-safety check and MUST be written, with a warning (see Off-Allowlist
Embedding Model Flag Is Warned, Not Blocked), never silently coerced to
the default.
(Previously: the template had no `backend`, `base_url`, or
`embedding_base_url` keys at all, because no second backend existed; a
workspace's endpoint was entirely `OLLAMA_HOST`-environment-driven and
undocumented in the shipped config.)

#### Scenario: Byte-identical template except model, default path

- GIVEN a successful init with no `--model`/`--embedding-model` flag on a
  non-TTY stdin
- WHEN the generated `openkos.yaml` is compared to the packaged template
- THEN the content is identical except the `model:` line resolves to
  `qwen3:8b` and the `embedding_model:` line resolves to `bge-m3`, written
  with no prompt or picker shown for either field

#### Scenario: Flag override selects the model

- GIVEN an empty current directory
- WHEN `openkos init --model gemma3` runs
- THEN `openkos.yaml` contains `model: gemma3` and every other field
  (including `embedding_model:`) is byte-identical to the packaged
  template's resolved defaults

#### Scenario: Embedding flag override selects the embedding model

- GIVEN an empty current directory and `bge-m3-vetted` is on the allowlist
- WHEN `openkos init --embedding-model bge-m3-vetted` runs
- THEN `openkos.yaml` contains `embedding_model: bge-m3-vetted` and every
  other field is byte-identical to the packaged template

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

- GIVEN an empty current directory, no `--model`/`--embedding-model` flag,
  and stdin is not a TTY
- WHEN `openkos init` runs
- THEN no prompt or picker is shown for either field, and `openkos.yaml`
  contains `model: qwen3:8b` and `embedding_model: bge-m3`

#### Scenario: Blank input is rejected

- GIVEN an empty current directory
- WHEN `openkos init` is run with `--model` or `--embedding-model` set to
  an empty or whitespace-only string
- THEN init exits non-zero, no workspace artifact is created, and
  `openkos.yaml` does not exist

#### Scenario: Unsafe token is rejected

- GIVEN an empty current directory
- WHEN `openkos init --model` or `--embedding-model` is passed a value
  containing whitespace, a quote, `#`, or a newline
- THEN init exits non-zero, no workspace artifact is created, and
  `openkos.yaml` does not exist

#### Scenario: Reserved YAML boolean/null word is rejected, case-insensitively

- GIVEN an empty current directory
- WHEN `openkos init --model` or `--embedding-model` is passed any of
  `yes`, `no`, `true`, `false`, `on`, `off`, `null`, `~`, or a case variant
- THEN the corresponding validator raises `ValueError`, init exits
  non-zero, and `openkos.yaml` does not exist

#### Scenario: The template documents the new backend keys as comments

- GIVEN the packaged `openkos.yaml.template`
- WHEN it is inspected
- THEN it contains `backend`, `base_url`, and `embedding_base_url` each as a
  commented-out line with an explanatory comment, none of them active

#### Scenario: A fresh init never writes backend, base_url, or embedding_base_url

- GIVEN a successful `openkos init` with no special flags
- WHEN the generated `openkos.yaml` is inspected
- THEN it contains no active `backend:`, `base_url:`, or
  `embedding_base_url:` line — only `model:` and `embedding_model:` were
  substituted, and the new keys remain commented exactly as in the template
