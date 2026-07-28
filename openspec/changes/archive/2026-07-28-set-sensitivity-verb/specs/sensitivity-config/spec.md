# Sensitivity Config Specification

## Purpose

`sensitivity-config` is the write layer that lets a human set one existing
concept's `sensitivity` field directly: the CLI verb `set-sensitivity` reads
a concept's frontmatter, changes exactly the `sensitivity` value, and writes
the file back. It is modeled on `volatility-config`'s read/write split
(`suggest-volatility` reads, `set-volatility` writes) and is the only writer
of a single concept's `sensitivity` outside `ingest` (stamp) and `merge`
(recompute via `okf.combine_sensitivity`). This verb never calls
`okf.combine_sensitivity` and consults no workspace floor.

## Requirements

### Requirement: `set-sensitivity` Command Shape

The system MUST provide a CLI verb `openkos set-sensitivity <concept-id>
<level>` that, on success, sets `sensitivity: <level>` in that one concept's
frontmatter. The verb MUST accept an `--auto` flag that skips the interactive
confirm prompt, and a distinct flag that permits a lowering assignment on a
path where the confirm prompt does not run.

#### Scenario: Successful set updates the concept's frontmatter

- GIVEN an existing concept `<concept-id>` with `sensitivity: private`
- WHEN `set-sensitivity <concept-id> public` runs and the confirm gate is
  accepted
- THEN the concept's frontmatter has `sensitivity: public` after the run

### Requirement: Strict Level Validation

`<level>` MUST exact-match one of `okf.SENSITIVITY_ORDER`. Validation MUST
happen before any read of the concept file or the workspace. Any other value
MUST fail with a clear stderr message and non-zero exit, and MUST NOT read
or write the concept file.

#### Scenario: Invalid level value is rejected

- GIVEN `set-sensitivity <concept-id> bogus` where `bogus` is not a valid
  `SENSITIVITY_ORDER` member
- WHEN the command runs
- THEN stderr states the value is invalid, the exit code is non-zero, and no
  concept file is read or written

### Requirement: Concept-Id Resolution And Refusals

`<concept-id>` MUST be resolved to a concept file the same way other
mutating verbs resolve one. An absolute id, an id containing a `..`
segment, an id matching a reserved basename, or an id that does not resolve
to an existing concept file MUST each be refused before any write: clear
stderr message, non-zero exit, nothing written.

#### Scenario: Absolute concept-id is refused

- GIVEN a `<concept-id>` given as an absolute path
- WHEN `set-sensitivity` runs
- THEN stderr reports the refusal, the exit code is non-zero, and nothing
  is written

#### Scenario: Concept-id containing a `..` segment is refused

- GIVEN a `<concept-id>` containing a `..` path segment
- WHEN `set-sensitivity` runs
- THEN stderr reports the refusal, the exit code is non-zero, and nothing
  is written

#### Scenario: Reserved basename is refused

- GIVEN a `<concept-id>` matching a reserved basename
- WHEN `set-sensitivity` runs
- THEN stderr reports the refusal, the exit code is non-zero, and nothing
  is written

#### Scenario: Nonexistent concept file is refused

- GIVEN a `<concept-id>` with no corresponding concept file in the workspace
- WHEN `set-sensitivity` runs
- THEN stderr reports the refusal, the exit code is non-zero, and nothing
  is written

### Requirement: Byte-Preserving Frontmatter Read-Modify-Write

Writing MUST change exactly the `sensitivity` field in the concept's
frontmatter. Every other field, and the entire document body, MUST be
byte-identical to the pre-write state.

#### Scenario: Only the sensitivity field changes

- GIVEN an existing concept with multiple frontmatter fields and a body
- WHEN `set-sensitivity <concept-id> confidential` runs and is confirmed
- THEN the frontmatter's `sensitivity` field is `confidential` and every
  other frontmatter field and the body are byte-identical to before

### Requirement: Preview And Confirm Gate

Before writing, the verb MUST print a preview line naming the concept, its
current `sensitivity` value, and the new value, then apply the standard
confirm-gate precedence used by other mutating verbs: `--auto` skips the
prompt, workspace config `review: false` skips the prompt, an interactive
TTY prompts, and a non-interactive session without `--auto` refuses with
exit 1. Declining the prompt MUST result in no write.

#### Scenario: Confirming the preview writes the change

- GIVEN a valid `set-sensitivity` invocation and confirm input `y`
- WHEN the command runs
- THEN the preview line is printed before the prompt, and the concept file
  is written after confirmation

#### Scenario: Declining the preview performs no write

- GIVEN a valid `set-sensitivity` invocation and confirm input `n` (or
  equivalent decline)
