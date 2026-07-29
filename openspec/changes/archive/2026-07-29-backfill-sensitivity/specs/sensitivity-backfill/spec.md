# Sensitivity Backfill Specification

## Purpose

`openkos backfill-sensitivity` is a dedicated, raise-only, bundle-wide sweep
that closes the sensitivity gap left by bundles or descendants created
before Source-to-descendant propagation existed (#219). It resolves every
`type: Source` concept's provenance descendants and raises each descendant
strictly below its Source, in one preview, one confirmation, one `log.md`
entry, and one commit.

## Non-Goals

This spec does not define: any downgrade path (no `--allow-downgrade`
equivalent — the verb is raise-only by construction); combining sensitivity
across multiple Sources for a multi-source descendant (deferred to
MVP-2/3 per ADR-0009); re-triggering extraction or modifying
`extraction_status`; a per-Source scoping argument (bundle-wide only in
MVP 1 — `set-sensitivity` already covers the single-Source case); a
`--dry-run` flag (the preview shown before confirmation, or declining the
prompt, already serves as the dry run); emitting the
unresolvable-provenance WARNING. `backfill-sensitivity` MUST NOT run
`find_unresolvable_provenance`; every Source cites its raw `resource`, so a
bundle-wide run would emit one WARNING per Source on every invocation,
including the no-op path. That signal is delivered by `lint`'s existing
`dangling` finding.

## Requirements

### Requirement: Bundle-Wide Per-Source Sweep

`openkos backfill-sensitivity` MUST treat every `type: Source` concept in
the bundle as an independent closure root and, for each, MUST resolve its
provenance descendants and compute each descendant's new value via
`okf.combine_sensitivity(existing, source_level)`, staging a write only when
that computation is a strict raise over the descendant's current value.
The command MUST NOT write a Source as its own closure root; a Source that
is a genuine provenance descendant of another Source is raised like any
other descendant, and no `type` filter is applied to the descendant set,
matching `main.py:3389-3404`. A descendant already at or above its Source's
level MUST NOT be staged.

#### Scenario: A descendant below its Source is raised

- GIVEN a Source with `sensitivity: confidential` and a provenance
  descendant with `sensitivity: public`
- WHEN `openkos backfill-sensitivity` runs and is confirmed
- THEN the descendant's `sensitivity` becomes `confidential` and the
  Source's own frontmatter is unchanged

#### Scenario: A descendant already at or above its Source's level is untouched

- GIVEN a Source with `sensitivity: private` and a provenance descendant
  already at `sensitivity: confidential`
- WHEN `openkos backfill-sensitivity` runs
- THEN that descendant's frontmatter is byte-identical to before the run

#### Scenario: A Source with a non-passing `extraction_status` still participates

- GIVEN a Source concept whose `extraction_status` is `failed`,
  `blocked-by-sensitivity`, or `no-concepts-found`, with a descendant below
  its `sensitivity`
- WHEN `openkos backfill-sensitivity` runs
- THEN that Source is treated as a closure root exactly like any other
  Source, and its qualifying descendant is raised

#### Scenario: A Source that is itself a provenance descendant of another Source is raised

- GIVEN a Source concept `B` whose own `provenance` cites a
  higher-sensitivity Source `A`, making `B` a member of `A`'s closure
- WHEN `openkos backfill-sensitivity` runs and is confirmed
- THEN `B`'s `sensitivity` is raised via `combine_sensitivity(B, A)` like any
  other descendant, and `A` is unchanged since it is a closure root, not a
  descendant of any other root

### Requirement: Descendants Outside Every Source's Closure Are Skipped, Never Written

A derived concept that is a member of no single Source's provenance closure
MUST NOT be written by the sweep, matching
`find_provenance_descendants`'s existing per-root closure semantics. A
concept citing two or more ids that all fall inside one Source's closure IS
covered and MUST be raised. A concept in no single-Source closure MUST be
surfaced only through the detection findings defined in the `lint` and
`status` specs, never silently.

#### Scenario: A descendant citing two unrelated Sources is never raised by the sweep

- GIVEN a derived concept whose `provenance` cites two distinct Source ids,
  neither of which lies inside the other's provenance closure, each with a
  `sensitivity` higher than the descendant's own
- WHEN `openkos backfill-sensitivity` runs
- THEN that descendant's frontmatter is unchanged by the run

#### Scenario: A descendant citing two ids inside the same Source's closure is raised

- GIVEN a derived concept whose `provenance` cites two concept ids that are
  both members of the same Source's closure, with the descendant's
  `sensitivity` below that Source's level
- WHEN `openkos backfill-sensitivity` runs and is confirmed
- THEN the descendant's `sensitivity` is raised via
  `combine_sensitivity(existing, source_level)`

### Requirement: One Preview, One Confirmation

Before writing, the command MUST print one bundle-wide preview listing
every staged `(concept_id, current -> new_level)` raise across all Sources
scanned, then apply the standard confirm-gate precedence used by other
mutating verbs: `--auto` skips the prompt, workspace config `review: false`
skips the prompt, an interactive TTY prompts via `typer.confirm`, and a
non-interactive session without `--auto` refuses with a non-zero exit and no
write. The post-write success message MUST repeat the same list of raises.

#### Scenario: Preview lists every staged raise before confirmation

- GIVEN two Sources whose sweeps together stage three descendant raises
- WHEN `openkos backfill-sensitivity` runs on a TTY without `--auto`
- THEN the preview lists all three `(concept_id, current -> new_level)`
  entries before the confirm prompt is shown

#### Scenario: `--auto` skips the prompt only

- GIVEN staged raises exist
- WHEN `openkos backfill-sensitivity --auto` runs
- THEN the raises are written without any confirmation prompt, and the
  success message lists the same raises shown in the preview

#### Scenario: Non-TTY without `--auto` refuses to write

- GIVEN no TTY is attached and `--auto` is absent
- WHEN `openkos backfill-sensitivity` runs with staged raises pending
- THEN the command refuses with a non-zero exit and no concept file is
  written

#### Scenario: Declining the prompt performs no write

- GIVEN an interactive TTY and staged raises pending
- WHEN `openkos backfill-sensitivity` runs and the confirm prompt is
  declined
- THEN no concept file is written and no commit is created

### Requirement: One Log Entry And One Autocommit For The Whole Sweep

A successful run MUST write every staged descendant raise, then append
exactly one dated entry to `bundle/log.md` summarizing the whole sweep, then
create exactly one `_autocommit` covering every changed path. No Source is
written as its own closure root by this command; a Source that is a genuine
provenance descendant of another Source is written like any other staged
descendant.

#### Scenario: A multi-descendant, multi-Source run produces one commit

- GIVEN staged raises spanning descendants of three different Sources
- WHEN `openkos backfill-sensitivity` runs and is confirmed
- THEN exactly one `log.md` entry is appended and exactly one commit covers
  every changed descendant file plus `log.md`

### Requirement: Idempotent No-Op On Re-Run

WHEN a run stages zero raises — either because the bundle is already fully
propagated or because a prior successful run already closed every gap — the
command MUST print an explicit "nothing to backfill"-style message, write no
file, create no commit, and exit 0.

#### Scenario: Immediate re-run after a successful sweep is a no-op

- GIVEN a bundle where `openkos backfill-sensitivity` just completed
  successfully
- WHEN `openkos backfill-sensitivity` runs again immediately
- THEN it prints a "nothing to backfill" message, writes nothing, creates no
  commit, and exits 0

#### Scenario: An already-clean bundle is a no-op on first run

- GIVEN a bundle where every descendant already meets or exceeds its
  Source's `sensitivity`
- WHEN `openkos backfill-sensitivity` runs
- THEN it prints a "nothing to backfill" message and exits 0 with no write

### Requirement: Fail-Closed Partial Write Failure Names Landed Paths

WHEN a write fails partway through Phase B (after one or more descendant
raises already landed, before `log.md` and the autocommit complete), the
command MUST NOT roll back any already-written file, MUST leave the bundle
over-classified rather than under-classified, and MUST report a failure
message that names every path that already landed before the failure.

#### Scenario: A mid-sweep write failure names the paths that already landed

- GIVEN a sweep staging raises across multiple descendants, where the write
  fails after the first two descendant files are written but before the
  third
- WHEN `openkos backfill-sensitivity` runs and the failure occurs
- THEN the command exits non-zero, the first two descendant files remain
  raised on disk, and the failure message names both of their paths
  explicitly
