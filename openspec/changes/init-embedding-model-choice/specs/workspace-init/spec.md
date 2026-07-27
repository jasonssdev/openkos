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
remain byte-identical to the template regardless of the chosen model(s).
The `model:` value MUST resolve with precedence flag > interactive
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
(Previously: `openkos.yaml` was byte-identical to the template except for
`model:`, described as "the single user-selectable field";
`embedding_model` was never written to `openkos.yaml` by `init` and had no
flag, picker, or validator.)

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

## ADDED Requirements

### Requirement: Vetted 1024-Dim Embedding Model Allowlist

The system MUST expose a static, code-level allowlist of embedding model
tags known to produce 1024-float vectors, satisfying the existing
`EMBED_DIM` contract. The allowlist MUST include `bge-m3`. The picker
described in Interactive Embedding Model Picker Over The Vetted Allowlist
MUST offer ONLY models that are BOTH installed (per `list_models()`) AND
present on this allowlist. The allowlist MUST gate the picker's candidate
list ONLY — it MUST NOT gate `--embedding-model` (see Off-Allowlist
Embedding Model Flag Is Warned, Not Blocked) or a value hand-written
directly into `openkos.yaml`.

#### Scenario: Allowlist includes the packaged default

- GIVEN the vetted embedding-model allowlist
- WHEN it is inspected
- THEN `bge-m3` is a member

#### Scenario: Allowlist gates only the picker, not the flag or manual edit

- GIVEN an embedding model tag that is NOT on the allowlist
- WHEN it is passed via `--embedding-model`, or hand-written into an
  existing `openkos.yaml`'s `embedding_model:` key
- THEN it is accepted in both cases (subject to YAML-safety validation),
  and is never rejected solely for allowlist non-membership

### Requirement: Interactive Embedding Model Picker Over The Vetted Allowlist

On an interactive (TTY) `openkos init` run where `--embedding-model` was
not given, and a reachable Ollama server reports at least one installed
model that is ALSO on the vetted allowlist, the system MUST present a
numbered selection list of those candidates, with the packaged default
(`bge-m3`) visibly marked as recommended when present among them. Pressing
Enter with no input MUST select the marked default. The selected tag MUST
be persisted into `openkos.yaml` under `embedding_model:`, validated
through `validate_embedding_model`. This picker MUST run in Phase A,
mirroring the chat-model picker's placement strictly before any workspace
write.

#### Scenario: Picker lists installed allowlisted embedding models with default marked

- GIVEN a reachable Ollama server with `bge-m3` installed and on the
  allowlist
- WHEN the embedding picker is shown on a TTY `openkos init` run
- THEN `bge-m3` is listed as a numbered option and visibly marked
  "(recommended)"

#### Scenario: Selecting a number picks that embedding model

- GIVEN the embedding picker lists `bge-m3` as option 1 and
  `qwen3-embedding:0.6b` (also allowlisted) as option 2
- WHEN the user enters `2`
- THEN `qwen3-embedding:0.6b` is the selected embedding model

#### Scenario: Empty input picks the default

- GIVEN the embedding picker is shown with `bge-m3` marked recommended
- WHEN the user presses Enter without typing a number
- THEN `bge-m3` is the selected embedding model

#### Scenario: Selection is persisted to openkos.yaml

- GIVEN the user selects an embedding model from the picker
- WHEN init completes
- THEN `openkos.yaml`'s `embedding_model:` line contains exactly that
  selected tag

### Requirement: Graceful Degradation Of The Embedding Picker

WHEN Ollama is unreachable during the embedding picker's probe, OR Ollama
is reachable but reports zero installed models that are also on the
allowlist, `init` MUST fall back to the silent-default resolution behavior
for `embedding_model` instead of showing a picker, and MUST NOT hard-fail:
the workspace MUST still be created and the process MUST exit 0. This
probe MUST reuse the chat picker's existing probe call — it MUST NOT issue
a second, separate reachability request.

#### Scenario: Unreachable Ollama falls back, workspace still created

- GIVEN Ollama is unreachable at the embedding picker's probe time, no
  `--embedding-model` flag, and stdin is a TTY
- WHEN `openkos init` runs
- THEN no embedding picker is shown, `embedding_model` silently resolves
  to `bge-m3`, the workspace is created, and the process exits 0

#### Scenario: Zero allowlisted models installed falls back, no crash

- GIVEN Ollama is reachable but no installed model is on the vetted
  allowlist
- WHEN `openkos init` runs on a TTY with no `--embedding-model` flag
- THEN no embedding picker is shown, `embedding_model` silently resolves
  to `bge-m3`, no exception propagates, and the workspace is created

### Requirement: Off-Allowlist Embedding Model Flag Is Warned, Not Blocked

WHEN `--embedding-model` names a value that passes YAML-safety validation
but is NOT on the vetted allowlist, `init` MUST still resolve, validate,
and write that value to `openkos.yaml`, and MUST print a non-fatal warning
to stderr naming the value as off-allowlist, without blocking the run or
altering its exit code.

#### Scenario: Off-allowlist flag value is written with a warning

- GIVEN an empty current directory
- WHEN `openkos init --embedding-model custom-embed:latest` runs, and
  `custom-embed:latest` is not on the allowlist
- THEN `openkos.yaml` contains `embedding_model: custom-embed:latest`, a
  non-fatal warning is printed to stderr naming it off-allowlist, and
  init exits 0

### Requirement: Sticky Re-Embed Warning On Every Successful Init

Every successful `init` MUST print, unconditionally, a warning stating
that the chosen `embedding_model` is sticky: changing it later (by editing
`openkos.yaml` or passing a different `--embedding-model` on a future
`init` of a different workspace) forces a full corpus re-embed the next
time `reindex` runs, via the existing model-tag gate. This warning MUST be
printed regardless of TTY/non-TTY, regardless of which embedding model was
chosen, and is not conditioned on any prior corpus existing — a fresh
workspace has nothing to re-embed yet, so the warning MUST be worded about
FUTURE cost, never present cost. It MUST be printed next to the existing
post-success Ollama preflight warning, after Phase B completes.

#### Scenario: Warning prints on every successful init, TTY or not

- GIVEN a successful `openkos init`, with or without a TTY, with any
  resolved `embedding_model`
- WHEN Phase B completes
- THEN stdout or stderr contains a warning stating the embedding-model
  choice is sticky and that changing it later forces a full re-embed via
  `reindex`

#### Scenario: Warning is worded about future cost on a fresh workspace

- GIVEN a fresh, just-initialized workspace with no prior corpus and
  nothing yet to re-embed
- WHEN the sticky re-embed warning is printed
- THEN its wording describes a FUTURE re-embed cost triggered by changing
  the model later, and does NOT claim any re-embed has happened or is
  happening now
