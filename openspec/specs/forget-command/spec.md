# Forget Command Specification

## Purpose

`openkos forget <concept-id>` is the missing removal counterpart to
`ingest`: it deletes a concept file and removes that concept's reference
from `index.md`, across any section, using the same Phase A (validate +
preview) / confirm-gate / Phase B (write) shape as `ingest`.

## Non-Goals

This spec does not define: tombstones or purge machinery (MVP-2, per
decision #717); updating any SQLite operational state (no such store
exists in `src/` yet — a no-op for MVP-1); rewriting or retargeting dangling
inbound links from OTHER concepts that still link to the forgotten one (detection
is in scope; silent rewriting is not); correcting the two known `docs/cli.md`
inaccuracies (SQLite claim, lint dangling-link claim) — filed as a separate
follow-up.

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

`openkos forget` MUST append a tombstone-marked entry to `log.md`
recording the removal. The tombstone entry MUST be a chronological
`log.md` line (same date-grouped style as other entries) and MUST include:
a marker that distinguishes it from a plain activity line, a timestamp,
the removed concept's id, and the removed concept's title (read from its
frontmatter before deletion). The tombstone MUST NOT add a `status` field,
frontmatter, or any leftover concept file; the concept file is still
deleted in Phase B.

#### Scenario: Tombstone log line recorded
- GIVEN a successful forget of `<concept-id>` with no inbound references
  (or `--force` overriding detected ones)
- WHEN `log.md` is inspected afterward
- THEN it contains a new tombstone-marked entry naming the removed
  concept's id and title, distinguishable from a plain activity line

#### Scenario: Tombstone survives an idempotent re-run
- GIVEN `forget` was already run once for `<concept-id>` and the tombstone
  line exists
- WHEN `forget <concept-id>` is run again (e.g. after a benign orphaned
  file from an interrupted Phase B)
- THEN the prior tombstone line is left intact and any new write behaves
  per the existing catalog-before-file ordering

### Requirement: Inbound Reference Detection

`openkos forget` MUST enumerate every inbound markdown link and inbound
typed relation edge targeting the concept being forgotten, using the same
inbound-reference scanners `merge` uses, invoked in a detect-only
(non-mutating) mode. Detection MUST run in Phase A, only after the
existing concept-id path-safety and existence checks succeed, and before
any write. Every detected reference (the referencing concept's id and the
reference kind: link or typed relation) MUST be surfaced in the Phase A
preview.

#### Scenario: No inbound references found
- GIVEN a concept with no inbound markdown links or typed relations
- WHEN `openkos forget <concept-id>` runs
- THEN the preview shows no inbound-reference warning

#### Scenario: Inbound markdown link detected
- GIVEN another concept holds a markdown link to `<concept-id>`
- WHEN `openkos forget <concept-id>` runs
- THEN the preview lists the referencing concept and the link kind

#### Scenario: Inbound typed relation detected
- GIVEN another concept holds a typed relation (e.g. `supersedes`,
  `references`) targeting `<concept-id>`
- WHEN `openkos forget <concept-id>` runs
- THEN the preview lists the referencing concept and the relation type

### Requirement: Unverifiable Referrer Detection (Fail-Closed)

Because the reused inbound-relation scanner silently skips any file whose
frontmatter or `relations:` cannot be parsed, `openkos forget` MUST run an
independent fail-closed check so a malformed referrer cannot cause a
concept to be silently deleted while an inbound edge to it still exists. A
file whose frontmatter/`relations:` fails to parse MUST be surfaced as an
`unverifiable` reference WHEN the target concept's id appears as a raw
substring of that file's text (the parser cannot confirm or rule out a
real inbound edge, so the safe assumption is that one may exist). A
malformed file that does NOT mention the target's id cannot reference it
and MUST be ignored, so unrelated bundle corruption never blocks an
unrelated forget.

#### Scenario: Unverifiable referrer mentioning the target is surfaced
- GIVEN a referrer file whose frontmatter cannot be parsed but whose text
  contains `<concept-id>`
- WHEN `openkos forget <concept-id>` runs
- THEN it is surfaced as an unverifiable reference in the Phase A preview

