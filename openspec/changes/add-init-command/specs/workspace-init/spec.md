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

#### Scenario: Fresh empty directory

- GIVEN an empty current directory
- WHEN `openkos init` runs
- THEN all five artifacts exist and the process exits 0

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

### Requirement: Generated Workspace Config

`openkos.yaml` MUST be generated (not copied) with `name` set to the
current directory's base name, `model: qwen3.5:9b`, `review: true`,
`default_sensitivity: private`, `freshness_window: 7d`, `raw: raw/`, and
`bundle: bundle/`.

#### Scenario: Generated fields match directory

- GIVEN a directory named `my-workspace`
- WHEN init succeeds and `openkos.yaml` is parsed
- THEN the parsed values equal `name: my-workspace` plus the fixed values
  above (byte-level formatting, such as quoting, is not asserted)

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
before writing any file. It MUST exit 1 and write nothing if `openkos.yaml`
already exists, if `AGENTS.md` already exists, or if `raw/` or `bundle/`
exist and are non-empty. It MUST NEVER overwrite an existing file, and MUST
leave no partial artifacts behind on refusal — writing MUST NOT begin until
every condition has been checked and none apply.

#### Scenario: Existing openkos.yaml

- GIVEN a directory containing `openkos.yaml`
- WHEN init runs
- THEN it exits 1 and no file is created or modified

#### Scenario: Existing AGENTS.md

- GIVEN a directory containing `AGENTS.md` and no `openkos.yaml`
- WHEN init runs
- THEN it exits 1, `AGENTS.md` is unchanged, and no other file is created

#### Scenario: Non-empty raw/ or bundle/

- GIVEN no `openkos.yaml` but a non-empty `raw/` or `bundle/`
- WHEN init runs
- THEN it exits 1 and writes nothing

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