- WHEN the command runs
- THEN no write occurs and the concept file is unchanged

### Requirement: Idempotent No-Op

WHEN the concept's current `sensitivity` already equals `<level>`, the verb
MUST print a no-op message, perform no write, create no commit, and exit 0.

#### Scenario: Re-setting the same level is a no-op

- GIVEN a concept with `sensitivity: private` already set
- WHEN `set-sensitivity <concept-id> private` runs
- THEN a no-op message is printed, the concept file is unchanged, no commit
  is created, and the exit code is 0

### Requirement: Auto-Commit On Successful Write

A successful write MUST auto-commit the concept file, and MUST append a
dated entry to `bundle/log.md`, consistent with the mutating-verb
convention used elsewhere in the CLI. `bundle/index.md` MUST NOT be touched,
since this verb edits an existing catalog entry rather than creating one.

#### Scenario: Successful write creates a commit and a log entry

- GIVEN a valid `set-sensitivity` invocation confirmed with `y`
- WHEN the write completes
- THEN a new commit exists covering the concept file and `bundle/log.md`,
  and `bundle/index.md` is unchanged

### Requirement: Lowering Requires Explicit Permission Wherever The Confirm Prompt Does Not Run

Raising or same-value assignment MUST pass under the standard confirm gate
with no extra flag. Lowering — assigning a `<level>` that ranks below the
concept's current `sensitivity` per `okf.SENSITIVITY_ORDER` — MUST pass the
standard gate when the confirm prompt actually runs and is accepted. On
every path where the confirm prompt does not run — including but not
limited to `--auto`, and workspace config `review: false`, which silences
the prompt for every verb workspace-wide — a lowering MUST additionally
require an explicit downgrade-permitting flag. Without that flag, the verb
MUST refuse in Phase A: exit 1, no write, no commit, and a stderr message
naming the required flag.

A current `sensitivity` value that is missing, blank, or not a recognized
`SENSITIVITY_ORDER` member MUST be ranked fail-closed (as the lowest rank),
so that assigning any level other than the lowest rank from such a value is
classified as a raise, and assigning the lowest rank from a dirty current
value is classified as a lowering subject to this same rule.

#### Scenario: Interactive lowering with accepted confirm needs no extra flag

- GIVEN a concept with `sensitivity: confidential` and an interactive TTY
- WHEN `set-sensitivity <concept-id> public` runs, the preview shows
  `confidential -> public`, and the confirm prompt is accepted with no
  downgrade flag passed
- THEN the write succeeds and the concept's `sensitivity` becomes `public`

#### Scenario: Lowering under `--auto` without the flag is refused

- GIVEN a concept with `sensitivity: confidential`
- WHEN `set-sensitivity <concept-id> public --auto` runs without the
  downgrade-permitting flag
- THEN the command refuses with exit 1, the message names the required
  flag, and the concept file and any commit log are unchanged

#### Scenario: Lowering under `--auto` with the flag succeeds

- GIVEN a concept with `sensitivity: confidential`
- WHEN `set-sensitivity <concept-id> public --auto` runs with the
  downgrade-permitting flag also passed
- THEN the write succeeds without an interactive prompt

#### Scenario: Lowering under `review: false` without the flag is refused

- GIVEN a workspace configured with `review: false` and a concept with
  `sensitivity: private`
- WHEN `set-sensitivity <concept-id> public` runs without `--auto` and
  without the downgrade-permitting flag
- THEN the command refuses with exit 1 because the confirm prompt would not
  run, the message names the required flag, and nothing is written

#### Scenario: A dirty current value ranks fail-closed for lowering purposes

- GIVEN a concept whose current `sensitivity` value is missing, blank, or
  not a recognized `SENSITIVITY_ORDER` member
- WHEN `set-sensitivity <concept-id> public --auto` runs without the
  downgrade-permitting flag, where `public` is the lowest `SENSITIVITY_ORDER`
  rank
- THEN the assignment is classified as a lowering, the command refuses with
  exit 1, the message names the required flag, and nothing is written

### Requirement: Scope Is Exactly One Named Concept

The verb MUST affect only the frontmatter of the one concept named by
`<concept-id>`. It MUST NOT modify, read for the purpose of propagation, or
otherwise touch any sibling or derived concept's `sensitivity` value. The
`--help` text and the post-write success message MUST each state that only
the one named concept is touched.

#### Scenario: Sibling and derived concepts are untouched

- GIVEN a workspace containing a source concept and its derived concepts,
  where only `<concept-id>` is the target
- WHEN `set-sensitivity <concept-id> <level>` runs and is confirmed
- THEN only the target concept's `sensitivity` changes; every sibling and
  derived concept's frontmatter is byte-identical to before the run
