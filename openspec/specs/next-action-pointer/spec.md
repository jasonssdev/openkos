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
`suggest-relations` or `suggest-volatility` (both require a live model
backend and are out of scope); the informational concept-to-concept
edge-count line (`next` never calls `build_graph`); remedies for findings
that name no command (`conformance`, `dangling`, `unbacked-provenance` —
these remain visible only through `openkos status`/`openkos lint`).

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
with at least one finding:

1. an empty bundle (zero eligible documents), command `openkos ingest
   <path>`; WHEN a directory of the bundle could not be read, the bundle
   MUST NOT be claimed empty and the command MUST be `openkos status`;
2. missing or empty vector index, command `openkos reindex`;
3. missing on-disk FTS index (`.openkos/fts.db` absent; absence only —
   staleness belongs to the next tier), command `openkos reindex`;
4. stale derived indexes (`fts.db` or `graph.db` describing an older bundle
   than the one on disk), command `openkos reindex`;
5. unextracted source (`extraction_status: failed`), command `openkos
   ingest <resource>`;
6. derived objects stored without judge selection, command `openkos ingest
   <resource>`;
7. below-source-sensitivity descendant, command `openkos
   backfill-sensitivity`;
8. multi-source-uncovered document, command the finding's own
   `openkos set-sensitivity <id> <level>` remediation;
9. pending exact-title duplicate group, command `openkos curate`;
10. on-disk name that is not NFC, command `openkos normalize-names`;
11. open contradiction finding, command `openkos contradictions`;
12. an open pending-work queue row of a kind no earlier tier ranks
    (`identity` beyond exact-title groups, `relation_type`, `volatility`,
    `revision`, `watch_refusal`), or a most recent unattended job outcome of
    `budget_exhausted`, `timed_out`, `commit_failed`, or `failed`, command
    `openkos pending`.

A lower-ranked tier's finding MUST NOT be recommended while a higher-ranked
tier has at least one finding.

#### Scenario: An empty bundle recommends the first ingest

- GIVEN a bundle with zero eligible documents and an empty vector index
- WHEN `openkos next` runs
- THEN it recommends `openkos ingest <path>` and does not recommend
  `openkos reindex`

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

#### Scenario: A missing vector index outranks unextracted sources

- GIVEN a bundle with documents whose vector index is missing and which also
  contains a Source with `extraction_status: failed`
- WHEN `openkos next` runs
- THEN it recommends `openkos reindex` and does not mention `openkos ingest`

#### Scenario: A stale derived index outranks every content tier

- GIVEN a bundle whose vector and FTS indexes are present but whose FTS
  index describes an older bundle than the one on disk, and which also
  contains a Source with `extraction_status: failed`
- WHEN `openkos next` runs
- THEN it recommends `openkos reindex` with a reason naming the stale index,
  and does not mention `openkos ingest`

#### Scenario: Unextracted sources outrank unjudged extractions

- GIVEN a bundle with fresh indexes, containing a Source with
  `extraction_status: failed` and also a Source whose derived objects lack
  judge selection
- WHEN `openkos next` runs
- THEN it recommends `openkos ingest <resource>` for the failed Source

#### Scenario: Unjudged extractions outrank below-source-sensitivity descendants

- GIVEN a bundle with fresh indexes and no failed extractions, containing a
  Source whose derived objects lack judge selection and also a provenance
  descendant below its Source's sensitivity
- WHEN `openkos next` runs
- THEN it recommends `openkos ingest <resource>` for that Source and does
  not mention `openkos backfill-sensitivity`

#### Scenario: Below-source-sensitivity outranks multi-source-uncovered

- GIVEN a bundle with fresh indexes and no extraction debt, containing a
  provenance descendant below its Source's sensitivity and also a
  multi-source-uncovered document
- WHEN `openkos next` runs
- THEN it recommends `openkos backfill-sensitivity` and does not mention
  `openkos set-sensitivity`

