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
resolve with precedence flag > interactive prompt > default, default
`qwen3:8b`, and MUST be written into the template via constrained
plain-text token replacement of a single placeholder, never a YAML dumper
or serializer. A colon `:` MUST be allowed in the value, since the default
`qwen3:8b` and Ollama `name:tag` tags contain one. An empty or blank
(post-trim) value, or a value containing whitespace, a quote (`'` or `"`),
`#`, or a newline, MUST be rejected before any file is written.
(Previously: the template pinned a static `model: qwen3:8b` line with no
per-workspace substitution of any field.)

#### Scenario: Byte-identical template except model, default path

- GIVEN a successful init with no `--model` flag on a non-TTY stdin
- WHEN the generated `openkos.yaml` is compared to the packaged template
- THEN the content is identical except the `model:` line resolves to
  `qwen3:8b`, written with no prompt shown

#### Scenario: No directory-derived field, regardless of directory name

- GIVEN a directory with any name, including one long enough or containing
  consecutive spaces such that it would previously have risked corruption
  if written into a YAML scalar
- WHEN init succeeds and `openkos.yaml` is written
- THEN the file contains no field derived from the directory name, all
  fields other than `model:` match the packaged template exactly, and the
  directory name causes no corruption of the `model:` line or any other
  line, independent of the directory's name

#### Scenario: Flag override selects the model

- GIVEN an empty current directory
- WHEN `openkos init --model gemma3` runs
- THEN `openkos.yaml` contains `model: gemma3` and every other field is
  byte-identical to the packaged template

#### Scenario: TTY prompt, accept the default

- GIVEN an empty current directory, no `--model` flag, and stdin is a TTY
- WHEN `openkos init` runs and the user accepts the offered default at the
  prompt
- THEN the prompt's displayed default is `qwen3:8b`, and `openkos.yaml`
  contains `model: qwen3:8b`

#### Scenario: TTY prompt, custom value

- GIVEN an empty current directory, no `--model` flag, and stdin is a TTY
- WHEN `openkos init` runs and the user enters `mistral` at the prompt
- THEN `openkos.yaml` contains `model: mistral`

#### Scenario: Non-TTY, no flag, silent default

- GIVEN an empty current directory, no `--model` flag, and stdin is not a
  TTY
- WHEN `openkos init` runs
- THEN no prompt is shown, and `openkos.yaml` contains `model: qwen3:8b`

#### Scenario: Flag wins even when stdin is a TTY

- GIVEN an empty current directory, stdin is a TTY, and `--model mistral`
  is passed
- WHEN `openkos init` runs
- THEN no prompt is shown, and `openkos.yaml` contains `model: mistral`

#### Scenario: Blank input is rejected

- GIVEN an empty current directory
- WHEN `openkos init` is run with `--model` set to an empty string, or a
  string that is empty or whitespace-only after trimming (flag or prompt
  path)
- THEN init exits non-zero, no workspace artifact is created, and
  `openkos.yaml` does not exist

#### Scenario: Unsafe token is rejected

- GIVEN an empty current directory
- WHEN `openkos init --model` is passed a value containing whitespace
  (e.g. `bad model`), a quote (`'` or `"`), `#`, or a newline
- THEN init exits non-zero, no workspace artifact is created, and
  `openkos.yaml` does not exist

#### Scenario: Colon-containing tag is accepted verbatim

- GIVEN an empty current directory
- WHEN `openkos init --model mistral:7b` runs, or `openkos init` runs and
  resolves to the default `qwen3:8b`
- THEN init succeeds, and `openkos.yaml` contains the `model:` line with
  the colon-containing tag written verbatim (`model: mistral:7b` or
  `model: qwen3:8b` respectively)
</content>
