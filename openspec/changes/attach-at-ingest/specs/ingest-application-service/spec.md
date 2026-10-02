# Delta for Ingest Application Service

## ADDED Requirements

### Requirement: Staging Plans Distinguish A New Object From An Attachment

`stage_derived_objects` MUST return, in `StagedDerivedObjects.plans`, plans
that state whether they CREATE a new object or ATTACH to an existing one. An
attach plan MUST carry the existing concept id, the composed revised text,
and the new `version`; a create plan MUST be unchanged. Every attach MUST also
appear as a `StagingDrop` of a distinct kind so the adapter renders its own
wording; an attach MUST NOT increment `lost_in_staging`, because nothing
extracted was lost.

The lookup MUST arrive as a parameter (a mapping of `(type, normalized key)`
to the existing concept's id and decoded text), built by the caller from the
same snapshot reads that feed the drift guard; staging MUST NOT read the
bundle to find a match. A `None` lookup MUST reproduce the pre-attach
behavior exactly.

`ingest_source` MUST register every attach target as a guarded target
(re-validated by the drift guard after the confirm gate, baseline bytes taken
from the read the revised text was composed from) and MUST NOT register it as
a create-only target. It MUST write an attach target atomically, never with
the create-only primitive. `IngestOutcome` MUST report the number of attached
concepts, and the auto-commit message MUST name them separately from created
ones while remaining byte-identical when there are none.

#### Scenario: A null lookup reproduces today's plans

- GIVEN staging called with no attach lookup
- WHEN it stages a candidate whose slug is taken by a foreign source
- THEN it returns a create plan at `<slug>-2` and no attach plan

#### Scenario: An attach plan carries the revision

- GIVEN a lookup holding an existing concept and a matching candidate
- WHEN staging runs
- THEN the plan is an attach to that concept id carrying the composed text
  and `version + 1`, and the drops contain one attach entry

#### Scenario: An attach target is drift-guarded

- GIVEN an attach target that changes on disk between Phase A and the commit
  phase
- WHEN `ingest_source` runs its commit phase
- THEN it raises `DriftDetected` and writes nothing

#### Scenario: The commit message is unchanged when nothing attached

- GIVEN an ingest with only create plans
- WHEN the auto-commit runs
- THEN the commit message equals the pre-attach message byte for byte

#### Scenario: Staging reads no files to find a match

- GIVEN the staging function and an in-memory lookup
- WHEN a match is staged
- THEN no filesystem read other than the existing slug-existence check on
  the unmatched path occurs
