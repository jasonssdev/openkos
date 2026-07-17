# Workspace Init Specification

## Purpose

`openkos init` creates a fresh OpenKOS workspace and OKF bundle in the
current directory, or refuses without writing anything if one already
exists.

## Requirements

### Requirement: Workspace Creation

The system MUST create `raw/`, `bundle/index.md`, `bundle/log.md`,
`openkos.yaml`, and `AGENTS.md` in the current directory and exit 0, when no
refusal condition (see Refusal Idempotency) applies. It MUST accept no
positional argument and no flags, operating on the current directory only.
On success, it MUST write a confirmation message to stdout that names what
was created.

#### Scenario: Fresh empty directory

- GIVEN an empty current directory
- WHEN `openkos init` runs
- THEN all five artifacts exist and the process exits 0

#### Scenario: Success message names what was created

- GIVEN an empty current directory
- WHEN `openkos init` runs successfully
- THEN stdout contains a message naming the created workspace/bundle
  artifacts
- AND the process exits 0

### Requirement: Bundle Index Shape

`bundle/index.md` MUST carry frontmatter whose parsed form has exactly one
key, `okf_version`, with parsed value equal to the string `0.1`, and an
empty body. The requirement is on the parsed value, not on the byte
sequence — either single- or double-quoted YAML scalars satisfy it.

#### Scenario: Exact parsed frontmatter, empty body

- GIVEN a successful init
- WHEN `bundle/index.md` is parsed
- THEN the parsed frontmatter equals exactly `{okf_version: "0.1"}` as data
  (quote style on disk is not asserted) and the body is empty

### Requirement: Bundle Log Shape