#### Scenario: Multi-source-uncovered outranks duplicate groups

- GIVEN a bundle with fresh indexes and no extraction debt or
  below-source-sensitivity descendants, containing a multi-source-uncovered
  document and also an exact-title duplicate group
- WHEN `openkos next` runs
- THEN it recommends that document's `openkos set-sensitivity` remediation
  and does not mention `openkos curate`

#### Scenario: Duplicate groups outrank non-NFC names

- GIVEN a bundle where every higher-ranked tier finds nothing, containing an
  exact-title duplicate group and also an on-disk name that is not NFC
- WHEN `openkos next` runs
- THEN it recommends `openkos curate` and does not mention `openkos
  normalize-names`

#### Scenario: Non-NFC names outrank open contradictions

- GIVEN a bundle where every higher-ranked tier finds nothing, containing an
  on-disk name that is not NFC and also an open contradiction finding
- WHEN `openkos next` runs
- THEN it recommends `openkos normalize-names` and does not mention
  `openkos contradictions`

#### Scenario: Every tier present, the highest wins

- GIVEN a bundle whose vector index is missing, and which also contains a
  Source with `extraction_status: failed`, a provenance descendant below its
  Source's sensitivity, an exact-title duplicate group, and an open
  contradiction finding
- WHEN `openkos next` runs
- THEN it recommends `openkos reindex` only, mentioning none of `openkos
  ingest`, `openkos backfill-sensitivity`, `openkos curate`, or `openkos
  contradictions`

### Requirement: First-Hit Short-Circuit Cost Contract

`openkos next` MUST stop evaluating tiers at the first one with a finding
and MUST NOT perform work belonging to any lower-ranked tier. This is a cost
contract, asserted by walk count, not a suggestion. The tiers that read the
bundle's documents (empty bundle, unextracted source, unjudged extraction,
below-source-sensitivity, multi-source-uncovered) MUST share a single
memoized document-collection walk — evaluating several of them in one run
MUST NOT trigger a second call. Every other signal (the stale-index
manifest hash, the exact-title duplicate groups, the non-NFC name scan)
MUST be computed only when its own tier is reached. The presence checks for
the vector index and the FTS index MUST perform zero bundle walks. The
empty-bundle tier MUST read documents only when the vector index is empty.
Reaching the duplicate-group tier MUST perform at most three bundle walks
in total, counting the shared document-collection walk and the
duplicate-group scan's own walks.

#### Scenario: Stopping at the missing-FTS tier performs zero bundle walks

- GIVEN a bundle whose vector index is populated and whose `.openkos/fts.db`
  is absent
- WHEN `openkos next` runs
- THEN it recommends `openkos reindex` having performed zero bundle walks

#### Scenario: Document-reading tiers share one walk

- GIVEN a bundle with fresh indexes, no unextracted sources, and a
  provenance descendant below its Source's sensitivity
- WHEN `openkos next` runs
- THEN it recommends `openkos backfill-sensitivity`, and evaluating the
  document-reading tiers together triggers only one call to the shared
  document-collection walk

#### Scenario: A document-tier finding never pays a later tier's walk

- GIVEN a bundle with fresh indexes and a Source with
  `extraction_status: failed`, which also contains an exact-title duplicate
  group and a non-NFC on-disk name
- WHEN `openkos next` runs
- THEN it recommends `openkos ingest <resource>` and computes neither the
  duplicate groups nor the non-NFC scan

### Requirement: Non-NFC On-Disk Names Are Ranked Last

`openkos next` MUST recommend `openkos normalize-names` when at least one
on-disk name under the bundle is not NFC, and this tier MUST be ranked
below every other tier except the open-contradictions tier, including the
duplicate-group tier.

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

