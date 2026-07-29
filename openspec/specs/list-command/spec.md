# List Command Specification

## Purpose

`openkos list [TYPE]` is the missing discovery counterpart to the id-taking
write verbs (`forget`, `relate`, `merge`, `unmerge`, `set-sensitivity`): it
enumerates bundle objects with their id, sensitivity, lifecycle status, and
title, read-only, in a single bundle walk.

## Non-Goals

This spec does not define: `--json` or any structured output (deferred, not
banned — see issue #240 note under the confidential-titles requirement);
`--sensitivity` filtering, `--fields`, or full-text search over titles;
recency ordering (alphabetical by id only); any change to `status`,
`duplicates`, `survey_bundle`, or the concept-id format; MCP/API surfaces.
`status`'s "Read-Only and Human-Readable Only" clause is NOT inherited by
this spec — `list` states its own read-only requirement independently.

## Requirements

### Requirement: Workspace Presence Check

`openkos list` MUST refuse to run outside an initialized workspace, using
the same `require_workspace` check every other read-only verb uses.

`list` MUST have exactly three exit outcomes, in this order:

1. **Argument refusal** — an unrecognized TYPE filter (see *Type Filter
   Vocabulary*) or an out-of-range `--limit` (see *Output Bounding*) exits
   non-zero BEFORE the workspace is consulted.
2. **Workspace refusal** — `require_workspace` failure exits non-zero.
3. **Success** — every other invocation exits 0, including an empty bundle
   and a bundle containing unreadable or unparseable documents.

Once past those two refusals, `require_workspace` failure MUST be the only
remaining non-zero exit path: no bundle content, however malformed, may make
`list` fail.

This argument-before-workspace ordering is not new. It matches
`set-volatility`, which validates `tier` and `concept_type` and exits 1 on
either before calling `config.require_workspace`
(`src/openkos/cli/main.py:3545-3563`). A caller who typed a bad flag should
learn that from the flag, not from an unrelated workspace error.

#### Scenario: Run outside a workspace
- GIVEN a directory that is not an initialized OpenKOS workspace
- WHEN `openkos list` runs with valid arguments
- THEN it exits non-zero, prints a clear error, and prints no raw traceback

#### Scenario: Bad argument outside a workspace reports the argument
- GIVEN a directory that is not an initialized OpenKOS workspace
- WHEN `openkos list nonsense-type` runs
- THEN it exits non-zero naming the unrecognized type, NOT the missing
  workspace, because argument validation precedes the workspace check

### Requirement: Exactly One Bundle Walk

`openkos list` MUST perform exactly one bundle walk (one call to the
`_iter_docs`-based enumerator) per invocation, regardless of filters or
limits applied. It MUST NOT call `lifecycle.deprecated_concept_ids` or any
other function that re-walks the bundle; id, sensitivity, status, and title
MUST all be derived in the same pass.

#### Scenario: Single walk regardless of filter
- GIVEN a bundle with objects of multiple types
- WHEN `openkos list people --limit 5` runs
- THEN the enumerator is invoked exactly once, and filtering/limiting are
  applied to its in-memory result, not by re-walking

### Requirement: Type Filter Vocabulary

The optional positional `TYPE` argument MUST accept `link_dir` values
(e.g. `people`, `sources`) as canonical, and MUST also accept
`REGISTRY.name` values (e.g. `Person`) as a case-sensitive alias resolving
to the same type. Help text and error messages MUST enumerate only the
canonical `link_dir` names.

#### Scenario: Filter by link_dir
- GIVEN a bundle containing people and sources
- WHEN `openkos list people` runs
- THEN only objects under `people/` are printed

#### Scenario: Filter by REGISTRY.name alias
- GIVEN a bundle containing people
- WHEN `openkos list Person` runs
- THEN the same rows as `openkos list people` are printed

#### Scenario: Unknown type filter
- GIVEN any bundle
- WHEN `openkos list bogus-type` runs
- THEN it exits non-zero, prints an error listing only canonical `link_dir`
  names, and prints no raw traceback

### Requirement: Output Bounding

`openkos list` MUST default to printing at most 50 rows and MUST print a
truncation footer reporting the number shown and the total matched when the
result is truncated. `--all` MUST print every matched row with no footer.
`--limit N` MUST print at most N rows with a footer when truncated.
`--limit 0` and any negative `--limit` MUST be rejected as invalid input
without printing any rows.

#### Scenario: Default limit truncates with footer
- GIVEN a bundle with 412 matching objects
- WHEN `openkos list` runs with no flags
- THEN it prints 50 rows and a footer reporting "50 of 412" (or equivalent)

#### Scenario: --all bypasses the limit
- GIVEN a bundle with 412 matching objects
- WHEN `openkos list --all` runs
- THEN all 412 rows are printed and no truncation footer appears

#### Scenario: Invalid limit rejected
- GIVEN any bundle
- WHEN `openkos list --limit 0` or `openkos list --limit -1` runs
- THEN it exits non-zero, prints a clear error, and prints no rows

### Requirement: Deprecated and Superseded Visibility

`openkos list` MUST show deprecated and superseded objects by default,
marked with their status, with no flag to hide them. Objects deleted from
disk by `merge` (`src/openkos/bundle/merge.py:23`) are absent from the walk
and therefore never appear as a distinct "merged" row.

#### Scenario: Deprecated object shown by default
- GIVEN a bundle containing an object marked `deprecated`
- WHEN `openkos list` runs
- THEN the object is printed with `STATUS` = `deprecated`, with no flag
  required

### Requirement: Column Layout

Each row MUST print exactly four columns, in order: `ID`, `SENSITIVITY`,
`STATUS`, `TITLE`. `SENSITIVITY` and `STATUS` MUST always be present
(never blank) for every row that was successfully parsed.

#### Scenario: Row layout
- GIVEN a bundle with one active, public concept
- WHEN `openkos list` runs
- THEN the row shows `ID`, `SENSITIVITY`, `STATUS`, then `TITLE`, in that
  order

### Requirement: Confidential Titles Are Printed in Full

`openkos list` MUST print every object's complete title regardless of its
`sensitivity` level. There MUST be no redaction, no flag that hides or
truncates a title, and no omitted row based on sensitivity; output MUST be
byte-identical across sensitivity levels for the same underlying data.
`sensitivity` governs what LEAVES the machine, not what the owner sees on
their own terminal: `--include-confidential`
(`src/openkos/sensitivity.py:78-99`) is exclusively an LLM-send gate
(`should_block` / `blocks_llm_send`) and MUST NOT be overloaded into a
display gate. Precedent: `duplicates`
(`src/openkos/cli/main.py:5149-5218`) already prints ids for confidential
objects with no gate. Issue #240 ("scope the confidential gate to non-local
LLM backends") may change what `--include-confidential` means for LLM
sends, but `list` performs no LLM send at all, so #240's outcome does not
change this requirement in either direction.

#### Scenario: Confidential title printed in full
- GIVEN a bundle containing a concept marked `sensitivity: confidential`
  with title "Jane's Medical History"
- WHEN `openkos list` runs
- THEN the row prints the complete title "Jane's Medical History"
  unredacted, identically to how a public object's title would print

### Requirement: Empty Bundle and Unparseable Document Handling

`openkos list` on an empty bundle MUST print a friendly empty-state message
and exit 0. A document the walk cannot read or parse MUST still be printed
as a row — following the enumerator's fail-safe convention (never raise),
not `sensitivity`'s fail-closed convention — with its id, a blank or
placeholder `TITLE`, and `SENSITIVITY`/`STATUS` filled from whatever
metadata was recoverable, defaulting to safe values when not. `list` MUST
NOT raise or abort the walk because one document failed to parse.

#### Scenario: Empty bundle
- GIVEN a freshly initialized workspace with no objects
- WHEN `openkos list` runs
- THEN it prints a friendly "no objects" message and exits 0

#### Scenario: Unparseable document does not abort the walk
- GIVEN a bundle containing one well-formed object and one document with
  invalid/unparseable frontmatter
- WHEN `openkos list` runs
- THEN both rows are printed — the well-formed object normally and the
  broken document with its id and a blank/placeholder title — and the
  command exits 0 with no raw traceback

### Requirement: Read-Only, No Structured Output

`openkos list` MUST NOT write, modify, or delete any bundle file, and MUST
NOT offer `--json` or any other structured output mode.

#### Scenario: No mutation on any run
- GIVEN any workspace state
- WHEN `openkos list` runs with any combination of flags
- THEN no file under the workspace is created, modified, or deleted, and no
  `--json` flag is accepted
