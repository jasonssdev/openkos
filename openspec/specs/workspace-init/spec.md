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
was created, and MUST additionally write a next-step hint to stdout naming
`openkos ingest <path>` as the next command to run. This hint MUST be
printed unconditionally on every successful run — init has no TTY/quiet
gating.

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

#### Scenario: Success output includes the next-step hint

- GIVEN an empty current directory
- WHEN `openkos init` runs successfully
- THEN stdout also contains a next-step hint that names `openkos ingest
  <path>` as the next command to run
- AND this hint is printed regardless of whether stdin is a TTY

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

### Requirement: Config Model Field Type Enforcement

`read_config` MUST enforce that a present `model` value in `openkos.yaml` is a `str`. It MUST slot this check into the existing "checked `is not
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

##### Scenario: Boolean-typed model value raises ValueError

- GIVEN an `openkos.yaml` containing `model: yes`, which PyYAML's default
  resolver parses as the Python `bool` `True`
- WHEN `read_config` parses the file
- THEN it raises `ValueError` naming `model` as the offending field and
  stating that a string is required

##### Scenario: Absent or null model falls back to the default

- GIVEN an `openkos.yaml` with no `model` key, or `model: null` / `model: ~`
- WHEN `read_config` parses the file
- THEN the resulting `Config.model` equals `DEFAULT_MODEL`, and no
  `ValueError` is raised

##### Scenario: String model is used unchanged

- GIVEN an `openkos.yaml` containing `model: qwen3:8b`
- WHEN `read_config` parses the file
- THEN the resulting `Config.model` equals `"qwen3:8b"` exactly

##### Scenario: Explicit review: false is unaffected by the model type check

- GIVEN an `openkos.yaml` containing `review: false` and a valid string
  `model`
- WHEN `read_config` parses the file
- THEN `Config.review` is `False`, preserving the existing "is not None, not
  truthiness" fallback behavior for other fields

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

### Requirement: Non-Fatal Post-Success Ollama Preflight

After a successful `init` (Phase B writes complete, before the process
returns), the system MUST run exactly one non-fatal, bounded-timeout Ollama
preflight probe using the resolved model and the same short timeout `doctor`
uses for its Ollama-reachable check. The probe MUST reuse the existing
config-free `list_models()`/`model_tag_matches()` primitives and MUST NOT
pull a model or start the Ollama server. WHEN the probe finds Ollama
unreachable, OR reachable but the resolved model not installed, OR the probe
itself raises any exception, `init` MUST print exactly ONE warning naming
`openkos doctor` as the next diagnostic step, and MUST still exit 0. WHEN
Ollama is reachable AND the resolved model is installed, no warning MUST be
printed. This requirement MUST NOT alter `init`'s exit code, refusal
behavior, or file-writer guarantee under any preflight outcome.

#### Scenario: Ollama unreachable prints a warning, exit still 0

- GIVEN a successful init and Ollama is not reachable at probe time
- WHEN the post-success preflight runs
- THEN a warning is printed naming `openkos doctor`
- AND `init` exits 0

#### Scenario: Model missing prints a warning, exit still 0

- GIVEN a successful init, Ollama is reachable, but the resolved model is
  not installed
- WHEN the post-success preflight runs
- THEN a warning is printed naming `openkos doctor`
- AND `init` exits 0

#### Scenario: Ollama and model both available — no warning

- GIVEN a successful init, Ollama is reachable, and the resolved model is
  installed
- WHEN the post-success preflight runs
- THEN no warning is printed
- AND `init` exits 0

#### Scenario: Preflight itself errors — still non-fatal

- GIVEN a successful init and the preflight probe raises any exception
  (e.g. an unexpected transport error) rather than a clean unreachable
  result
- WHEN the post-success preflight runs
- THEN the exception is caught broadly, a warning naming `openkos doctor`
  is printed, `init` exits 0, and no traceback reaches the user

#### Scenario: Preflight never pulls a model or starts the server

- GIVEN a successful init with any preflight outcome
- WHEN the post-success preflight runs
- THEN no `ollama pull` or `ollama serve` action is invoked by `openkos`

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

### Requirement: Conditional Git Repository Initialization

`init` MUST run `git init` in the workspace root ONLY when
`vcs.git.repo_root(cwd)` returns `None` (the workspace is not already inside
any git working tree). It MUST NOT run `git init` when `cwd` already
resolves to a git working tree, whether the workspace root itself is that
tree's root or a subdirectory of a parent repo.

#### Scenario: Fresh empty directory outside any repo

- GIVEN an empty current directory with no enclosing git working tree
- WHEN `openkos init` runs
- THEN `git init` runs in the workspace root and a `.git` directory exists

#### Scenario: Directory already inside a git working tree

- GIVEN the current directory is inside an existing git working tree (either
  its root or a subdirectory)
- WHEN `openkos init` runs
- THEN `git init` MUST NOT run and no nested `.git` directory is created

### Requirement: Gitignore Scaffolding

`init` MUST write a `.gitignore` at the workspace root that ignores
`.openkos/` and `.DS_Store`, UNLESS a `.gitignore` already exists at that
path, in which case `init` MUST NOT overwrite or modify it.