For the unextracted-source, unjudged-extraction, and
below-source-sensitivity tiers, `openkos next` MUST print the exact command
string the underlying finding already carries rather than deriving a new
one; for the multi-source-uncovered tier it MUST print the finding's own
structured remediation. It MUST NOT construct a different command for the
same finding than the one the finding itself names. The three index tiers'
command MUST be exactly `openkos reindex`. The duplicate-group tier's
command MUST be exactly `openkos curate`. The non-NFC tier's command MUST
be exactly `openkos normalize-names`. The open-contradictions tier's command
MUST be exactly `openkos contradictions`.

#### Scenario: The unextracted-source tier's printed command matches the finding's own command

- GIVEN a bundle with a Source with `extraction_status: failed` and a known
  `resource`
- WHEN `openkos next` recommends a command for that finding
- THEN the printed command is identical to the retry command the
  unextracted-source finding itself carries

#### Scenario: The below-source-sensitivity tier's printed command matches the finding's own command

- GIVEN a bundle with a provenance descendant below its Source's sensitivity
- WHEN `openkos next` recommends a command for that finding
- THEN the printed command is identical to the remedy command the
  below-source-sensitivity finding itself carries, exactly `openkos
  backfill-sensitivity`

#### Scenario: The multi-source-uncovered tier prints the finding's remediation

- GIVEN a bundle with a multi-source-uncovered document whose finding
  carries a runnable `openkos set-sensitivity` remediation
- WHEN `openkos next` recommends a command for that finding
- THEN the printed command is identical to that remediation

#### Scenario: The index tiers' command is the fixed reindex command

- GIVEN a bundle whose vector index is missing
- WHEN `openkos next` runs
- THEN the printed command is exactly `openkos reindex`

#### Scenario: The duplicate-group tier's command is the fixed curate command

- GIVEN a bundle with an exact-title duplicate group and no higher-ranked
  finding
- WHEN `openkos next` runs
- THEN the printed command is exactly `openkos curate`, and the reason names
  `openkos duplicates` as the way to review the groups first

### Requirement: A Declined Finding Is Named, Never Silently Dropped

A finding whose own detail yields no runnable command MUST NOT be
recommended: printing a command that cannot be run as printed is worse than
printing none. The unextracted-source tier therefore declines when the Source records no
`resource`, and when the command extracted from the finding's detail is not
exactly `openkos ingest` followed by that Source's own `resource` value; the
unjudged-extraction tier declines on the same conditions, and the
multi-source-uncovered tier declines when its finding carries no runnable
remediation.

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
- THEN it recommends `openkos curate` and still names the declined
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

#### Scenario: Stopping before any document is read names no skipped documents

- GIVEN a bundle whose vector index is populated but whose `.openkos/fts.db`
  is absent, and which also contains an unparseable document
- WHEN `openkos next` runs
- THEN it recommends `openkos reindex`, performs zero bundle walks, and
  names no skipped document

### Requirement: No-Runnable-Action Output Never Claims Cleanliness

WHEN no ranked tier produces a finding, `openkos next` MUST
print a line naming `openkos status` as the place to see the full report,
and MUST NOT state or imply that the bundle is clean, free of issues, or has
nothing needing attention. This output MUST be the same regardless of
whether commandless findings (conformance, dangling,
unbacked-provenance) exist in the bundle, because `next`'s short-circuit
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

- GIVEN a bundle where no ranked tier fires and at least one commandless
  finding exists (a §11 conformance violation, a dangling reference, or an
  unbacked-provenance finding)
- WHEN `openkos next` runs
- THEN it still prints the same no-runnable-action line naming `openkos
  status`, and does not claim the bundle is clean
### Requirement: No Count of Unseen Findings

`openkos next` MUST NOT print any numeral representing a count of findings
it did not rank or did not walk far enough to discover, on any path,
including the path that reaches the duplicate-group tier and has already
paid for every walk before it.

What this bans is a numeral standing IN PLACE OF items the output never
enumerates — "3 other items pending" over a list of nothing. A count
attached to a full enumeration is not that, and is permitted: the duplicate-group tier's own
group count describes the finding that fired, and the skip-notice count is
immediately followed by every skipped document named by path. The
distinction is whether the reader can act on what the numeral refers to.

