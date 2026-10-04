# Delta for Ingestion

## ADDED Requirements

### Requirement: An Extracted Candidate Attaches To An Existing Same-Type, Same-Key Concept

WHEN `attach_at_ingest` is enabled, `ingest` MUST, per staged candidate and
before the slug-collision path, look for an existing concept of the SAME OKF
`type` whose `title` has the same normalized key as the candidate's
(`resolution.normalize.normalize_key`, the key the exact-title Identity tier
uses). WHEN one or more such concepts exist, the candidate MUST NOT be
written to a `<slug>-N` slug and MUST NOT create a file; it MUST instead be
staged as an ATTACH to one existing concept. The attach target MUST keep its
Concept ID, `type`, `title` and `description`. WHEN several concepts match
(a pre-existing `base`/`-N` family), the target MUST be the un-suffixed id,
else the lowest `-N`.

The match is purely structural: it MUST NOT consult a model, a judge
confidence, an embedding, or an alias. A concept of a different OKF type MUST
NOT match. The OKF types `Event` and `Person` are EXCLUDED: a candidate of
either type MUST take the unchanged slug-collision path. A concept whose
effective status is deprecated MUST NOT be an attach target; WHEN it is the
only same-key match, the candidate MUST take the unchanged slug-collision
path. WHEN any same-key match already lists this ingest's `sources/<slug>`
in its `provenance`, the candidate MUST be skipped create-only and every
matching file left byte-untouched (the unchanged same-source no-op), so a
re-ingest never attaches twice and never bumps `version` twice.

#### Scenario: A foreign-source candidate attaches instead of forking

- GIVEN `concepts/model-context-protocol.md` of type `Concept` with
  `provenance: [sources/a]`
- WHEN source `b` yields a `Concept` titled "Model Context Protocol"
- THEN no `concepts/model-context-protocol-2.md` is written, and
  `concepts/model-context-protocol.md` lists both `sources/a` and
  `sources/b` in `provenance`

#### Scenario: Normalization decides the match, not the slug

- GIVEN an existing `Place` titled "Café Central"
- WHEN a candidate `Place` titled "Cafe central" is staged from another
  source
- THEN it attaches to the existing `Place`

#### Scenario: A different type does not match

- GIVEN an existing `Concept` titled "Atlas" and a candidate `Project`
  titled "Atlas"
- WHEN the candidate is staged
- THEN it is written as a new `projects/atlas.md` and the `Concept` is
  untouched

#### Scenario: Person and Event keep today's behavior

- GIVEN an existing `Person` titled "Ana Ruiz" with `provenance:
  [sources/a]` and a candidate `Person` titled "Ana Ruiz" from source `b`
- WHEN the candidate is staged
- THEN it is written to `people/ana-ruiz-2.md` exactly as before this
  requirement, and the existing file is untouched
- GIVEN the same shape with type `Event`
- WHEN the candidate is staged
- THEN it is likewise written to `events/<slug>-2.md`

#### Scenario: A deprecated concept is never an attach target

- GIVEN a `Concept` titled "Skills" that is effective-deprecated, and no
  other same-key `Concept`
- WHEN a candidate `Concept` titled "Skills" is staged from a new source
- THEN it is written to the first free `<slug>-N`, and the deprecated
  concept is untouched

#### Scenario: A pre-existing family attaches to its canonical member

- GIVEN `concepts/skill.md`, `concepts/skill-2.md` and `concepts/skill-3.md`
  all of type `Concept` with the same key, none citing source `d`
- WHEN source `d` yields a `Concept` titled "Skill"
- THEN `concepts/skill.md` is the attach target and `-2`/`-3` are untouched

#### Scenario: A source that already owns a family member is a no-op

- GIVEN `concepts/skill-2.md` lists `sources/c` in `provenance`, and
  `concepts/skill.md` does not
- WHEN source `c` is re-ingested with a re-extraction yielding "Skill"
- THEN no file is written and no `version` changes

#### Scenario: A cross-source homonym of a non-excluded type does attach