`bundle/log.md` MUST carry no frontmatter, and MUST contain
`# Directory Update Log`, a `## YYYY-MM-DD` section for the machine's
current LOCAL calendar date (not UTC's, when the two differ), and the
bullet `* **Initialization**: Created the bundle structure and the root
[index](/index.md).`

#### Scenario: Initialization entry

- GIVEN a successful init
- WHEN `bundle/log.md` is read
- THEN it has no frontmatter and contains the heading, the dated section,
  and the exact Initialization bullet above

#### Scenario: Dated section reflects local date, not UTC

- GIVEN a successful init on a machine whose local timezone is offset from
  UTC such that the local calendar date differs from the UTC calendar date
  at the moment `init` runs
- WHEN `bundle/log.md` is read
- THEN the `## YYYY-MM-DD` section matches the machine's local date, not
  UTC's

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

### Requirement: Static AGENTS.md Template

`AGENTS.md` MUST be a byte-identical copy of the packaged template, with no
per-workspace substitution.

#### Scenario: Byte-identical template

- GIVEN a successful init
- WHEN the generated `AGENTS.md` is compared to the packaged template
- THEN the content is byte-identical

### Requirement: No Concept-Type Folders

The system MUST NOT pre-create concept-type folders (`concepts/`,
`people/`, `sources/`, `decisions/`, or others) inside `bundle/`.

#### Scenario: Bundle holds only reserved files

- GIVEN a successful init
- WHEN `bundle/` is listed
- THEN it contains exactly `index.md` and `log.md`

### Requirement: Refusal Idempotency

The system MUST evaluate all refusal conditions in a pre-flight check
before writing any file. It MUST exit 1 and write nothing if any of the
following applies: `openkos.yaml` already exists; `AGENTS.md` already
exists; `raw/` exists and is non-empty; `bundle/` exists and is non-empty;
`raw` or `bundle` exists and is not a directory; or `raw` or `bundle`
exists as a pre-existing symlink (to a directory, to a file, or to a
nonexistent target). It MUST NEVER overwrite an existing file, follow a
symlinked workspace path when creating or writing, or write outside the
workspace root. It MUST leave no partial artifacts behind on refusal —
writing MUST NOT begin until every condition has been checked and none
apply. On refusal, it MUST write to stderr a message identifying which
condition triggered the refusal; when the trigger is a non-empty
`bundle/`, that message MUST additionally identify the leftover as a
likely remnant of an interrupted (crashed or killed) prior `init` and
point to manual remediation, instead of the bare "already exists and is
not empty".

#### Scenario: Existing openkos.yaml

- GIVEN a directory containing `openkos.yaml`
- WHEN init runs
- THEN it exits 1, no file is created or modified, and stderr identifies
  the existing `openkos.yaml` as the cause

#### Scenario: Existing AGENTS.md

- GIVEN a directory containing `AGENTS.md` and no `openkos.yaml`
- WHEN init runs
- THEN it exits 1, `AGENTS.md` is unchanged, no other file is created, and
  stderr identifies the existing `AGENTS.md` as the cause

#### Scenario: Non-empty raw/ or bundle/

- GIVEN no `openkos.yaml` but a non-empty `raw/` or `bundle/`
- WHEN init runs
- THEN it exits 1, writes nothing, and stderr identifies the non-empty
  directory as the cause

#### Scenario: raw or bundle exists as a non-directory

- GIVEN no `openkos.yaml`, and either `raw` or `bundle` exists in the
  current directory as a regular file, not a directory
- WHEN init runs
- THEN it exits 1 in pre-flight, writes nothing, raises no uncaught
  exception, and stderr identifies that path as not a directory

#### Scenario: Second run on an initialized workspace

- GIVEN a directory already initialized by init
- WHEN init runs again
- THEN it exits 1 and none of the five artifacts are overwritten or
  truncated

#### Scenario: No partial output kept on refusal

- GIVEN any refusal condition, detected in pre-flight before any write
- WHEN init exits 1
- THEN none of the five artifacts exist unless they pre-existed, and any
  pre-existing one is unchanged

#### Scenario: Symlinked raw or bundle target is refused

- GIVEN no `openkos.yaml`, and `raw` or `bundle` in the current directory
  is a pre-existing symlink, whether it targets a directory, a file, or a
  nonexistent path
- WHEN init runs
- THEN it exits 1 in pre-flight, writes nothing anywhere (including
  through the symlink or at its target), never follows the symlink, and
  stderr identifies that path as a symlink

#### Scenario: Stray bundle/ retry names the likely crashed-init cause

- GIVEN a prior `init` run left a non-empty `bundle/` behind (for example
  after a mid-write crash) and no `openkos.yaml` exists
- WHEN init runs again
- THEN it exits 1, writes nothing, and stderr's message identifies the
  leftover `bundle/` as a likely remnant of an interrupted init and points
  to remediation — not the bare "already exists and is not empty"

### Requirement: Write Failure Handling

If a Phase-B write fails after pre-flight has passed — for example due to
insufficient permissions, insufficient disk space, or a path that another
process created between pre-flight and the write — the system MUST write a
clear error message to stderr and exit with a non-zero code. It MUST NOT
let an uncaught exception traceback reach the user.

#### Scenario: Write failure surfaces a clean error

- GIVEN pre-flight has passed and a Phase-B write then fails, for example
  because the target lost write permission or was occupied by another
  process after pre-flight completed
- WHEN init attempts that write
- THEN it exits with a non-zero code, writes a clear error message to
  stderr, and no uncaught exception traceback reaches the user

### Requirement: Adoption of Non-Workspace Directories

The system MUST allow init to succeed in a non-empty current directory,
provided it is not already a workspace, no `AGENTS.md` exists, and any
pre-existing `raw/` or `bundle/` are empty.

#### Scenario: Adopt a folder of notes

- GIVEN unrelated existing files and no `openkos.yaml`, `AGENTS.md`,
  `raw/`, or `bundle/`
- WHEN init runs
- THEN it exits 0, creates the five artifacts, and leaves the existing
  files unchanged

### Requirement: Default raw/ Permissions

`raw/` MUST be created with the filesystem's default directory
permissions; no `chmod` MUST be applied.

#### Scenario: Default permissions

- GIVEN a successful init
- WHEN `raw/`'s mode is inspected
- THEN it matches an unmodified, freshly created directory's default mode

### Requirement: OKF Conformance

Init's output MUST satisfy OKF §9 conformance for a fresh bundle. Rules 1
(frontmatter present) and 2 (non-empty `type`) MUST pass vacuously, because
a fresh bundle contains zero non-reserved `.md` files for the mechanical
conformance check to inspect. Rule 3 (reserved-file structure) MUST hold by
construction, through the `index.md` and `log.md` shapes required by the
Bundle Index Shape and Bundle Log Shape requirements above. This slice MUST
NOT claim a mechanical check of rule 3 — that check is deferred to `lint`.
When the mechanical conformance check encounters a file it cannot read or
decode (for example a permission error or invalid encoding), it MUST
report that failure distinctly as an I/O/read error and MUST NOT report it
as a conformance violation.

#### Scenario: Mechanical check reports no violations on a fresh bundle

- GIVEN a successful init
- WHEN the OKF conformance check (rules 1 and 2) runs against `bundle/`
- THEN it reports no violations, because `bundle/` contains only the two
  reserved files and no non-reserved `.md` file exists to check

#### Scenario: Rule 3 holds by construction, not by mechanical check

- GIVEN a successful init
- WHEN `bundle/index.md` and `bundle/log.md` are inspected against the
  shapes required by Bundle Index Shape and Bundle Log Shape
- THEN both satisfy OKF §9 rule 3 by construction
- AND no mechanical rule-3 check is performed by this slice; that check is
  deferred to `lint`

#### Scenario: Unreadable file is reported as an I/O error, not a conformance violation

- GIVEN a non-reserved `.md` file under `bundle/` that exists but cannot
  be read as text — for example permission denied, or content that cannot
  be decoded with the expected encoding
- WHEN the OKF conformance check runs against `bundle/`
- THEN the failure is reported as an I/O/read error distinct from a
  conformance violation, and is not phrased as "no parseable frontmatter"
  or any other conformance-violation wording