#### Scenario: No count appears when a tier fires

- GIVEN a bundle where a ranked tier produces a finding
- WHEN `openkos next` runs
- THEN its output contains no numeral describing how many other findings
  exist or remain unseen

#### Scenario: No count appears when the duplicate-group tier has already paid every walk

- GIVEN a bundle that reaches the duplicate-group tier (no higher-ranked
  finding exists) and also contains commandless findings not evaluated by
  any tier
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
after every tier ranked above the duplicate-group tier has produced no
finding. It MUST NOT evaluate the duplicate-group check when any of those
tiers has already produced a finding.

#### Scenario: Duplicate-group check does not run when the vector index is missing

- GIVEN a bundle whose vector index is missing and which also contains an
  exact-title duplicate group
- WHEN `openkos next` runs
- THEN the exact-title duplicate-group check does not run

#### Scenario: Duplicate-group check does not run when an extraction tier fires

- GIVEN a bundle with fresh indexes, a Source with
  `extraction_status: failed`, and an exact-title duplicate group
- WHEN `openkos next` runs
- THEN the exact-title duplicate-group check does not run

#### Scenario: Duplicate-group check does not run when a sensitivity tier fires

- GIVEN a bundle with fresh indexes, no unextracted sources, a
  provenance descendant below its Source's sensitivity, and an exact-title
  duplicate group
- WHEN `openkos next` runs
- THEN the exact-title duplicate-group check does not run

#### Scenario: Duplicate-group check runs only when every higher tier is empty

- GIVEN a bundle with fresh indexes, no unextracted or unjudged sources, no
  sensitivity findings, and an exact-title duplicate group
- WHEN `openkos next` runs
- THEN the exact-title duplicate-group check runs and `openkos curate` is
  recommended

### Requirement: Each Recommendation And Declination Names Its Subjects

`NextAction` MUST carry an additive, structured `subjects` field —
`tuple[str, ...] | None`, defaulting to `None` — naming the concept id(s)
the finding is about, derived from the finding's own structured data, never
parsed or inferred from free-text `reason`/`detail` prose. `NextResult` MUST
carry an additive, structured `declination_subjects` field, a tuple
index-aligned one-to-one with `declinations`, holding the same
`tuple[str, ...] | None` shape per entry.

`None` MUST mean the tier's call site has not declared its subjects at all
— an undeclared state that any disclosure gate downstream MUST treat as
withholdable by construction, distinct from a declared, empty tuple. WHEN a
tier's own finding names one or more concepts, `subjects` MUST be populated
with their ids. WHEN a tier's finding does not itself resolve to a specific
concept id (a fixed-text tier naming no document), `subjects` MUST be an
explicit empty tuple `()` — a DECLARED absence of subjects, never the
unset `None` default. Every call site that produces a `NextAction` or
records a declination MUST declare `subjects` explicitly; none MUST rely on
the `None` default to mean "nothing to declare."

For the below-source-sensitivity and multi-source-uncovered tiers,
`subjects` MUST include both the finding's own concept id and every id
named by the finding's `related_ids` (see `LintFinding.related_ids` below),
since those tiers' reason text names more than one concept. For the open
contradictions tier, `subjects` MUST be the finding's own pair of concept
ids. Every other tier's `subjects` MUST be either the sole concept id its
finding is about, or `()` for a tier whose fixed text names no document.

This is purely additive: `openkos next`'s existing human-readable stdout
output MUST remain byte-identical to its output before these fields
existed.

`lint.LintFinding` MUST gain an additive `related_ids: tuple[str, ...]`
field, defaulting to `()` and excluded from equality comparison, populated
for the `below-source-sensitivity` finding with the cited Source's own id
and for the `multi-source-uncovered` finding with every id its detail
cites. No lint output rendering MUST change because of this field.