- GIVEN a `Project` titled "Atlas" from source `a`
- WHEN source `b` yields a `Project` titled "Atlas"
- THEN it attaches (the exclusion list is the only guard against homonyms;
  this scenario pins that the list, not a heuristic, decides)

### Requirement: An Attach Revises The Existing Concept Deterministically

An attach MUST produce the revised document without a model call and
without changing any field it is not specified to change. The revised
document MUST: append the Source to `provenance` (existing entries first,
deduplicated, order-preserving) and re-project `sources` from the unioned
`provenance`; set `version` to the existing integer `version` plus one (a
missing or non-integer `version` counts as 1, so the first attach yields
2); union `tags`; recompute `sensitivity` as the high-water mark of the
existing value and the candidate's resolved value (`combine_sensitivity`),
never lowering it; take `freshness` and `generated` together from the
side with the strictly newer generation time; and never import the
candidate's `type_alternative`. It MUST add one `## Related` bullet for the
new Source using the same phrase ingest uses for a new object.

The candidate's body MUST be added as a new section under the heading
`## Update from <Source title> (sources/<slug>)`, placed immediately above
the existing `## Related` section, with the candidate's own headings
demoted two levels. WHEN the candidate's body, compared after whitespace
normalization, is already contained in the existing body, no section MUST be
added, while `provenance`, `sources`, the `## Related` bullet and `version`
MUST still be updated. The attach MUST NOT rewrite or reorder any existing
body text. The result MUST remain a conformant OKF document with the
Concept ID unchanged and no `id` field.

#### Scenario: An attach bumps version and appends provenance

- GIVEN an existing concept with `version: 1` and `provenance:
  [sources/a]`
- WHEN a candidate from source `b` attaches
- THEN the document has `version: 2`, `provenance: [sources/a, sources/b]`,
  `sources` projecting both, and a new `## Update from ... (sources/b)`
  section above `## Related`

#### Scenario: A document without a usable version starts the count at one

- GIVEN an existing concept with no `version` key (or `version: "x"`)
- WHEN a candidate attaches
- THEN the result carries `version: 2`

#### Scenario: Sensitivity is the high-water mark

- GIVEN an existing concept at `public` and a candidate from a Source
  resolved to `confidential`
- WHEN the candidate attaches
- THEN the concept's `sensitivity` is `confidential`
- GIVEN an existing concept at `confidential` and a candidate resolved to
  `public`
- WHEN the candidate attaches
- THEN the concept's `sensitivity` stays `confidential`

#### Scenario: An already-contained body adds no section

- GIVEN a candidate whose body text already appears verbatim in the
  existing body
- WHEN it attaches
- THEN no `## Update` section is added, and `provenance` and `version` are
  still updated

#### Scenario: Identity fields never change

- GIVEN an existing concept with a title, a description and a Concept ID
- WHEN a candidate with a different description attaches
- THEN the Concept ID, `title`, `description` and `type` are byte-identical
  to before

### Requirement: Attach Is Gated By A Config Key And Disclosed

