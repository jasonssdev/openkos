# Delta for Next Action Pointer

## MODIFIED Requirements

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

## ADDED Requirements

### Requirement: Queue-Backed Tiers Read The Queue Before Recomputing

WHEN the pending-work queue exists and is readable, tiers 9, 11, and 12
MUST take their findings from open queue rows and MUST NOT recompute the
advisor behind them; the declined and kept-distinct exclusions MUST still
apply. WHEN the queue is absent or unreadable, tiers 9 and 11 MUST behave
as they did before the queue existed, and tier 12 MUST NOT fire. Reading
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

#### Scenario: No queue keeps the old behavior

- GIVEN a workspace with no queue table and one persisted open contradiction
  finding
- WHEN `openkos next` runs and no higher tier fires
- THEN it recommends `openkos contradictions` as before
