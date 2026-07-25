# Delta for Ingestion

## MODIFIED Requirements

### Requirement: Bounded, Deduplicated Derived-Object Staging

`ingest` MUST compute the complete set of derived objects to write with zero
writes (Phase A) before Phase B writes any of them. The number of derived
objects written for a single source MUST NOT exceed a hard cap of 5 (a safety
ceiling applied after per-item validation, not a target). During staging, the
system MUST, per candidate in reply order: derive a slug from the candidate's
title and drop a candidate whose title yields an empty slug; apply an
in-batch slug-collision guard that keeps the first and drops later
candidate(s) from the SAME reply that slugify to an already-seen slug; and
drop a candidate whose fields fail the stricter single-line concept-build
gate. WHEN a candidate's slug collides with an existing on-disk concept whose
`provenance` already references THIS source, the candidate MUST be skipped
create-only (unchanged behavior — the existing file is left untouched). WHEN
a candidate's slug collides with an existing on-disk concept whose
`provenance` references a DIFFERENT source (or a slug the candidate's own
source previously won via disambiguation), the candidate MUST NOT be dropped;
instead it MUST be written to the first free numeric-suffixed slug
(`<slug>-2`, then `-3`, ...) with its own single-source `provenance`. A slug
MUST be reserved only once its candidate survives every check, so a dropped
or redirected candidate never reserves a slug for a later one. Each
per-candidate drop or disambiguation MUST be reported to stderr and MUST
affect only that candidate, never the whole batch.
(Previously: any slug collision with an existing on-disk file was silently
dropped, create-only, regardless of which source owned the existing file.)

#### Scenario: More than the cap of validated objects is bounded

- GIVEN a source whose extraction would yield more than 5 valid objects
- WHEN `openkos ingest <path>` completes
- THEN no more than 5 derived objects are written, keeping the first 5 in
  reply order

#### Scenario: Two objects in one reply collide on slug

- GIVEN a validated batch of two objects whose titles slugify to the same
  slug
- WHEN staging derived objects for write
- THEN only the first object in reply order is staged; the second is dropped
  with a note on stderr and not written

#### Scenario: Same-source slug collision is a create-only no-op

- GIVEN a validated candidate whose slug already exists on disk on a concept
  whose `provenance` references this ingest's source
- WHEN staging derived objects for write
- THEN that candidate is skipped (create-only), the existing file is left
  byte-untouched, and a note is emitted to stderr

#### Scenario: First foreign-source collision writes to `<slug>`

- GIVEN no existing concept file at the candidate's slug
- WHEN a first source's candidate is staged
- THEN it is written to `<slug>.md` with single-source `provenance`

#### Scenario: Second, different-source, same-title candidate writes to `<slug>-2`

- GIVEN an existing concept at `<slug>.md` whose `provenance` references a
  DIFFERENT source than the current candidate
- WHEN the current candidate is staged
- THEN it is written to `<slug>-2.md` with its own single-source
  `provenance`, and the existing `<slug>.md` is left untouched

#### Scenario: Third, different-source, same-title candidate writes to `<slug>-3`

- GIVEN `<slug>.md` and `<slug>-2.md` already exist, each owned by a
  different source than the current candidate
- WHEN the current candidate is staged
- THEN it is written to `<slug>-3.md`, the first free numeric suffix

### Requirement: Idempotent Re-Ingest Reconciles Derived Objects Per Slug

WHEN a source is re-ingested, `ingest` MUST reconcile derived objects per
slug rather than all-or-nothing: for each validated candidate, the system
MUST check whether an object with that slug already exists, MUST insert it
only when no such slug exists yet (create-only), and MUST leave any existing
derived object file byte-untouched — no overwrite, no re-typing, no merge.
The slug-existence check for a candidate MUST complete BEFORE any write for
that candidate, so a failed write never leaves a partially-reconciled state.
Re-ingest re-runs extraction, so a genuinely new object CAN be inserted even
when older objects for the same source already exist. Re-ingesting the SAME
source MUST NOT spawn a new disambiguated slug on each run: a slug collision
against a concept already carrying this source's `provenance` — INCLUDING a
disambiguated slug (`<slug>-N`) this source previously won — MUST be
recognized as this source's own object and treated as the create-only no-op
above, not as a foreign-source collision requiring further disambiguation.
(Previously: reconciliation did not distinguish which source owned a
colliding slug, and made no mention of disambiguated `-N` slugs.)

#### Scenario: Re-ingest leaves an existing derived object untouched

- GIVEN a source already ingested with a resulting derived object, possibly
  hand-edited afterward
- WHEN `openkos ingest <path>` is run again for the same source
- THEN the existing derived object file whose slug already exists is left
  byte-unchanged

#### Scenario: Re-ingest inserts a slug-missing object and skips existing ones

- GIVEN a source that already has one derived object on disk, and a re-ingest
  whose extraction yields that same object plus one whose slug is not yet on
  disk
- WHEN `openkos ingest <path>` runs again
- THEN only the object whose slug does not yet exist is written; the existing
  slug is skipped and not rewritten

#### Scenario: Re-ingesting the first source spawns no new file

- GIVEN a source previously ingested and written to `<slug>.md`
- WHEN that same source is re-ingested unchanged
- THEN no new file is written and `<slug>.md` is left byte-unchanged

#### Scenario: Re-ingesting the source that owns `<slug>-2` does not spawn `-3`

- GIVEN a second source previously disambiguated to `<slug>-2.md`
- WHEN that same second source is re-ingested
- THEN `ingest` recognizes `<slug>-2.md` as this source's own object, no new
  file is written, and no `<slug>-3.md` is spawned

#### Scenario: Byte-identical raw re-ingest short-circuits

- GIVEN a source already ingested and re-ingested with byte-identical raw
  content
- WHEN `openkos ingest <path>` runs again
- THEN it short-circuits as today (D2), with no new derived-object files of
  any kind

## ADDED Requirements

### Requirement: Durable Disambiguation Audit Log

WHEN a candidate is written to a disambiguated slug, `ingest` MUST append one
durable log entry via the existing bundle log primitive, recording the
source's slug, the candidate's extracted title, the original colliding slug,
and the chosen disambiguated slug. This entry MUST be surfaced by `openkos
status` alongside other recent activity, with no new persisted ledger file.

#### Scenario: Disambiguating ingest is recorded and surfaced

- GIVEN an ingest that writes a candidate to `<slug>-2` due to a
  foreign-source collision
- WHEN the bundle log is inspected or `openkos status` is run afterward
- THEN an entry naming the source, extracted title, original slug `<slug>`,
  and chosen slug `<slug>-2` is present

### Requirement: Disambiguated Concepts Remain Resolvable

A concept written to a disambiguated slug MUST remain a normal, fully
conformant concept document discoverable by existing entity-resolution and
contradiction-detection flows without any change to those flows: the
disambiguated concept and the concept it collided with MUST both be visible
to `find_candidates`/`adjudicate` as a candidate group, and, once
graph-connected, to contradiction detection.

#### Scenario: Disambiguated pair forms a candidate group

- GIVEN two different sources whose extraction both yield the same title,
  producing `<slug>.md` and `<slug>-2.md`
- WHEN `openkos duplicates` (or `adjudicate`) runs
- THEN it reports a candidate group containing both concepts, rather than "No
  candidates found"
