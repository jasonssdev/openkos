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
`multi-source-uncovered`, `unbacked-provenance` — these remain visible only
through `openkos status`/`openkos lint`).

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

`openkos next` MUST rank these actionable finding kinds, in this fixed
order, and MUST recommend the command belonging to the highest-ranked kind
with at least one finding: (1) missing or empty vector index, command
`openkos reindex`; (1b) missing on-disk FTS index (`.openkos/fts.db`
absent; absence only — staleness stays the stale-derived-indexes tier's
job), command `openkos reindex` (issue #553); (2) unextracted source
(`extraction_status: failed`), command `openkos ingest <resource>`; (3)
below-source-sensitivity descendant, command `openkos backfill-sensitivity`;
(4) pending exact-title duplicate group, command `openkos duplicates`. A
lower-ranked tier's finding MUST NOT be recommended while a higher-ranked
tier has at least one finding.

#### Scenario: A missing FTS index outranks every content tier

- GIVEN a bundle whose vector index is populated but whose `.openkos/fts.db`
  has never been built, and which also contains a Source with
  `extraction_status: failed`
- WHEN `openkos next` runs
- THEN it recommends `openkos reindex` with a reason naming the missing FTS
  index, and does not mention `openkos ingest`

#### Scenario: A missing vector index wins the reason over a missing FTS index

- GIVEN a bundle with documents where BOTH derived indexes are missing
- WHEN `openkos next` runs
- THEN it recommends `openkos reindex` with the missing-vector-index reason

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

### Requirement: Non-NFC On-Disk Names Are Ranked Last

`openkos next` MUST recommend `openkos normalize-names` when at least one
on-disk name under the bundle is not NFC, and this tier MUST be ranked
LAST — below every other tier, including the duplicate-group tier (#491).

The ranking is not a cost decision but an ordering one: a decomposed name
blocks nothing, is not missing, and is not unsafe, since
`okf.concept_path_for` already resolves an NFC id against a decomposed
file. It is hygiene, and hygiene outranks nothing.

Its signal MUST come from `lint.scan_non_nfc_entries` — the same scan
`lint`'s `non-nfc-name` finding and the `normalize-names` verb consume — so
the recommendation can never disagree with the verb about what is
offending. The signal MUST be memoized on the shared signal holder like
every other, and MUST NOT be read by any other tier.

Because it is ranked last, this tier's walk MUST NOT be performed on any
run where a higher-ranked tier produced a finding. That placement, not a
persistent cache, is what keeps a bundle with real work pending from paying
for it.

#### Scenario: A clean bundle with a decomposed name recommends the verb

- GIVEN a bundle where every higher-ranked tier finds nothing and one
  on-disk name is not NFC
- WHEN `openkos next` runs
- THEN it recommends `openkos normalize-names`

#### Scenario: An earlier tier's finding never pays the non-NFC walk

- GIVEN a bundle containing both an unextracted source and a non-NFC
  on-disk name
- WHEN `openkos next` runs
- THEN it recommends the higher-ranked tier's command and performs no
  non-NFC scan at all

#### Scenario: An all-NFC bundle recommends nothing from this tier

- GIVEN a bundle where every higher-ranked tier finds nothing and every
  on-disk name is already NFC
- WHEN `openkos next` runs
- THEN no action is recommended and `openkos normalize-names` is not
  mentioned

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

### Requirement: A Declined Finding Is Named, Never Silently Dropped

A finding whose own detail yields no runnable command MUST NOT be
recommended: printing a command that cannot be run as printed is worse than
printing none. Tier 2 therefore declines when the Source records no
`resource`, and when the command extracted from the finding's detail is not
exactly `openkos ingest` followed by that Source's own `resource` value.

Each such declination MUST be named in the output, on every path, whether or
not a lower-ranked tier subsequently fires. The declination MUST identify
the document and distinguish which repair it needs — a missing `resource`
versus one that cannot be spelled as a runnable argument — and MUST NOT
reprint the raw `resource` value, which is the very value the declination
established cannot be trusted in generated prose.

#### Scenario: A failed extraction recording no resource is named

- GIVEN a bundle with a present vector index and a Source with
  `extraction_status: failed` and no `resource`
- WHEN `openkos next` runs
- THEN it recommends no `openkos ingest` command, and names the document as
  seen but not recommended because it records no resource

#### Scenario: A failed extraction whose resource is unusable is named

- GIVEN a bundle with a present vector index and a Source with
  `extraction_status: failed` whose `resource` cannot be spelled as a
  runnable argument
- WHEN `openkos next` runs
- THEN it recommends no `openkos ingest` command, names the document as
  seen but not recommended because its resource is not a runnable argument,
  and its output does not contain the raw `resource` value

#### Scenario: A declination is named even when a lower tier fires

- GIVEN a bundle containing both a declined unextracted-source finding and
  an exact-title duplicate group
- WHEN `openkos next` runs
- THEN it recommends `openkos duplicates` and still names the declined
  document

#### Scenario: A runnable finding produces no declination

- GIVEN a bundle with a Source with `extraction_status: failed` and an
  intact `resource`
- WHEN `openkos next` runs
- THEN it recommends that Source's own retry command and names no
  declination

### Requirement: A Partially Read Bundle Is Never Reported as Fully Read

A document that could not be read, or whose frontmatter could not be parsed,
is excluded from the scan entirely and exists only as a skip notice. WHEN
such notices were produced by a walk this run already performed,
`openkos next` MUST name every skipped document by path, on every path,
whether or not a ranked tier fired — an action derived from a knowingly
incomplete document set carries the same caveat as no action at all.

Reporting these notices MUST NOT itself trigger a bundle walk: a run whose
first tier fires without reading documents observed no notices and MUST NOT
claim otherwise, so the cost contract above is unaffected.

#### Scenario: Skipped documents are named when no tier fires

- GIVEN a bundle with a present vector index, no ranked findings, and a
  document whose frontmatter cannot be parsed
- WHEN `openkos next` runs
- THEN it prints the no-runnable-action line and names the skipped document
  by path

#### Scenario: Skipped documents are named when a tier fires

- GIVEN a bundle with a present vector index, a Source with
  `extraction_status: failed` and an intact `resource`, and a document whose
  frontmatter cannot be parsed
- WHEN `openkos next` runs
- THEN it recommends that Source's retry command and also names the skipped
  document by path

#### Scenario: Every skipped document is named, not only the first

- GIVEN a bundle containing more than one unparseable document
- WHEN `openkos next` runs
- THEN every one of them is named by path

#### Scenario: Stopping at tier 1 names no skipped documents

- GIVEN a bundle whose vector index is missing and which also contains an
  unparseable document
- WHEN `openkos next` runs
- THEN it recommends `openkos reindex`, performs zero bundle walks, and
  names no skipped document

### Requirement: No-Runnable-Action Output Never Claims Cleanliness

WHEN none of the four ranked tiers produces a finding, `openkos next` MUST
print a line naming `openkos status` as the place to see the full report,
and MUST NOT state or imply that the bundle is clean, free of issues, or has
nothing needing attention. This output MUST be the same regardless of
whether commandless findings (conformance, dangling,
multi-source-uncovered) exist in the bundle, because `next`'s short-circuit
means it never proves their absence.

Declinations and skip notices are NOT commandless findings and are exempt
from that sameness rule: both name specific documents this run actually
observed, so withholding them to keep the output uniform would trade an
honest report for a tidy one.

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

What this bans is a numeral standing IN PLACE OF items the output never
enumerates — "3 other items pending" over a list of nothing. A count
attached to a full enumeration is not that, and is permitted: tier 4's own
group count describes the finding that fired, and the skip-notice count is
immediately followed by every skipped document named by path. The
distinction is whether the reader can act on what the numeral refers to.

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