The `openkos.yaml` boolean `attach_at_ingest` MUST gate attach. WHEN it is
`false`, ingest MUST behave exactly as it did before attach existed. A
non-boolean value MUST be refused as an invalid configuration, like every
other boolean key. Every attach MUST be disclosed: `log.md` MUST receive one
entry per attached concept naming the concept, its `type`, the Source, and the
resulting `version`; the ingest outcome MUST report how many concepts were
attached; and the run summary MUST name each attached concept's id, on
stdout for a person's `ingest`, so the revision is visible without reading
`log.md`. An attach MUST be committed in the same auto-commit as the
ingest, and the commit MUST be revertible on its own (it touches only that
ingest's files).

#### Scenario: The key off restores today's behavior

- GIVEN `attach_at_ingest: false` and an existing `Concept` titled "Skill"
  from source `a`
- WHEN source `b` yields a `Concept` titled "Skill"
- THEN `concepts/skill-2.md` is written and `concepts/skill.md` is untouched

#### Scenario: A non-boolean value is refused

- GIVEN `attach_at_ingest: maybe`
- WHEN any command reads the configuration
- THEN it refuses with a message naming the key

#### Scenario: An attach is disclosed in the log and the summary

- GIVEN an ingest that attaches one candidate
- WHEN the run completes
- THEN `log.md` has an `**Attach**` entry for that concept, and the run
  summary names its id and the new `version`

## MODIFIED Requirements

### Requirement: Bounded, Deduplicated Derived-Object Staging

`ingest` MUST compute the complete set of derived objects to write with zero
writes (Phase A) before Phase B writes any of them. The number of derived
objects written for a single source MUST NOT exceed a backstop cap of 20,
applied exactly once, after union construction and judge selection (or after
the judge-failure degrade) — never as a pre-judge truncation. The one
exception is the judge-unavailable degrade, in which the judge could not
rank the candidates: the cap MUST NOT be applied, because a positional cut
on an unranked set discards by arrival order, and the set remains bounded by
the pre-judge candidate ceiling of 24 merged candidates. During
staging, the system MUST, per candidate in reply order: derive a slug from
the candidate's title and drop a candidate whose title yields an empty slug;
apply an in-batch slug-collision guard that keeps the first and drops later
candidate(s) from the SAME reply that slugify to an already-seen slug; and
drop a candidate whose fields fail the stricter single-line concept-build
gate. A candidate that matches an existing concept on OKF type and
normalized title key MUST be handled by "An Extracted Candidate Attaches To
An Existing Same-Type, Same-Key Concept" before the slug-collision rules
below apply; the slug-collision rules apply to every candidate that
requirement does not take (an excluded type, an attach-disabled run, a
deprecated-only match, or no key match). WHEN a candidate's slug collides
with an existing on-disk concept whose `provenance` already references THIS
source, the candidate MUST be skipped create-only (unchanged behavior — the
existing file is left untouched). WHEN a candidate's slug collides with an
existing on-disk concept whose `provenance` references a DIFFERENT source
(or a slug the candidate's own source previously won via disambiguation),
and the candidate was not attached, the candidate MUST NOT be dropped;
instead it MUST be written to the first free numeric-suffixed slug
(`<slug>-2`, then `-3`, ...) with its own single-source `provenance`. A slug
MUST be reserved only once its candidate survives every check, so a dropped
or redirected candidate never reserves a slug for a later one. Each
per-candidate drop, attach or disambiguation MUST be reported to stderr and
MUST affect only that candidate, never the whole batch.

#### Scenario: More than the backstop of validated objects is bounded

- GIVEN a source whose union+judge selection would yield more than 20 valid
  objects
- WHEN `openkos ingest <path>` completes
- THEN no more than 20 derived objects are written

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

#### Scenario: Second, different-source, same-title candidate of an excluded type writes to `<slug>-2`

- GIVEN an existing `Person` at `<slug>.md` whose `provenance` references a
  DIFFERENT source than the current candidate, which is also a `Person`
- WHEN the current candidate is staged
- THEN it is written to `<slug>-2.md` with its own single-source
  `provenance`, and the existing `<slug>.md` is left untouched

#### Scenario: Third, different-source, same-title candidate of an excluded type writes to `<slug>-3`

- GIVEN `<slug>.md` and `<slug>-2.md` already exist as `Person` concepts,
  each owned by a different source than the current `Person` candidate
- WHEN the current candidate is staged
- THEN it is written to `<slug>-3.md`, the first free numeric suffix

#### Scenario: A same-type, same-key candidate is attached, not suffixed

- GIVEN an existing `Concept` at `<slug>.md` whose `provenance` references a
  DIFFERENT source, and attach enabled
- WHEN a `Concept` candidate with the same normalized title is staged
- THEN no `<slug>-2.md` is written and the existing concept is attached to

### Requirement: Re-Extraction Reconciles Derived Objects Per Slug

WHEN a re-ingest DOES run extraction (retryable debt, `--re-extract`, a
legacy origin-key backfill, or a post-`forget` regenerate), `ingest` MUST
reconcile derived objects per slug rather than all-or-nothing: for each
validated candidate, the system MUST check whether an object with that slug
already exists, MUST insert it only when no such slug exists yet
(create-only), and MUST leave any existing derived object file that already
carries THIS source's `provenance` byte-untouched — no overwrite, no
re-typing, no merge. An existing derived object that carries only a
DIFFERENT source's `provenance` is NOT governed by this paragraph when the
candidate attaches to it ("An Extracted Candidate Attaches To An Existing
Same-Type, Same-Key Concept"); it is revised once, adding this source, and
a later re-extraction of this source finds it already carrying this
source's `provenance` and leaves it byte-untouched.
The slug-existence check for a candidate MUST complete BEFORE any write for
that candidate, so a failed write never leaves a partially-reconciled state.
A genuinely new object CAN be inserted even
when older objects for the same source already exist. Re-ingesting the SAME
source MUST NOT spawn a new disambiguated slug on each run: a slug collision
against a concept already carrying this source's `provenance` — INCLUDING a
disambiguated slug (`<slug>-N`) this source previously won — MUST be
recognized as this source's own object and treated as the create-only no-op
above, not as a foreign-source collision requiring further disambiguation.

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

#### Scenario: Re-ingesting a source that attached leaves the revised concept untouched

- GIVEN source `b` attached to `<slug>.md`, which now lists `sources/b` in
  `provenance` with `version: 2`
- WHEN source `b` is re-ingested with `--re-extract`
- THEN `<slug>.md` is byte-unchanged and `version` is still 2

#### Scenario: Byte-identical raw re-ingest short-circuits

- GIVEN a source already ingested and re-ingested with byte-identical raw
  content and a successful previous extraction
- WHEN `openkos ingest <path>` runs again
- THEN it short-circuits (the convergence requirement above), with
  no new derived-object files of any kind

### Requirement: Disambiguated Concepts Remain Resolvable

A concept written to a disambiguated slug MUST remain a normal, fully
conformant concept document discoverable by existing entity-resolution and
contradiction-detection flows without any change to those flows: the
disambiguated concept and the concept it collided with MUST both be visible
to `find_candidates`/`adjudicate` as a candidate group, and, once
graph-connected, to contradiction detection. Disambiguation applies to the
candidates attach does not take (the excluded types `Event` and `Person`,
a run with attach disabled, and a deprecated-only match); other same-type,
same-key candidates are attached and form no pair.

#### Scenario: Disambiguated pair forms a candidate group

- GIVEN two different sources whose extraction both yield a `Person` with
  the same title, producing `<slug>.md` and `<slug>-2.md`
- WHEN `openkos duplicates` (or `adjudicate`) runs
- THEN it reports a candidate group containing both concepts, rather than "No
  candidates found"

#### Scenario: A non-excluded same-key pair is attached and forms no group

- GIVEN two different sources whose extraction both yield a `Concept` with
  the same title and attach enabled
- WHEN `openkos duplicates` runs
- THEN it reports no group for that title, because one concept exists

### Requirement: Derived Object Cataloging and Logging

Each successfully written derived object MUST be cataloged in `index.md`
under the section matching its type (`# Concepts`, `# Entities`, `# Places`,
`# Events`, `# Procedures`, `# Decisions`, `# Projects`, `# People`, or
`# Organizations`), and each write MUST be recorded as a new entry in
`log.md`, alongside the Source concept's own catalog and log entries. An
attach rewrites an existing, already-cataloged concept: it MUST NOT add a
second `index.md` bullet for it, and it MUST be recorded as an `**Attach**`
entry in `log.md` (see "Attach Is Gated By A Config Key And Disclosed").

#### Scenario: Catalog and log reflect the Source and each derived object

- GIVEN successful extraction of one or more derived objects and a completed
  ingest
- WHEN `index.md` and `log.md` are inspected
- THEN `index.md` lists the Source under `# Sources` and each derived object
  under the section matching its type, and `log.md` records every write

#### Scenario: An attach adds a log entry and no duplicate bullet

- GIVEN an ingest that attaches one candidate to an existing concept
- WHEN `index.md` and `log.md` are inspected
- THEN `index.md` still has exactly one bullet for that concept, and
  `log.md` has an `**Attach**` entry for it
