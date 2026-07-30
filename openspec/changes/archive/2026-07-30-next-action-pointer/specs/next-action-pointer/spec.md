# Next Action Pointer Specification

## Purpose

`openkos next` is a read-only, deterministic verb that answers "which single
command should I run next" over the current bundle. It ranks a fixed set of
actionable findings by a pinned priority order and prints exactly one
runnable command with a one-line reason — or, when nothing actionable fires,
one line pointing at `openkos status`. It never ranks findings that name no
command, never asserts the bundle is clean, and never calls a model backend.

## Non-Goals

This spec does not define: any change to `openkos status` (its output, body,
ordering, or spec are untouched); `--json` or any structured output;
non-zero exit on findings (`next` is not a CI gate); a count of unseen or
skipped findings anywhere in its output; recommendations for
`suggest-relations`, `contradictions`, or `suggest-volatility` (all three
require a live model backend and are out of scope); the informational
concept-to-concept edge-count line (`next` never calls `build_graph`);
remedies for findings that name no command (`conformance`, `dangling`,
`multi-source-uncovered` — these remain visible only through
`openkos status`/`openkos lint`).

## Requirements

### Requirement: Workspace Presence Check

`openkos next` MUST refuse to run outside an initialized workspace, using
the same shared workspace-presence check `openkos status` uses, and MUST NOT
produce a raw traceback. On every other workspace state — including a
freshly initialized, empty bundle — `openkos next` MUST exit 0.

#### Scenario: Run outside a workspace

- GIVEN a directory that is not an initialized OpenKOS workspace
- WHEN `openkos next` runs
- THEN it exits non-zero, prints a clear error to stderr, and prints no raw
  traceback

#### Scenario: Every in-workspace state exits 0

- GIVEN an initialized workspace, in any state (empty, healthy, or
  containing any mix of findings)
- WHEN `openkos next` runs
- THEN it exits 0

### Requirement: Read-Only and Human-Readable Only

`openkos next` MUST NOT write, modify, or delete any bundle file, and MUST
produce human-readable text output only; no `--json` or other structured
output mode is offered.

#### Scenario: No mutation on any run

- GIVEN any workspace state (empty, healthy, or with findings across every
  ranked tier)
- WHEN `openkos next` runs
- THEN no file under the workspace is created, modified, or deleted, and no
  `--json` flag is accepted

### Requirement: Pinned Tier Order

`openkos next` MUST rank exactly four actionable finding kinds, in this
fixed order, and MUST recommend the command belonging to the highest-ranked
kind with at least one finding: (1) missing or empty vector index, command
`openkos reindex`; (2) unextracted source (`extraction_status: failed`),
command `openkos ingest <resource>`; (3) below-source-sensitivity
descendant, command `openkos backfill-sensitivity`; (4) pending exact-title
duplicate group, command `openkos duplicates`. A lower-ranked tier's finding
MUST NOT be recommended while a higher-ranked tier has at least one finding.

#### Scenario: Tier 1 outranks tier 2

- GIVEN a bundle whose vector index is missing and which also contains a
  Source with `extraction_status: failed`
- WHEN `openkos next` runs
- THEN it recommends `openkos reindex` and does not mention `openkos ingest`

#### Scenario: Tier 2 outranks tier 3

- GIVEN a bundle with a present vector index, containing a Source with
  `extraction_status: failed` and also a provenance descendant below its
  Source's sensitivity
- WHEN `openkos next` runs
- THEN it recommends `openkos ingest <resource>` for the failed Source and
  does not mention `openkos backfill-sensitivity`

#### Scenario: Tier 3 outranks tier 4

- GIVEN a bundle with a present vector index and no unextracted sources,
  containing a provenance descendant below its Source's sensitivity and also
  an exact-title duplicate group
- WHEN `openkos next` runs
- THEN it recommends `openkos backfill-sensitivity` and does not mention
  `openkos duplicates`

#### Scenario: All four tiers present, tier 1 wins

- GIVEN a bundle whose vector index is missing, and which also contains a
  Source with `extraction_status: failed`, a provenance descendant below its
  Source's sensitivity, and an exact-title duplicate group
- WHEN `openkos next` runs
- THEN it recommends `openkos reindex` only, mentioning none of `openkos
  ingest`, `openkos backfill-sensitivity`, or `openkos duplicates`

### Requirement: First-Hit Short-Circuit Cost Contract

`openkos next` MUST stop evaluating tiers at the first one with a finding
and MUST NOT perform work belonging to any lower-ranked tier. This is a cost
contract, asserted by walk count, not a suggestion: stopping at tier 1 MUST
perform zero bundle walks (only the cheap vector-index presence check).
Stopping at tier 2 or at tier 3 MUST perform exactly one bundle walk, and
tiers 2 and 3 MUST share that single walk — evaluating both from tiers 2 and
3 MUST NOT trigger two separate bundle-walk calls. Reaching tier 4, or
finding no actionable tier at all, MUST perform at most three bundle walks
in total across the whole run.

#### Scenario: Stopping at tier 1 performs zero bundle walks

- GIVEN a bundle whose vector index is missing
- WHEN `openkos next` runs
- THEN it recommends `openkos reindex` having performed zero bundle walks

#### Scenario: Stopping at tier 2 performs exactly one bundle walk

- GIVEN a bundle with a present vector index and a Source with
  `extraction_status: failed`
- WHEN `openkos next` runs
- THEN it recommends `openkos ingest <resource>` having performed exactly
  one bundle walk

#### Scenario: Stopping at tier 3 performs exactly one bundle walk, sharing tier 2's walk

- GIVEN a bundle with a present vector index, no unextracted sources, and a
  provenance descendant below its Source's sensitivity
