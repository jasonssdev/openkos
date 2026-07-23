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

### Requirement: Scope Selection

`openkos forget` MUST accept `--scope {self,source}`, defaulting to `self`.
`--scope self` MUST produce a purge set of exactly the root concept-id, with
all S2a behavior (path safety, detection, refusal, preview, confirm, write
ordering) unchanged and byte-identical. `--scope source` MUST expand the
purge set to the root plus every concept resolved by Provenance Descendant
Resolution.

#### Scenario: Default scope is self
- GIVEN `openkos forget <concept-id>` runs with no `--scope` flag
- WHEN Phase A executes
- THEN behavior is identical to S2a: the purge set contains only
  `<concept-id>`

### Requirement: Provenance Descendant Resolution

When `--scope source` is passed, the system MUST resolve the purge set via a
pure helper that adds a concept C (C != root) iff every entry of C's
`provenance` frontmatter list refers to a concept already in the purge set
(the orphan-after-delete subset invariant): a concept with ANY provenance
entry OUTSIDE the current purge set MUST NOT be added and MUST be preserved.
Resolution MUST iterate to a fixed point and MUST run only after the root's
existing path-safety and existence checks succeed, and before detection or
preview.

#### Scenario: Single-source children are cascade members
- GIVEN Source X and two concepts each with `provenance: [X]`
- WHEN `openkos forget X --scope source` runs
- THEN the purge set contains X and both children (3 concepts)

#### Scenario: Multi-source child is preserved
- GIVEN a concept C with `provenance: [X, Y]` and only X is being forgotten
- WHEN `openkos forget X --scope source` runs
- THEN C is NOT added to the purge set and is left untouched

#### Scenario: Path safety runs before descendant resolution
- GIVEN a concept-id containing a `..` segment and `--scope source`
- WHEN `openkos forget <concept-id> --scope source` runs
- THEN it refuses on path-safety grounds before any provenance lookup occurs

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

### Requirement: Full-Set Preview and Count Confirmation

The Phase A preview MUST list every concept-id in the purge set, every
`index.md`/`log.md` edit, and every EXTERNAL (not-in-set) inbound reference
or unverifiable referrer. The confirm prompt MUST state the total number of
concepts to be deleted. `--force` MUST NOT auto-confirm this count; the
unchanged confirm-gate precedence (`--auto` / config `review: false` / TTY
prompt / non-TTY refusal) still governs the count-bearing prompt.

#### Scenario: Preview names every id and the count
- GIVEN a purge set of 3 concepts under `--scope source`
- WHEN `openkos forget <source-id> --scope source` runs
- THEN the preview lists all 3 concept-ids and the prompt states "3"
  concepts

#### Scenario: `--force` does not auto-confirm the count
- GIVEN an interactive TTY and `--scope source --force` with no external
  references
- WHEN the command runs without `--auto`
- THEN `typer.confirm` still prompts, stating the delete count, before
  Phase B writes

#### Scenario: Non-TTY without `--auto` still refuses on the cascade path
- GIVEN stdin is not a TTY, `review: true`, `--scope source --force`, no
  external references
- WHEN `openkos forget <source-id> --scope source --force` runs without
  `--auto`
- THEN it refuses at the confirm gate and writes nothing, same as
  `--scope self`

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

`openkos forget` MUST append ONE tombstone-marked `log.md` entry PER concept
removed from the purge set (N individual `**Tombstone**` lines, not a single
grouped line), each in the same chronological, date-grouped style as S2a's
single tombstone, each naming that concept's id and title (read from its
frontmatter before deletion).

#### Scenario: N tombstone lines for a cascade
- GIVEN a successful `--scope source` forget deleting 3 concepts
- WHEN `log.md` is inspected afterward
- THEN it contains 3 distinct tombstone-marked entries, one per removed
  concept

#### Scenario: `--scope self` still writes exactly one tombstone
- GIVEN a successful `--scope self` (default) forget
- WHEN `log.md` is inspected afterward
- THEN it contains exactly one tombstone entry, identical to S2a

### Requirement: Inbound Reference Detection

`openkos forget` MUST enumerate every inbound markdown link and inbound
typed relation edge targeting ANY concept in the purge set (the root under
`--scope self`, or the root plus its resolved descendants under `--scope
source`), using the same inbound-reference scanners `merge` uses, invoked in
a detect-only (non-mutating) mode. Detection MUST run in Phase A, only after
path-safety/existence checks and (when applicable) Provenance Descendant
Resolution succeed, and before any write. Every detected reference MUST be
surfaced in the Phase A preview together with the purge-set member id it
targets.

#### Scenario: No inbound references found
- GIVEN no purge-set member has an inbound markdown link or typed relation
- WHEN `openkos forget <concept-id>` runs
- THEN the preview shows no inbound-reference warning

#### Scenario: Inbound markdown link to a set member detected
- GIVEN a concept outside the purge set holds a markdown link to a
  purge-set member
- WHEN `openkos forget <source-id> --scope source` runs
- THEN the preview lists the referencing concept, the link kind, and the
  targeted member

#### Scenario: Inbound typed relation to a set member detected
- GIVEN a concept outside the purge set holds a typed relation targeting a
  purge-set member