#### Scenario: No existing .gitignore

- GIVEN a workspace root with no `.gitignore`
- WHEN `openkos init` runs
- THEN a `.gitignore` is created that ignores `.openkos/` and `.DS_Store`

#### Scenario: Existing .gitignore is preserved

- GIVEN a workspace root that already contains a `.gitignore`
- WHEN `openkos init` runs
- THEN the existing `.gitignore` content is unchanged byte-for-byte

### Requirement: Scoped Initial Commit

When git identity (`user.name` and `user.email`) is configured and
available, `init` MUST make exactly one commit whose message is
`chore(openkos): initialize workspace`. The commit MUST include only the
canonical files created by `init` (`openkos.yaml`, `AGENTS.md`, `raw/**`,
`bundle/**`) plus any `.gitignore` written by this run — staged individually
or by explicit path, never via a blanket `git add -A` — so pre-existing
unrelated dirty content in a host working tree is never swept into the
commit.

#### Scenario: Fresh repo, full commit

- GIVEN a fresh empty directory and a configured git identity
- WHEN `openkos init` runs
- THEN one commit exists with message `chore(openkos): initialize
  workspace`, and it contains `openkos.yaml`, `AGENTS.md`, `raw/**`,
  `bundle/**`, and `.gitignore`

#### Scenario: Existing repo, scoped commit excludes unrelated content

- GIVEN `cwd` is inside an existing git working tree containing unrelated
  untracked or modified files, and a configured git identity
- WHEN `openkos init` runs
- THEN the resulting commit contains only the files `init` itself created
  in this run, and the unrelated pre-existing dirty content remains
  untouched and uncommitted

#### Scenario: Existing repo with pre-existing .gitignore, scoped commit

- GIVEN `cwd` is inside an existing git working tree that already has a
  `.gitignore`, and a configured git identity
- WHEN `openkos init` runs
- THEN `git init` does not run, the existing `.gitignore` is unchanged, and
  the commit contains only the newly created openkos files, not the
  pre-existing `.gitignore`

### Requirement: Non-Fatal Git Degradation

WHEN `git` is unavailable on `PATH`, OR git identity (`user.name`/
`user.email`) is unset, OR any other git step in the git-setup block fails
(e.g. `git commit` rejected by a hook, a lock, or disk pressure, after
`git add` already staged files), `init` MUST emit a non-fatal WARNING on
stderr and MUST still exit 0 with a fully valid, complete workspace (all
five pre-existing artifacts plus `.gitignore`, per the unmodified Workspace
Creation requirement). WHEN identity specifically is unset, `init` MUST
still create the repository (if applicable) and write `.gitignore`, but
MUST SKIP the commit step entirely — it MUST NOT fall back to any injected
bot identity. WHEN a git step fails for any OTHER reason mid-setup (a
repository and/or `.gitignore` may already exist, and files may already be
staged but not committed), the WARNING MUST NOT claim setup was cleanly
"skipped" and MUST point the user at a concrete recovery step (e.g. running
`git status` to inspect and finish setup manually).

#### Scenario: Git unavailable

- GIVEN `git` is not installed or not on `PATH`
- WHEN `openkos init` runs
- THEN a non-fatal WARNING is printed to stderr, `init` exits 0, and every
  workspace artifact required by the Workspace Creation requirement is
  present

#### Scenario: Git identity unset

- GIVEN `git` is available but `user.name` and/or `user.email` are unset
- WHEN `openkos init` runs
- THEN a non-fatal WARNING is printed to stderr, `init` exits 0, `.gitignore`
  and (when applicable) the repository are created, and no commit is made

#### Scenario: Git error mid-setup leaves a partial but honestly-reported state

- GIVEN `git` is available and identity is configured, but a git step after
  staging (e.g. `git commit`) fails (hook rejection, lock, disk pressure)
- WHEN `openkos init` runs
- THEN a non-fatal WARNING is printed to stderr that does NOT claim setup was
  cleanly skipped and DOES point at a concrete recovery step (e.g.
  `git status`), and `init` exits 0 with the workspace itself still fully
  valid

### Requirement: Git Step Ordering and Layering

The git-setup step (conditional `git init`, `.gitignore` scaffolding, and
the scoped commit) MUST run strictly AFTER Phase B's last canonical write
(the `openkos.yaml` marker) and MUST NOT block or invalidate an otherwise-
successful workspace on any git failure. The canonical layer (`model`,
`bundle`, `state`) MUST NOT import `vcs`; git orchestration MUST live in
the `init` CLI command, calling `vcs.git` write primitives.

#### Scenario: Git step runs after the workspace marker

- GIVEN a fresh empty directory
- WHEN `openkos init` runs
- THEN `openkos.yaml` exists before the git-setup step executes, so any git
  failure occurs only after the workspace is already valid

#### Scenario: Canonical layer stays git-agnostic

- GIVEN the `openkos` source tree
- WHEN `src/openkos/model/`, `src/openkos/bundle/`, and `src/openkos/state/`
  are inspected for imports
- THEN none of them imports `openkos.vcs`