- WHEN `openkos next` runs
- THEN it recommends `openkos backfill-sensitivity` having performed exactly
  one bundle walk, and evaluating tier 2 and tier 3 together triggers only
  one call to the shared document-collection walk

#### Scenario: Reaching tier 4 performs at most three bundle walks

- GIVEN a bundle with a present vector index, no unextracted sources, no
  below-source-sensitivity descendants, and an exact-title duplicate group
- WHEN `openkos next` runs
- THEN it recommends `openkos duplicates` having performed at most three
  bundle walks in total

### Requirement: Per-Tier Command Reflects the Finding's Own Command

For tiers 2 and 3, `openkos next` MUST print the exact command string the
underlying finding already carries rather than deriving a new one; it MUST
NOT construct a different command for the same finding than the one the
finding's own detail names. Tier 1's command MUST be exactly `openkos
reindex`. Tier 4's command MUST be exactly `openkos duplicates`.

#### Scenario: Tier 2's printed command matches the finding's own command

- GIVEN a bundle with a Source with `extraction_status: failed` and a known
  `resource`
- WHEN `openkos next` recommends a command for that finding
- THEN the printed command is identical to the retry command the
  unextracted-source finding itself carries

#### Scenario: Tier 3's printed command matches the finding's own command

- GIVEN a bundle with a provenance descendant below its Source's sensitivity
- WHEN `openkos next` recommends a command for that finding
- THEN the printed command is identical to the remedy command the
  below-source-sensitivity finding itself carries, exactly `openkos
  backfill-sensitivity`

#### Scenario: Tier 1's command is the fixed reindex command

- GIVEN a bundle whose vector index is missing
- WHEN `openkos next` runs
- THEN the printed command is exactly `openkos reindex`

#### Scenario: Tier 4's command is the fixed duplicates command

- GIVEN a bundle with an exact-title duplicate group and no higher-ranked
  finding
- WHEN `openkos next` runs
- THEN the printed command is exactly `openkos duplicates`

### Requirement: No-Runnable-Action Output Never Claims Cleanliness

WHEN none of the four ranked tiers produces a finding, `openkos next` MUST
print a line naming `openkos status` as the place to see the full report,
and MUST NOT state or imply that the bundle is clean, free of issues, or has
nothing needing attention. This output MUST be the same regardless of
whether commandless findings (conformance, dangling,
multi-source-uncovered) exist in the bundle, because `next`'s short-circuit
means it never proves their absence.

#### Scenario: No ranked tier fires on a truly empty bundle

- GIVEN a freshly initialized workspace with no sources ingested and a
  present, populated vector index
- WHEN `openkos next` runs
- THEN it prints a line naming `openkos status`, and no wording claims the
  bundle is clean or issue-free

#### Scenario: No ranked tier fires despite commandless findings existing

- GIVEN a bundle with a present vector index, no unextracted sources, no
  below-source-sensitivity descendants, no exact-title duplicate groups, and
  at least one commandless finding (a §9 conformance violation, a dangling
  reference, or a multi-source-uncovered descendant)
- WHEN `openkos next` runs
- THEN it still prints the same no-runnable-action line naming `openkos
  status`, and does not claim the bundle is clean

### Requirement: No Count of Unseen Findings

`openkos next` MUST NOT print any numeral representing a count of findings
it did not rank or did not walk far enough to discover, on any path,
including the path that reaches tier 4 and has already paid for every walk.

#### Scenario: No count appears when a tier fires

- GIVEN a bundle where a ranked tier produces a finding
- WHEN `openkos next` runs
- THEN its output contains no numeral describing how many other findings
  exist or remain unseen

#### Scenario: No count appears when tier 4 has already paid every walk

- GIVEN a bundle that reaches tier 4 evaluation (no tier 1-3 finding exists)
  and also contains commandless findings not evaluated by any tier
- WHEN `openkos next` runs
- THEN its output contains no numeral describing how many commandless or
  unranked findings exist, even though every bundle walk has already run

### Requirement: No Model Backend Constructed

`openkos next` MUST NOT construct any model backend on any code path,
regardless of workspace state or which tier fires.

#### Scenario: No model backend on any path

- GIVEN any workspace state, including one where every ranked tier is empty
- WHEN `openkos next` runs
- THEN no model backend of any kind is constructed during the run

### Requirement: Duplicate-Group Check Gated on Higher Tiers

`openkos next` MUST evaluate the exact-title duplicate-group check only
after tiers 1 through 3 have each produced no finding. It MUST NOT evaluate
the duplicate-group check when any of tiers 1, 2, or 3 has already produced
a finding.

#### Scenario: Duplicate-group check does not run when tier 1 fires

- GIVEN a bundle whose vector index is missing and which also contains an
  exact-title duplicate group
- WHEN `openkos next` runs
- THEN the exact-title duplicate-group check does not run

#### Scenario: Duplicate-group check does not run when tier 2 fires

- GIVEN a bundle with a present vector index, a Source with
  `extraction_status: failed`, and an exact-title duplicate group
- WHEN `openkos next` runs
- THEN the exact-title duplicate-group check does not run

#### Scenario: Duplicate-group check does not run when tier 3 fires

- GIVEN a bundle with a present vector index, no unextracted sources, a
  provenance descendant below its Source's sensitivity, and an exact-title
  duplicate group
- WHEN `openkos next` runs
- THEN the exact-title duplicate-group check does not run

#### Scenario: Duplicate-group check runs only when tiers 1-3 are all empty

- GIVEN a bundle with a present vector index, no unextracted sources, no
  below-source-sensitivity descendants, and an exact-title duplicate group
- WHEN `openkos next` runs
- THEN the exact-title duplicate-group check runs and `openkos duplicates`
  is recommended