- WHEN `openkos forget <source-id> --scope source` runs
- THEN the preview lists the referencing concept and the relation type

### Requirement: Unverifiable Referrer Detection (Fail-Closed)

`openkos forget` MUST run an independent fail-closed check across the purge
set: a file whose frontmatter/`relations:` fails to parse MUST be surfaced
as an `unverifiable` reference WHEN ANY purge-set member's canonical id
appears as a raw substring of that file's text. A malformed file mentioning
none of the set's ids MUST be ignored.

#### Scenario: Unverifiable referrer mentioning a set member is surfaced
- GIVEN a referrer file whose frontmatter cannot be parsed but whose text
  contains a purge-set member's id
- WHEN `openkos forget <source-id> --scope source` runs
- THEN it is surfaced as an unverifiable reference in the Phase A preview

#### Scenario: Unverifiable referrer not mentioning any set member is ignored
- GIVEN a malformed file that mentions no purge-set member's id
- WHEN `openkos forget <source-id> --scope source` runs
- THEN it is not surfaced and does not block the forget

### Requirement: Refuse Forget When Inbound References Exist, Unless `--force`

`openkos forget` MUST refuse to proceed (Phase A refusal, exits non-zero,
writes nothing) when one or more EXTERNAL inbound references or
unverifiable referrers were detected, UNLESS `--force` is passed. A referrer
whose id is itself a member of the purge set MUST NOT count toward this
refusal (set-difference): an intra-set backlink (e.g. a cascade child's
`## Related` link back to its Source) is expected and MUST NOT block. When
`--force` is passed and external references exist, the forget proceeds;
those references are left dangling.

#### Scenario: Intra-set backlink does not block
- GIVEN a cascade child renders a `## Related` backlink to its Source, both
  in the purge set
- WHEN `openkos forget <source-id> --scope source` runs
- THEN this backlink is excluded from the refusal count and does not block

#### Scenario: External inbound reference still refuses by default
- GIVEN a concept outside the purge set holds a reference to a purge-set
  member
- WHEN `openkos forget <source-id> --scope source` runs without `--force`
- THEN it refuses in Phase A, exits non-zero, and writes nothing

#### Scenario: External unverifiable referrer still refuses by default
- GIVEN an unverifiable external referrer mentioning a purge-set member's id
- WHEN `openkos forget <source-id> --scope source` runs without `--force`
- THEN it refuses in Phase A, exits non-zero, and writes nothing

#### Scenario: `--force` overrides an external refusal
- GIVEN an external inbound reference to a purge-set member was detected
- WHEN `openkos forget <source-id> --scope source --force` runs (subject to
  the confirm gate)
- THEN the cascade proceeds and the external reference is left dangling

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

For EVERY concept in the purge set, when it carries an OUTBOUND `supersedes`
edge to a concept OUTSIDE the purge set, that target concept is no longer
effective-deprecated once the cascade completes and re-enters retrieval.
`openkos forget` MUST disclose this in the Phase A preview, naming the
target and the purge-set member whose edge caused it, before the confirm
gate.

#### Scenario: A cascade member's supersedes edge discloses resurrection
- GIVEN a purge-set member M has an outbound `supersedes` edge to concept Y
  outside the set
- WHEN `openkos forget <source-id> --scope source` runs
- THEN the preview names Y and states that Y re-enters retrieval once the
  cascade completes

#### Scenario: No out-of-set supersedes edge, no disclosure
- GIVEN no purge-set member has an outbound `supersedes` edge to a concept
  outside the set
- WHEN `openkos forget <source-id> --scope source` runs
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

Phase B MUST rewrite `index.md` (removing every purge-set member's entry)
and `log.md` (appending all N tombstone lines) BEFORE deleting any concept
file, so the catalog never references a file that does not exist. The N
concept-file deletions (`fsio.remove_file`) MUST run LAST, in deterministic
sorted order by concept-id. Phase B is NOT required to be transactional; a
failure partway through the N unlinks MAY leave a partial, git-recoverable
result with the catalog already consistent-forward. On such a partial failure
of a cascade (N > 1), the error MUST report how many of the N members were
removed before failing and how many remain, and point to recovery (git or
`openkos lint`), so the operator is not left to reconstruct partial state.

#### Scenario: Catalog updated before any cascade file deletion
- GIVEN a confirmed `--scope source` forget of 3 concepts
- WHEN Phase B writes execute
- THEN `index.md` and `log.md` are fully updated before any of the 3 files
  is deleted

#### Scenario: Partial cascade deletion is git-recoverable
- GIVEN a Phase B run interrupted after 2 of 3 unlinks
- WHEN the bundle is inspected afterward
- THEN `index.md`/`log.md` reflect all 3 removals, one file may remain as a
  benign orphan, the error states how many of the 3 were removed and how many
  remain, and recovery is via `git status`/`git checkout`

### Requirement: Malformed Bundle Handling

`forget` MUST reuse the existing `OSError`/`ValueError` convention for
malformed bundle content (e.g. unparseable frontmatter) and MUST run the
same `require_workspace` gate as other commands.

#### Scenario: Malformed index.md
- GIVEN a bundle whose `index.md` cannot be parsed by the removal
  primitive
- WHEN `openkos forget <concept-id>` runs
- THEN it exits non-zero with a clear error and writes nothing
