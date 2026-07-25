# Delta for Workspace Init

## ADDED Requirements

### Requirement: Config Model Field Type Enforcement

`read_config` MUST enforce that a present `model` value in `openkos.yaml`
is a `str`. It MUST slot this check into the existing "checked `is not
None`, not truthiness" conditional used for every field's fallback, without
altering that pattern for `model` or any other field (e.g. `review: false`
MUST still survive untouched). WHEN the parsed `model` value is present but
not a `str` (for example a YAML 1.1 boolean or null literal that PyYAML
resolved to `bool`/`NoneType`), `read_config` MUST raise `ValueError` with a
message that identifies `model` as the offending field and states that a
string value is required. WHEN `model` is absent from the YAML, or present
as YAML `null`, it MUST fall back to `DEFAULT_MODEL` unchanged — this is not
a type error. WHEN `model` is a normal string, `read_config` MUST use it
unchanged.

#### Scenario: Boolean-typed model value raises ValueError

- GIVEN an `openkos.yaml` containing `model: yes`, which PyYAML's default
  resolver parses as the Python `bool` `True`
- WHEN `read_config` parses the file
- THEN it raises `ValueError` naming `model` as the offending field and
  stating that a string is required

#### Scenario: Absent or null model falls back to the default

- GIVEN an `openkos.yaml` with no `model` key, or `model: null` / `model: ~`
- WHEN `read_config` parses the file
- THEN the resulting `Config.model` equals `DEFAULT_MODEL`, and no
  `ValueError` is raised

#### Scenario: String model is used unchanged

- GIVEN an `openkos.yaml` containing `model: qwen3:8b`
- WHEN `read_config` parses the file
- THEN the resulting `Config.model` equals `"qwen3:8b"` exactly

#### Scenario: Explicit review: false is unaffected by the model type check

- GIVEN an `openkos.yaml` containing `review: false` and a valid string
  `model`
- WHEN `read_config` parses the file
- THEN `Config.review` is `False`, preserving the existing "is not None, not
  truthiness" fallback behavior for other fields

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
`validate_model` MUST additionally reject, case-insensitively, any value
that is EXACTLY one of the YAML 1.1 boolean/null literals recognized by
PyYAML's default resolver: `yes`, `no`, `true`, `false`, `on`, `off`,
`null`, and `~`, in any casing PyYAML accepts for those words (including
but not limited to `Yes`, `YES`, `No`, `NO`, `True`, `TRUE`, `False`,
`FALSE`, `On`, `ON`, `Off`, `OFF`, `Null`, `NULL`). This rejection MUST be
exact-token, not substring — a value that merely contains a reserved word
as part of a longer token (e.g. `yesmodel`, `notus`) MUST still be
accepted, since it does not resolve to a YAML boolean/null on round-trip.
(Previously: `validate_model` rejected blank/whitespace values, quotes,
`#`, newlines, and leading/trailing-colon or leading-dash forms, but did
not reject the YAML 1.1 reserved boolean/null word set, allowing values
like `yes` to be written and then re-read as the Python `bool` `True`.)

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

#### Scenario: Reserved YAML boolean/null word is rejected, case-insensitively

- GIVEN an empty current directory
- WHEN `openkos init --model` is passed any of `yes`, `no`, `true`,
  `false`, `on`, `off`, `null`, `~`, or a case variant such as `Yes`,
  `YES`, `TRUE`, or `Off`
- THEN `validate_model` raises `ValueError` with a clear message, init
  exits non-zero, no workspace artifact is created, and `openkos.yaml`
  does not exist

#### Scenario: Reserved-word substring is still accepted

- GIVEN an empty current directory
- WHEN `openkos init --model` is passed `yesmodel` or `notus` — tags that
  contain a reserved word as a substring but are not exactly one
- THEN init succeeds and `openkos.yaml` contains that exact tag, unmodified

#### Scenario: Legitimate existing tags remain accepted

- GIVEN an empty current directory
- WHEN `openkos init --model` is passed `qwen3:8b`, `llama3.1:8b`, or
  `bge-m3`
- THEN init succeeds and `openkos.yaml` contains that exact tag