#### Scenario: A tier-2 recommendation names its Source's concept id

- GIVEN a bundle with a Source with `extraction_status: failed` and an
  intact `resource`
- WHEN `next_action` produces its tier-2 recommendation
- THEN the recommendation's `subjects` field contains that Source's own
  concept id

#### Scenario: A declination names its subject

- GIVEN a bundle with a Source with `extraction_status: failed` and no
  `resource`
- WHEN `next_action` produces the resulting declination
- THEN the matching entry in `declination_subjects` contains that Source's
  concept id, not derived from its free-text detail

#### Scenario: A tier with no resolvable subject declares an explicit empty tuple

- GIVEN a finding whose tier does not resolve to a specific concept id (for
  example a missing-index or duplicate-group tier)
- WHEN `next_action` produces that tier's result
- THEN its `subjects` field is the explicit empty tuple `()`, a declared
  absence, never the unset `None` default

#### Scenario: declination_subjects stays index-aligned with declinations

- GIVEN a bundle producing more than one declination
- WHEN `next_action` produces its result
- THEN `declination_subjects` has exactly as many entries as `declinations`,
  in the same order, one per declination

#### Scenario: A below-source-sensitivity finding's subjects include its related Source

- GIVEN a provenance descendant below its Source's sensitivity, where the
  finding's `related_ids` names that Source's concept id
- WHEN `next_action` produces the tier-3 recommendation
- THEN `subjects` contains both the descendant's own concept id and the
  Source's id from `related_ids`

#### Scenario: A multi-source-uncovered finding's subjects include every cited id

- GIVEN a multi-source-uncovered finding whose detail cites more than one
  Source
- WHEN `next_action` produces that tier's recommendation or declination
- THEN `subjects` contains the finding's own concept id together with
  every id `related_ids` names

#### Scenario: related_ids does not affect LintFinding equality

- GIVEN two `LintFinding` instances equal in every field except
  `related_ids`
- WHEN they are compared for equality
- THEN they compare equal

#### Scenario: openkos next's stdout is byte-identical

- GIVEN an unchanged bundle state, exercised before and after these fields
  exist
- WHEN `openkos next` runs
- THEN its stdout is byte-for-byte identical in both cases

### Requirement: Queue-Backed Tiers Read The Queue Before Recomputing

WHEN the pending-work queue exists and is readable, tiers 9, 11, and 12
MUST take their findings from open queue rows and MUST NOT recompute the
advisor behind them; the declined and kept-distinct exclusions MUST still
apply. WHEN the queue is absent or unreadable, tiers 9 and 11 MUST behave
as they did before the queue existed, and tier 12's queue-row half MUST NOT
fire. Tier 12's job-outcome half reads the unattended job record
independently of the queue and MUST still fire when the most recent job
outcome needs attention. Reading
the queue MUST NOT make a model call, MUST NOT construct a model backend,
and MUST NOT write.

#### Scenario: An open identity row is ranked without a candidate walk

- GIVEN an open `identity` row for an exact-title duplicate group, and no
  higher tier fires
- WHEN `openkos next` runs
- THEN it recommends `openkos curate` without walking the bundle for
  duplicate candidates

#### Scenario: A watch refusal points at pending

- GIVEN one open `watch_refusal` row and no higher tier fires
- WHEN `openkos next` runs
- THEN it recommends `openkos pending` and names the refused Source

#### Scenario: A job outcome fires without a queue

- GIVEN a workspace with no queue table whose most recent unattended job
  outcome is `budget_exhausted`, and no higher tier fires
- WHEN `openkos next` runs
- THEN it recommends `openkos pending` and names the job outcome

#### Scenario: No queue keeps the old behavior

- GIVEN a workspace with no queue table and one persisted open contradiction
  finding
- WHEN `openkos next` runs and no higher tier fires
- THEN it recommends `openkos contradictions` as before
