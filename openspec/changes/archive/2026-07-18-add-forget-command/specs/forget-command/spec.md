# Forget Command Specification

## Purpose

`openkos forget <concept-id>` is the missing removal counterpart to
`ingest`: it deletes a concept file and removes that concept's reference
from `index.md`, across any section, using the same Phase A (validate +
preview) / confirm-gate / Phase B (write) shape as `ingest`.

## Non-Goals

This spec does not define: tombstones or purge machinery (MVP-2, per
decision #717); updating any SQLite operational state (no such store
exists in `src/` yet — a no-op for MVP-1); detecting or rewriting dangling
inbound links from OTHER concepts that still link to the forgotten one;
correcting the two known `docs/cli.md` inaccuracies (SQLite claim, lint
dangling-link claim) — filed as a separate follow-up.

## Requirements

### Requirement: Concept-ID Resolution and Path Safety

A concept-id MUST be a bundle-relative POSIX path minus its `.md`
extension. `forget` MUST reject any concept-id containing `..` segments,
absolute paths, or any path resolving outside the bundle directory, and
MUST reject reserved filenames (`index.md`, `log.md`).

#### Scenario: Traversal segment rejected
- GIVEN a concept-id containing a `..` segment
- WHEN `openkos forget <concept-id>` runs
- THEN it refuses in Phase A, exits non-zero, and writes nothing

#### Scenario: Reserved filename rejected
- GIVEN a concept-id resolving to `index` or `log`
- WHEN `openkos forget <concept-id>` runs
- THEN it refuses in Phase A, exits non-zero, and writes nothing

### Requirement: Workspace Presence Check

`openkos forget` MUST refuse to run outside an initialized workspace,
using the same shared `require_workspace` check as `ingest`/`status`/`lint`,
and MUST NOT produce a raw traceback.

#### Scenario: Run outside a workspace
- GIVEN a directory that is not an initialized OpenKOS workspace
- WHEN `openkos forget <concept-id>` runs
- THEN it exits non-zero, prints a clear error to stderr, and prints no raw
  traceback

### Requirement: Nonexistent Concept Refusal

`openkos forget` MUST treat a concept-id whose file does not exist as an
error, not a silent no-op.

#### Scenario: Concept file missing
- GIVEN a concept-id with no corresponding file under the bundle
- WHEN `openkos forget <concept-id>` runs
- THEN it exits non-zero with a clear error and writes nothing

### Requirement: Generic Index Entry Removal

The system MUST provide a generic removal primitive in `bundle/index.py`
that drops the bullet whose markdown link resolves to `<concept-id>`,
searching ALL `index.md` sections (Sources, Concepts, People, Decisions).
Matching logic MUST live entirely in the bundle layer and MUST NOT import
from or depend on the lint module.

#### Scenario: Entry removed from any section
- GIVEN an `index.md` entry for `<concept-id>` under any section
- WHEN `openkos forget <concept-id>` completes
- THEN that entry is removed and all other entries are unchanged

#### Scenario: No matching entry is a no-op on the catalog
- GIVEN an `index.md` with no bullet linking to `<concept-id>`
- WHEN the removal primitive runs
- THEN `index.md` is returned unchanged (no unrelated bullet is dropped)

### Requirement: Log Entry on Forget

`openkos forget` MUST append a plain (non-tombstone) activity line to
`log.md` recording the removal, mirroring `ingest`'s log entry.

#### Scenario: Plain log line recorded
- GIVEN a successful forget of `<concept-id>`
- WHEN `log.md` is inspected afterward
- THEN it contains a new dated `**Forget**` line naming the removed
  concept, with no tombstone marker

### Requirement: Review/Confirm Flow

`forget` MUST compute the file deletion and index/log changes in memory
during Phase A without writing, present a preview, and perform Phase B
writes only after confirmation, using `ingest`'s exact gate precedence:
`--auto` skips confirmation; else config `review: false` skips it; else an
interactive TTY prompts via `typer.confirm`; else (non-TTY, no `--auto`)
the command refuses to write and exits non-zero.

#### Scenario: Non-TTY without --auto refuses to write
- GIVEN `review: true`, stdin is not a TTY, and `--auto` is not passed
- WHEN `openkos forget <concept-id>` runs
- THEN it refuses to write, exits non-zero, and nothing is deleted or
  modified

#### Scenario: --auto skips the prompt
- GIVEN a valid, existing concept-id
- WHEN `openkos forget <concept-id> --auto` runs
- THEN no confirmation prompt is shown and Phase B writes proceed directly

### Requirement: Catalog-Before-File Write Ordering

Phase B MUST remove the `index.md` entry and write the `log.md` line
BEFORE deleting the concept file, so the catalog never references a file
that does not exist. Phase B is NOT required to be transactional as a
whole; a failure partway through MAY leave a partial, git-recoverable
result.

#### Scenario: Catalog updated before file deletion
- GIVEN a confirmed forget of `<concept-id>`
- WHEN Phase B writes execute
- THEN `index.md` and `log.md` are updated before the concept file is
  deleted

#### Scenario: Interrupted Phase B never leaves a dangling catalog reference
- GIVEN a Phase B run interrupted after the catalog update but before file
  deletion
- WHEN the bundle is inspected afterward
- THEN the concept file may still exist as a benign orphan, but `index.md`
  never references a missing file; recovery is via `git status`/`git
  checkout`

### Requirement: Malformed Bundle Handling

`forget` MUST reuse the existing `OSError`/`ValueError` convention for
malformed bundle content (e.g. unparseable frontmatter) and MUST run the
same `require_workspace` gate as other commands.

#### Scenario: Malformed index.md
- GIVEN a bundle whose `index.md` cannot be parsed by the removal
  primitive
- WHEN `openkos forget <concept-id>` runs
- THEN it exits non-zero with a clear error and writes nothing

## Known Limitation

Forgetting a concept that OTHER concepts still link to leaves those
inbound links dangling. MVP-1 does not detect or rewrite inbound links;
this is documented, not silently fixed, and remains open for MVP-2.
