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

The lookup MUST arrive as a parameter (a `find(type, title)` that returns the
ids of existing, non-deprecated concepts of a non-excluded type whose title has
the same normalized key, and a `read(id)` that returns one concept's decoded
text), built by the caller; staging MUST NOT read the bundle itself. The
caller's `read` MUST be memoised and MUST be invoked only after extraction
returns, taking the target's baseline bytes from that same read, so the bytes a
revision is composed from are the bytes the drift guard compares and a concept
edited while the model was extracting is not overwritten. A `None` lookup MUST
reproduce the pre-attach behavior exactly.

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

#### Scenario: The baseline is read after extraction

- GIVEN an attach target edited while the extractor was running
- WHEN the ingest completes
- THEN the edit is preserved in the revised concept