#### Scenario: Unverifiable referrer not mentioning the target is ignored
- GIVEN a malformed file that does not mention `<concept-id>`
- WHEN `openkos forget <concept-id>` runs
- THEN it is not surfaced and does not block the forget

### Requirement: Refuse Forget When Inbound References Exist, Unless `--force`

`openkos forget` MUST refuse to proceed (Phase A refusal, exits non-zero,
writes nothing) when one or more inbound references OR unverifiable
referrers were detected, UNLESS `--force` is passed. When `--force` is
passed and such references exist, the forget proceeds; those inbound
references are NOT retargeted or rewritten and are left dangling as an
accepted `--force` tradeoff.

#### Scenario: Inbound markdown link refuses by default
- GIVEN an inbound markdown link was detected for `<concept-id>`
- WHEN `openkos forget <concept-id>` runs without `--force`
- THEN it refuses in Phase A, exits non-zero, and writes nothing

#### Scenario: Inbound typed relation refuses by default
- GIVEN an inbound typed relation was detected for `<concept-id>`
- WHEN `openkos forget <concept-id>` runs without `--force`
- THEN it refuses in Phase A, exits non-zero, and writes nothing

#### Scenario: Unverifiable referrer refuses by default
- GIVEN an unverifiable referrer (unparseable file mentioning
  `<concept-id>`) was detected
- WHEN `openkos forget <concept-id>` runs without `--force`
- THEN it refuses in Phase A, exits non-zero, and writes nothing

#### Scenario: `--force` overrides the refusal
- GIVEN an inbound reference (link or typed relation) was detected for
  `<concept-id>`
- WHEN `openkos forget <concept-id> --force` runs (subject to the confirm
  gate)
- THEN the forget proceeds, the tombstone is written, and the referencing
  concept's link or relation is left intact but now dangling

### Requirement: `--force` Is Orthogonal to the Confirm Gate

`--force` MUST bypass ONLY the inbound-reference refusal. It MUST NOT
skip, alter, or otherwise interact with the confirm-gate precedence
(`--auto` skips the prompt; else config `review: false` skips it; else an
interactive TTY prompts; else non-TTY refuses). `--force` and `--auto` are
independent flags and MAY be combined or used separately.

#### Scenario: `--force` alone still prompts on a TTY
- GIVEN an interactive TTY, `review: true`, and detected inbound
  references
- WHEN `openkos forget <concept-id> --force` runs (no `--auto`)
- THEN the inbound-reference refusal is bypassed but `typer.confirm` still
  prompts before Phase B writes

#### Scenario: `--force` and `--auto` combined skip both gates
- GIVEN detected inbound references
- WHEN `openkos forget <concept-id> --force --auto` runs
- THEN neither the inbound-reference refusal nor the confirmation prompt
  blocks the write

#### Scenario: `--force` without `--auto` on non-TTY still refuses at the confirm gate
- GIVEN stdin is not a TTY, `review: true`, `--force` is passed, and no
  inbound references exist
- WHEN `openkos forget <concept-id> --force` runs without `--auto`
- THEN it refuses to write and exits non-zero via the unchanged confirm
  gate, not the inbound-reference gate

### Requirement: Resurrection Interaction Disclosure

When the concept being forgotten carries an OUTBOUND `supersedes` edge
(X supersedes Y), forgetting X deletes that edge, so Y is no longer
effective-deprecated and re-enters retrieval under the status-aware
retrieval predicate. `openkos forget` MUST disclose this in the Phase A
preview, naming the target concept whose effective status changes, before
the confirm gate is reached.

#### Scenario: Forgetting a superseding concept discloses resurrection
- GIVEN concept X has an outbound `supersedes` edge targeting concept Y
- WHEN `openkos forget X` runs
- THEN the preview names Y and states that Y re-enters retrieval once X is
  removed

#### Scenario: No outbound `supersedes` edge, no disclosure
- GIVEN the concept being forgotten has no outbound `supersedes` edge
- WHEN `openkos forget <concept-id>` runs
- THEN the preview contains no resurrection-disclosure line

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
