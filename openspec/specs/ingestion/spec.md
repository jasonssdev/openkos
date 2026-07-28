# Ingestion Specification

## Purpose

`openkos ingest <path>` copies a raw source into the bundle, generates one
conformant OKF Source concept, and attempts LLM-driven extraction of a
bounded list of derived objects — zero up to a hard cap of 5 — each
classified across the 9-type derived-object vocabulary (`Concept`, `Entity`,
`Place`, `Event`, `Procedure`, `Decision`, `Project`, `Person`,
`Organization`). Records provenance OKF-natively, updates the bundle catalog
(`index.md`) and log (`log.md`), and degrades to Source-only behavior with
zero crashes on any LLM failure.

## Non-Goals

Extraction of a bounded list of derived objects across the 9-type
classifiable vocabulary HAS shipped and is specified below. This spec does
NOT define: entity resolution, merge, or cross-source dedup of derived
objects (MVP-2); reclassification, re-typing, or merge of an existing
derived object on re-ingest — re-ingest reconciles per slug (create-only
insert of slug-missing objects, existing files left byte-untouched), but
never re-types or merges what already exists; a typed relationship graph or
inter-object relations (MVP-2); sensitivity high-water-mark across multiple
sources (MVP-2/3); a configurable (per-workspace) cap or cross-document
synthesis; or MVP-2 hybrid retrieval — all deferred to future MVPs per
`knowledge-object-model.md`.

## Requirements

### Requirement: Config Reader

The system MUST provide `read_config`, parsing `openkos.yaml` and returning
at least `model`, `review`, and `default_sensitivity`. It MUST NOT alter
`write_config`'s byte-identical template contract.

#### Scenario: Reads required fields

- GIVEN an initialized workspace with a valid `openkos.yaml`
- WHEN `read_config` runs
- THEN it returns `model`, `review`, and `default_sensitivity` matching the
  file's values

#### Scenario: No workspace config

- GIVEN a directory with no `openkos.yaml`
- WHEN `read_config` runs, directly or via `ingest`
- THEN it reports a clear error and performs no write

### Requirement: Bundle Catalog Append

The system MUST provide a primitive inserting a new entry into
`bundle/index.md` under the correct section, preserving all existing
entries and sections.

#### Scenario: New entry preserves existing catalog

- GIVEN an `index.md` with prior entries
- WHEN the primitive adds an entry for a new Source concept
- THEN the entry appears in the correct section and prior entries are
  unchanged

#### Scenario: Newline in title, slug, or description is rejected

- GIVEN a `title`, `slug`, or `description` value containing a newline
  (`\n` or `\r`)
- WHEN the catalog-append primitive is called with that value
- THEN it raises `ValueError` and `index.md` is left unchanged, preventing
  a source-derived value from forging a new Markdown section header

### Requirement: Bundle Log Append

The system MUST provide a primitive inserting a dated line into
`bundle/log.md`, preserving existing dated sections and entries.

#### Scenario: New dated line preserves existing log

- GIVEN a `log.md` with prior dated entries
- WHEN the primitive adds a line for the current local date
- THEN the line appears under the correct `## YYYY-MM-DD` section (created
  if absent) and prior entries are unchanged

#### Scenario: Newline in entry is rejected

- GIVEN an `entry` value containing a newline (`\n` or `\r`)
- WHEN the log-append primitive is called with that value
- THEN it raises `ValueError` and `log.md` is left unchanged, preventing a
  source-derived value from forging a new dated section header

### Requirement: Non-Exclusive Atomic Write

The system MUST provide an atomic write primitive (temp file + rename) for
updating files that already exist, separate from `write_exclusive`.
`write_exclusive` MUST remain create-only.

#### Scenario: Interrupted write leaves original intact

- GIVEN an existing `index.md` or `log.md` and a write interrupted before
  rename completes
- WHEN the bundle is inspected afterward
- THEN the original content is unchanged and no partial file replaces it

#### Scenario: write_exclusive stays create-only

- GIVEN a file that already exists
- WHEN `write_exclusive` targets that path
- THEN it refuses, unchanged from before this change

#### Scenario: write_exclusive cleans up its own partial file on write failure

- GIVEN `write_exclusive` has already created `path` in create-only ("x")
  mode and the subsequent write to that handle fails
- WHEN the failure occurs
- THEN `write_exclusive` unlinks the partially-written `path` before
  re-raising, so `path` does not exist afterward and a retry does not raise
  `FileExistsError` against its own leftover partial

### Requirement: Ingest Raw Copy and Source Concept Generation

`openkos ingest <path>` MUST copy the raw source into the bundle's raw
storage as an exclusive (create-only) binary write and generate exactly one
OKF Source concept with frontmatter `type`, `title`, `description`,
`resource`, `tags`, `timestamp`, plus OpenKOS-layer `status`, `version`,
`freshness`, `sensitivity`, and `provenance`. In addition, `ingest` MUST
attempt LLM-driven extraction of a **bounded list** of derived objects —
zero up to a hard cap of 5 — each of a type in the 9-type classifiable
vocabulary (`{Concept, Entity, Place, Event, Procedure, Decision, Project,
Person, Organization}`) from the source. WHEN extraction succeeds, for EACH
derived object that passes per-item validation and survives staging, `ingest`
MUST write that derived object IN ADDITION to the Source concept, with
`provenance` pointing to the Source and `sensitivity` inherited from the
Source. WHEN extraction fails, is unavailable, times out, errors, or leaves
no valid surviving object, `ingest` MUST degrade to Source-only behavior —
write only the Source concept, emit an explanatory note to stderr, and exit 0
(no crash). Extraction always runs
regardless of `--auto`; `--auto` only skips the confirmation prompt. WHEN
the source decodes as UTF-8 text, the Source concept's BODY MUST embed that
text verbatim under a labeled section, followed by `# Citations`. WHEN the
source is not valid UTF-8 text, the body MUST instead contain a short,
honest note that the content could not be embedded as text (no crash),
followed by `# Citations`. An empty source MUST render a body distinct from
both the verbatim and undecodable cases. The generated Source concept MUST
pass `check_conformance`. The `description` MUST remain a single line (no
newlines) and MUST state that the raw source's content was embedded
verbatim, and MUST NOT claim extraction or splitting into derived concepts.

#### Scenario: Successful ingest embeds verbatim text

- GIVEN an initialized workspace and a readable UTF-8 text source at
  `<path>`
- WHEN `openkos ingest <path>` completes (confirmed or `--auto`)
- THEN the raw source is copied, one Source concept exists whose body
  contains that source's text verbatim under a labeled section followed by
  `# Citations`, `check_conformance` reports no violations, and
  `index.md`/`log.md` reflect the new entry

#### Scenario: Path does not exist

- GIVEN `<path>` does not exist or is not readable
- WHEN `openkos ingest <path>` runs
- THEN it exits non-zero, writes a clear error to stderr, and no file is
  created or modified

#### Scenario: Already-ingested source is refused, not overwritten

- GIVEN `raw/<name>` or `bundle/sources/<slug>.md` already exists for this
  source
- WHEN `openkos ingest <path>` runs
- THEN it refuses in Phase A, exits non-zero with a clear error, and
  writes nothing

#### Scenario: Successful extraction yields a Concept

- GIVEN a source whose content clearly describes an idea, topic, or
  framework, and a fake LLM backend returning a well-formed structured
  reply of `type: Concept`
- WHEN `openkos ingest <path>` completes
- THEN both the Source concept AND a Concept document are written, the
  Concept's `provenance` references the Source, and `check_conformance`
  reports no violations for either document

#### Scenario: Successful extraction yields an Entity

- GIVEN a source whose content clearly describes a concrete tool, product,
  or artifact that is not a person or organization, and a fake LLM backend
  returning a well-formed structured reply of `type: Entity`
- WHEN `openkos ingest <path>` completes
- THEN both the Source concept AND an Entity document are written, and the
  Entity's `provenance` references the Source

#### Scenario: Multiple distinct objects are all written

- GIVEN a source genuinely about several distinct objects, and a fake LLM
  backend returning a well-formed array of multiple validly-typed objects
  (at or under the cap)
- WHEN `openkos ingest <path>` completes
- THEN the Source concept AND one derived document per surviving object are
  written, each with `provenance` referencing the Source, and
  `check_conformance` reports no violations for any document

#### Scenario: Undecodable source falls back without crashing

- GIVEN a source at `<path>` that is not valid UTF-8 text (e.g. binary)
- WHEN `openkos ingest <path>` completes
- THEN `ingest` does not crash, the raw copy is still made byte-identical,
  and the Source concept's body honestly states the content could not be
  embedded as text, with no false claim of embedded content

#### Scenario: Empty source renders a distinct body

- GIVEN a source at `<path>` that is zero-length
- WHEN `openkos ingest <path>` completes
- THEN the Source concept's body distinctly indicates the source was
  empty, distinguishable from both the verbatim-embed and
  undecodable-fallback cases

### Requirement: Type Classification Prefers Specific Types Over the Entity Fallback

Extraction MUST classify each derived object's type using a closed
vocabulary of `{Concept, Entity, Place, Event, Procedure, Decision, Project,
Person, Organization}`. `Entity` MUST be used only as a fallback when no more
specific type fits; every other type MUST be preferred over `Entity` whenever
the source content clearly matches that type's definition, and `Concept` MUST
be preferred whenever the source content describes an idea, topic, theory,
term, or framework — including one named after a person, organization, or
place. Extraction MUST classify each object by what the source is
fundamentally ABOUT, and MUST NOT enumerate every named entity as a
standalone object: a person, place, or organization merely mentioned or named
in passing is a participant or attribute of a richer object, not an
independent extraction target. Extraction MUST prefer FEWER, RICHER objects
over many shallow ones, so the derived set reflects what the source is
genuinely about rather than every name it contains.

#### Scenario: Entity chosen only when no specific type fits

- GIVEN a fake LLM backend that would only plausibly classify the source's
  content as a concrete artifact rather than an idea, person, place, event,
  procedure, decision, or project
- WHEN extraction runs
- THEN the derived object's `type` is `Entity`, not any more specific type

#### Scenario: Concept preferred when content fits

- GIVEN a fake LLM backend returning a reply describing an idea or
  framework
- WHEN extraction runs
- THEN the derived object's `type` is `Concept`

#### Scenario: Self-narrating decision classifies as Decision

- GIVEN a source that narrates a choice made, carrying its rationale, the
  alternatives considered, and its current status
- WHEN extraction runs
- THEN the derived object's `type` is `Decision`, not `Concept` or `Event`

#### Scenario: Ongoing goal-directed effort classifies as Project

- GIVEN a source fundamentally about an ongoing effort defined by a goal and
  a timespan rather than a single bounded happening
- WHEN extraction runs
- THEN the derived object's `type` is `Project`, not `Event`

#### Scenario: Named entities in passing are not enumerated

- GIVEN a source fundamentally about one bounded happening that names several
  people only in passing (e.g. a meeting transcript listing attendees)
- WHEN extraction runs
- THEN the result contains the richer objects the source is about (e.g. the
  Event and any Decisions reached) and does NOT contain a shallow Person
  object per named attendee

### Requirement: Fail-Closed Validation of Extracted Output

Extraction output MUST be validated before any derived object is written.
Each candidate object in the parsed reply MUST be validated INDEPENDENTLY:
validation MUST reject a candidate whose parsed shape is not the documented
structured shape; whose `type` is outside the 9-value classifiable set
`{Concept, Entity, Place, Event, Procedure, Decision, Project, Person,
Organization}` (`Source` remains the only in-registry type rejected as
non-classifiable); or whose required fields are missing or empty (at minimum
`title` and `description`). A malformed candidate MUST be dropped WITHOUT
discarding the valid candidates in the same reply — validation is per-item,
not all-or-nothing. Extraction MUST yield a bounded list of the surviving
valid objects, or an empty list when none survive; an empty list and "the LLM
proposed nothing" MUST NOT be distinguished at this layer. WHEN no valid
derived object survives, `ingest` MUST NOT write any derived object, MUST
still write the Source concept, MUST emit a note to stderr explaining the
degrade, and MUST exit 0.

#### Scenario: Malformed JSON degrades to Source-only

- GIVEN a fake LLM backend returning a reply that is not valid structured
  output
- WHEN `openkos ingest <path>` runs
- THEN only the Source concept is written, a note appears on stderr, and
  the exit code is 0

#### Scenario: Invalid type degrades to Source-only

- GIVEN a fake LLM backend returning well-formed output whose `type` is
  outside the 9-value classifiable set `{Concept, Entity, Place, Event,
  Procedure, Decision, Project, Person, Organization}` (including `Source`
  itself)
- WHEN `openkos ingest <path>` runs
- THEN only the Source concept is written, a note appears on stderr, and
  the exit code is 0

#### Scenario: Missing title degrades to Source-only

- GIVEN a fake LLM backend returning output with an empty or missing
  `title`
- WHEN `openkos ingest <path>` runs
- THEN only the Source concept is written, a note appears on stderr, and
  the exit code is 0

#### Scenario: Malformed candidate is dropped, valid candidates kept

- GIVEN a fake LLM backend returning an array of several candidates, one of
  which is missing a required field
- WHEN `openkos ingest <path>` runs
- THEN the valid candidates are written as derived objects and the malformed
  one is dropped, without discarding the valid ones

#### Scenario: All candidates invalid degrades to Source-only

- GIVEN a fake LLM backend whose every candidate fails validation
- WHEN `openkos ingest <path>` runs
- THEN only the Source concept is written, a note appears on stderr, and the
  exit code is 0

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

### Requirement: Extraction Degrades Gracefully on LLM Unavailability

WHEN the LLM backend raises an error (unavailable, timeout, or any backend
error) during extraction, `ingest` MUST catch it locally, degrade to
Source-only behavior, emit a note to stderr, and exit 0. Extraction failure
MUST NOT crash or abort the ingest command.

#### Scenario: LLM backend unavailable

- GIVEN a fake LLM backend whose `chat` call raises a backend error
- WHEN `openkos ingest <path>` runs
- THEN only the Source concept is written, a note describing the degrade
  appears on stderr, and the command exits 0

### Requirement: Derived Object Provenance and Sensitivity Inheritance

A successfully validated derived object MUST record `provenance`
referencing its originating Source concept, and MUST inherit the built
Source concept's own resolved `sensitivity` value at creation time — read
from the Source object actually staged in this run, not from
`cfg.default_sensitivity` or any other shared configuration constant. This
inheritance MUST hold even when the Source's resolved `sensitivity` differs
from the configured default (e.g. because of prior propagation or an
explicit override), proving the value is read, not assumed.

#### Scenario: Provenance and sensitivity inherited from the Source's own value

- GIVEN a source ingested with a configured `sensitivity` value and
  successful extraction
- WHEN `openkos ingest <path>` completes
- THEN the derived object's frontmatter `provenance` includes a reference
  to the Source concept and its `sensitivity` equals the Source's own
  `sensitivity`

#### Scenario: Inheritance tracks the Source's resolved value, not the config default

- GIVEN a source ingested where the built Source concept's resolved
  `sensitivity` differs from `cfg.default_sensitivity` (e.g. a non-default
  value was resolved for this run)
- WHEN `openkos ingest <path>` completes
- THEN every derived object's `sensitivity` equals the built Source's own
  resolved value, and would differ from the derived object's value if
  `cfg.default_sensitivity` had been used instead

### Requirement: Review Gate Shows the Source and Every Derived Object Before Write

The confirmation preview MUST show the proposed Source concept AND every
staged derived object (zero or more) before any write occurs. WHEN `--auto`
is passed, the Source concept and all staged derived objects MUST be written
without prompting; the confirmation prompt is skipped, but extraction still
runs beforehand.

#### Scenario: Interactive confirm shows the Source and each derived object

- GIVEN successful extraction of one or more derived objects and an
  interactive TTY without `--auto`
- WHEN `openkos ingest <path>` reaches the confirm gate
- THEN the preview lists the Source concept and every staged derived object,
  and declining aborts with no files written

#### Scenario: `--auto` writes the Source and every derived object without prompting

- GIVEN successful extraction and `--auto`
- WHEN `openkos ingest <path>` runs
- THEN no confirmation prompt appears and the Source concept together with
  every staged derived object is written

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

### Requirement: Derived Object Cataloging and Logging

Each successfully written derived object MUST be cataloged in `index.md`
under the section matching its type (`# Concepts`, `# Entities`, `# Places`,
`# Events`, `# Procedures`, `# Decisions`, `# Projects`, `# People`, or
`# Organizations`), and each write MUST be recorded as a new entry in
`log.md`, alongside the Source concept's own catalog and log entries.

#### Scenario: Catalog and log reflect the Source and each derived object

- GIVEN successful extraction of one or more derived objects and a completed
  ingest
- WHEN `index.md` and `log.md` are inspected
- THEN `index.md` lists the Source under `# Sources` and each derived object
  under the section matching its type, and `log.md` records every write

### Requirement: Embedded Content Is Queryable End-to-End

Given a source has been ingested with its text embedded per the
requirement above, `openkos query "<question>"` MUST be able to retrieve
the resulting Source concept via the existing FTS index when the question
matches the embedded content, the LLM context assembled for the answer
MUST include that embedded content, and the rendered answer MUST cite that
Source concept. No change to `state/fts.py` or `retrieval/answer.py` is
required to satisfy this — embedding alone MUST make the content reachable
by the existing generic body-indexing and body-feeding behavior.

#### Scenario: Query retrieves and cites ingested content

- GIVEN a source ingested via `openkos ingest <path>` whose embedded body
  contains a distinctive phrase
- WHEN `openkos query "<question about that phrase>"` runs
- THEN the answer is not the no-match response, and the Source concept for
  `<path>` appears among the cited concepts

### Requirement: Path Containment

The raw copy destination and the concept slug MUST derive only from the
source's basename (path with directory components stripped) and a
sanitized slug. Directory-traversal or absolute-path segments in `<path>`
MUST NOT influence where the copy or concept document is written.

#### Scenario: Traversal segments are stripped, not followed

- GIVEN a source path containing traversal segments, e.g.
  `../../evil.txt`
- WHEN `openkos ingest ../../evil.txt` runs
- THEN the copied file lands inside the bundle's `raw/` directory (as
  `raw/evil.txt`), and no file is written outside `raw/` or
  `bundle/sources/`

#### Scenario: Empty slug after sanitization is refused

- GIVEN a source filename whose stem sanitizes to an empty slug (e.g. a
  stem made only of non-alphanumeric characters, such as `+++.txt`)
- WHEN `openkos ingest <path>` runs
- THEN it refuses in Phase A with a clear error, exits non-zero, and writes
  nothing (no raw copy, concept document, or catalog change)

### Requirement: OKF-Native Provenance

The system MUST record provenance as a `provenance:` frontmatter list of
raw source paths on the generated Source concept, with no separate
provenance store.

#### Scenario: Provenance recorded in frontmatter

- GIVEN a successful ingest of `<path>`
- WHEN the generated concept's frontmatter is inspected
- THEN `provenance` lists the raw path(s) for that source

### Requirement: Review/Confirm Flow

`ingest` MUST compute the Source concept, raw copy, any staged derived
objects, and index/log changes in memory during Phase A without writing,
present a preview, and perform Phase B writes only after confirmation. Each
Phase B write MUST be individually create-only (`copy_exclusive`,
`write_exclusive`) or atomic (`write_atomic`), and content MUST be written
before the catalog (raw copy, concept document, and each derived object
before `index.md`/`log.md`), so the catalog never references a file that does
not exist. Phase B is NOT required to be
transactional as a whole: there is no rollback across the sequence, and a
failure partway through MAY leave a partial, detectable result recoverable
via git. `--auto` MUST skip the confirmation prompt and proceed directly
to Phase B.
Config `review: false` MUST likewise skip the prompt, the same as
`--auto`. When `review: true` and stdin is not a TTY and `--auto` is not
passed, the system MUST refuse to write rather than default silently —
this intentionally diverges from `init`'s silent-on-non-TTY behavior,
because `ingest` honors "review before save".

#### Scenario: Preview before write

- GIVEN a valid ingest target and interactive confirmation
- WHEN `openkos ingest <path>` runs without `--auto`
- THEN a preview of the raw copy, Source concept, and index/log changes is
  shown before any file is written

#### Scenario: Phase B writes proceed on confirm

- GIVEN a shown preview
- WHEN the user confirms
- THEN the raw copy, concept document, and index/log updates are written
  in order (content before catalog); on success all four land together

#### Scenario: Phase B failure leaves a detectable, recoverable partial result

- GIVEN a shown preview and confirmation
- WHEN a Phase B write past the first one fails
- THEN the command exits non-zero with a clear error and no raw traceback;
  writes already completed are NOT rolled back (no in-process undo); any
  resulting partial (e.g. an uncatalogued concept) is visible via
  `git status` and recoverable via `git checkout`/`git clean`

#### Scenario: --auto skips the prompt

- GIVEN a valid ingest target
- WHEN `openkos ingest <path> --auto` runs
- THEN no confirmation prompt is shown and Phase B writes proceed directly

#### Scenario: review: false skips the prompt like --auto

- GIVEN a workspace config with `review: false` and no `--auto` flag
- WHEN `openkos ingest <path>` runs
- THEN no confirmation prompt is shown and Phase B writes proceed directly

#### Scenario: Non-TTY without --auto refuses to write

- GIVEN `review: true` in config, stdin is not a TTY, and `--auto` is not
  passed
- WHEN `openkos ingest <path>` runs
- THEN it refuses to write, exits non-zero, tells the user to re-run with
  `--auto`, and nothing is written

### Requirement: Default Sensitivity from Config

On a FRESH ingest (no prior `bundle/sources/<slug>.md`), the generated
Source concept's `sensitivity` MUST equal the workspace config's
`default_sensitivity`; no `--sensitivity` flag is offered in this slice.
This is a narrowing of a previously unconditional guarantee, not new
behavior: on a RE-INGEST (`regenerate=True`), the Source's `sensitivity`
MUST instead be resolved as `okf.combine_sensitivity(on_disk_value,
cfg.default_sensitivity)` — the high-water mark of the two — and that
resolved value MUST be both written to `concept_path` and passed as
`stamp_sensitivity` to derived-object staging, so re-ingest can only raise
or preserve a Source's sensitivity, never lower it. The only sanctioned
downgrade path remains `set-sensitivity --allow-downgrade`. The extraction
gate's `workspace_floor` parameter MUST keep tracking `cfg.default_sensitivity`
literally, unrelated to the resolved or on-disk value (`sensitivity-aware-llm`
Requirement 4 is unaffected). An on-disk `sensitivity` value that is
unrecognized or non-string MUST rank as `confidential` under the existing
`_rank` fallback, so resolution fails closed toward the MORE restrictive
level rather than escalating silently; a missing key or a blank/whitespace-only
string instead ranks as `private` -- the config default floor -- per `_rank`'s
existing behavior, never `confidential`. `timestamp`,
`description`, `resource`, `provenance`, and the body MUST continue to
refresh exactly as before this change; only the `sensitivity` field is
carried forward, as a merge into the freshly built metadata, never a
restore of the prior document. WHEN a regenerated Source's resolved
`sensitivity` exceeds `cfg.default_sensitivity`, the re-ingest preview line
for that Source MUST name the preserved level.

#### Scenario: Fresh ingest still stamps the config default

- GIVEN a workspace config with `default_sensitivity: private` and no prior
  `bundle/sources/<slug>.md` for this source
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept's `sensitivity` field is `private`

#### Scenario: Re-ingest preserves an on-disk value raised above the config default

- GIVEN a Source previously raised to `confidential` via `set-sensitivity`,
  and `default_sensitivity: private` in config
- WHEN `openkos ingest <path>` re-ingests that same source (`regenerate=True`)
- THEN the Source's `sensitivity` remains `confidential`, and any derived
  object newly written on that same re-ingest is stamped `confidential`

#### Scenario: Re-ingest raises to a config default above the on-disk value

- GIVEN a Source on disk at `public`, and config `default_sensitivity`
  raised to `confidential`
- WHEN `openkos ingest <path>` re-ingests that same source
- THEN the Source's `sensitivity` is raised to `confidential`

#### Scenario: Re-ingest with equal values is byte-identical to today

- GIVEN a Source on disk whose `sensitivity` already equals
  `cfg.default_sensitivity`
- WHEN `openkos ingest <path>` re-ingests that same source
- THEN the resolved `sensitivity` is unchanged and the Source's write is
  byte-identical to the pre-existing regenerate behavior for that field

#### Scenario: Existing derived objects are untouched by re-ingest regardless of resolved level

- GIVEN a Source with one existing derived object on disk, and a re-ingest
  that resolves the Source's `sensitivity` to a higher level
- WHEN `openkos ingest <path>` completes
- THEN the existing derived object's file, including its `sensitivity`
  field, is left byte-unchanged (create-only reconciliation still applies)

#### Scenario: Missing on-disk sensitivity floors to private

- GIVEN a Source's on-disk `sensitivity` frontmatter key is missing
  entirely, and config `default_sensitivity: private`
- WHEN `openkos ingest <path>` re-ingests that source
- THEN the on-disk value ranks as `private` under `_rank`'s missing-key
  handling, and the resolved `sensitivity` that gets written and staged is
  `private`

#### Scenario: Blank on-disk sensitivity floors to private

- GIVEN a Source's on-disk `sensitivity` frontmatter value is a blank or
  whitespace-only string, and config `default_sensitivity: private`
- WHEN `openkos ingest <path>` re-ingests that source
- THEN the on-disk value ranks as `private` under `_rank`'s blank-string
  handling, and the resolved `sensitivity` that gets written and staged is
  `private`

#### Scenario: Unrecognized or non-string on-disk sensitivity fails closed to confidential

- GIVEN a Source's on-disk `sensitivity` frontmatter value is either
  non-string (e.g. an `int` or `list`) or a string that does not match any
  `SENSITIVITY_ORDER` member
- WHEN `openkos ingest <path>` re-ingests that source
- THEN the resolved `sensitivity` ranks as `confidential` under the
  existing `_rank` fallback, and that value is what gets written and staged

#### Scenario: Extraction gate still reads the workspace default, not the resolved value

- GIVEN a Source whose resolved `sensitivity` differs from
  `cfg.default_sensitivity` after re-ingest resolution
- WHEN extraction's LLM-send gate (`blocks_llm_send`) evaluates whether to
  call the LLM
- THEN it reads `workspace_floor` (`cfg.default_sensitivity`) literally,
  never the resolved or on-disk value

#### Scenario: Preview reports a preserved level

- GIVEN a Source on disk whose `sensitivity` (`confidential`) exceeds
  `cfg.default_sensitivity` (`private`)
- WHEN the re-ingest preview is shown before Phase B writes
- THEN the preview line for the regenerated Source states the resolved
  level (`confidential`) with the trailing clause "preserved from the
  existing Source"

#### Scenario: Preview reports a raised level

- GIVEN a Source on disk whose `sensitivity` (`private`) is below
  `cfg.default_sensitivity` (`confidential`)
- WHEN the re-ingest preview is shown before Phase B writes
- THEN the preview line for the regenerated Source states the resolved
  level (`confidential`) with the trailing clause "raised by the
  workspace default"

#### Scenario: Preview reports an unchanged level

- GIVEN a Source on disk whose `sensitivity` already equals
  `cfg.default_sensitivity`
- WHEN the re-ingest preview is shown before Phase B writes
- THEN the preview line for the regenerated Source states the resolved
  level with the trailing clause "unchanged"

### Requirement: Per-Type Derived-Object Tally Summary

After a successful `openkos ingest <path>` run that writes at least one
derived object, the command MUST print one additional summary line to
STDOUT of the form `extracted {N} objects — {count} {Type}[, {count}
{Type}...]`, where `N` is the total count of derived objects written and
"objects" is pluralized via the existing `_plural` helper (`extracted 1
object — ...` for `N == 1`). Only types with `count > 0` MUST appear; each
type MUST be rendered using its canonical `CLASSIFIABLE_TYPES` string, and
types MUST be ordered by canonical type-registry order, NOT insertion order
or alphabetical order, so identical input always renders the same string.
WHEN zero derived objects are written (Source-only degrade), this line MUST
NOT be emitted. This line is strictly additive: it MUST NOT replace, alter,
or reorder any existing stdout line, and MUST NOT change any exit code.

#### Scenario: Zero derived objects — no tally line

- GIVEN `openkos ingest <path>` completes with zero derived objects written
  (Source-only degrade)
- WHEN the command's stdout is inspected
- THEN no tally line matching `extracted ... objects` appears

#### Scenario: Single object, singular wording

- GIVEN `openkos ingest <path>` completes writing exactly one derived
  object of type `Concept`
- WHEN the command's stdout is inspected
- THEN it contains the line `extracted 1 object — 1 Concept`

#### Scenario: Multiple objects, one type

- GIVEN `openkos ingest <path>` completes writing three derived objects, all
  of type `Entity`
- WHEN the command's stdout is inspected
- THEN it contains the line `extracted 3 objects — 3 Entity`

#### Scenario: Multiple objects, mixed types in canonical order

- GIVEN `openkos ingest <path>` completes writing derived objects of types
  `Person`, `Concept`, and `Event` (in that write/reply order), and the
  canonical registry orders these as `Concept`, `Event`, `Person`
- WHEN the command's stdout is inspected
- THEN the tally line lists counts in canonical registry order (`Concept`,
  then `Event`, then `Person`), regardless of write or reply order

### Requirement: Blocking-Extraction Activity Indicator

While the blocking `extract_concept` LLM call runs during `ingest`, the
system MUST display a live, indeterminate activity indicator (spinner) on
STDERR only. The indicator MUST NOT report a percentage, ETA, or any other
determinate progress signal. On a non-TTY stream (e.g. piped or captured
stdout, such as under `CliRunner`), STDOUT MUST remain byte-clean of any
spinner control characters or partial-line artifacts, and the exit code MUST
be unchanged from before this indicator was added. The indicator MUST be
cleared whether `extract_concept` returns successfully OR raises
`OllamaError`, leaving no leftover partial line on either path.

#### Scenario: Spinner is stderr-only and stdout stays clean

- GIVEN `openkos ingest <path>` running with stdout captured/piped
  (non-TTY)
- WHEN the blocking `extract_concept` call runs
- THEN stdout contains no spinner control characters or partial lines, and
  the command's exit code is unchanged from behavior before this indicator

#### Scenario: Spinner clears on extraction success

- GIVEN `extract_concept` returns successfully
- WHEN the call completes
- THEN the activity indicator is cleared with no leftover partial line

#### Scenario: Spinner clears on OllamaError

- GIVEN `extract_concept` raises `OllamaError`
- WHEN the error is raised
- THEN the activity indicator is cleared with no leftover partial line, and
  `ingest` proceeds to its existing Source-only degrade behavior

### Requirement: Reusable Type-Tally Formatting Helper

The system MUST provide a helper `_format_type_tally(counts: dict[str,
int]) -> str` whose contract depends only on its `dict[str, int]` input
(type-name → count), decoupled from any `ingest`-specific internals (e.g.
`derived_plans`), so other commands MAY reuse it. Given a non-empty dict, it
MUST render `extracted {N} objects — {count} {Type}[, {count} {Type}...]`
per the tally requirement above (pluralization, canonical-registry
ordering, only `count > 0` entries). Given an empty dict, it MUST return an
empty string (`""`), signaling "no line to print" to the caller.

#### Scenario: Empty dict yields empty string

- GIVEN `_format_type_tally({})`
- WHEN the helper is called
- THEN it returns `""`

#### Scenario: Single-entry dict yields singular line

- GIVEN `_format_type_tally({"Concept": 1})`
- WHEN the helper is called
- THEN it returns `"extracted 1 object — 1 Concept"`

#### Scenario: Multi-entry dict is ordered by canonical registry, not insertion order

- GIVEN `_format_type_tally({"Person": 2, "Concept": 1})` (insertion order:
  `Person` before `Concept`), where canonical registry order places
  `Concept` before `Person`
- WHEN the helper is called
- THEN the returned string lists `Concept` before `Person`, regardless of
  the dict's insertion order

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

## OKF §9 Conformance Rules 1-3

The bundle MUST conform to all three rules of OKF §9 (Open Knowledge Format v0.1 schema). Rules 1-2 govern the frontmatter shape of every `.md` file in the bundle; rule 3 governs the fixed structure of reserved files. The conformance check is implemented in `okf.check_conformance`, which walks the bundle once and returns an empty list when all three rules are satisfied.

### Requirement: OKF §9 Conformance — Reserved File Structure (Rule 3)

`check_conformance` MUST enforce OKF §9 rule 3 (reserved-file structure) in
addition to rules 1-2, via an additive walk over `index.md` and `log.md`
files that MUST NOT alter the existing rule 1-2 walk (`_iter_docs`) or its
output. `check_conformance` MUST continue to return `list[str]` violation
messages in the existing `f"{path}: {message}"` shape; rule-3 violations
MUST be appended to the same list as rules 1-2. Rule 3 covers exactly the two
structural checks below; validating an `index.md`'s body shape
(heading/bullet structure per §6) is explicitly OUT OF SCOPE for this
requirement, as is any change to the freshness/orphan lint.

#### Scenario: Reserved-file walk does not perturb rules 1-2

- GIVEN a bundle previously evaluated under rules 1-2 only
- WHEN `check_conformance` runs after rule 3 is added
- THEN the rule 1-2 portion of the violation list is byte-identical to
  before

### Requirement: index.md Frontmatter Conformance (§6 + §11 Root Exception)

For every `index.md` in the bundle tree, `check_conformance` MUST treat a
frontmatter FENCE (opening `---` delimiter with a closing `---`, whether or
not its YAML parses) as a violation UNLESS the file is the bundle-root
`index.md` (`path.parent == bundle_dir`), where §11 permits an `okf_version:
"0.1"` frontmatter block as the sole exception.

#### Scenario: Root index.md with okf_version frontmatter passes

- GIVEN a bundle-root `index.md` containing `okf_version: "0.1"`
  frontmatter
- WHEN `check_conformance` runs
- THEN no violation is reported for that file

#### Scenario: Non-root index.md with frontmatter is a violation

- GIVEN an `index.md` at any depth other than the bundle root, containing a
  frontmatter FENCE (opening and closing `---` delimiters)
- WHEN `check_conformance` runs
- THEN a violation naming that file's path is reported

### Requirement: log.md ISO-8601 Date Heading Conformance (§7)

For every `log.md` in the bundle tree, `check_conformance` MUST treat every
`## ` heading whose text does not match `^\\d{4}-\\d{2}-\\d{2}$` as a
violation.

#### Scenario: Valid ISO date heading passes

- GIVEN a `log.md` whose only `## ` heading is `## 2026-07-14`
- WHEN `check_conformance` runs
- THEN no violation is reported for that heading

#### Scenario: Malformed date heading is a violation

- GIVEN a `log.md` containing a `## ` heading that is not an ISO-8601 date,
  e.g. `## July 2026`
- WHEN `check_conformance` runs
- THEN a violation naming that file's path and the offending heading is
  reported

### Requirement: OKF §9 Conformance — `relations:` Field Shape

`check_conformance` MUST validate the `relations:` frontmatter field when
present on any document: it MUST be a list of mappings, each containing a
non-empty `target` and a non-empty `type` string. A malformed shape (not a
list, an entry missing `target`/`type`, or an entry with an empty value)
MUST be reported as a violation in the existing `f"{path}: {message}"`
shape, appended to the existing rules 1-3 violation list. For any document
without a `relations:` key, the existing rules 1-3 output MUST remain
byte-identical to before this rule was added.

#### Scenario: Malformed relations entry reported as violation

- GIVEN a document whose `relations:` list contains an entry missing
  `target` or `type`
- WHEN `check_conformance` runs
- THEN a violation naming that document's path is appended to the result

#### Scenario: Byte-identical output when relations is absent

- GIVEN a bundle with no document containing a `relations:` key
- WHEN `check_conformance` runs before and after this rule is added
- THEN the violation list is byte-identical

#### Scenario: Well-formed relations passes

- GIVEN a document with a well-formed `relations:` list
- WHEN `check_conformance` runs
- THEN no violation is reported for that document's `relations:` field

### Requirement: Reference Bundle Full §9 Conformance

The reference bundle at `examples/good-life-demo/bundle` MUST pass
`check_conformance` with an empty violation list under all three §9 rules,
asserted by a test that runs in CI's existing `test` job with no CI
configuration changes required.

#### Scenario: Reference bundle passes all three rules

- GIVEN the bundle at `examples/good-life-demo/bundle`
- WHEN `check_conformance` runs against it
- THEN it returns an empty list

### Requirement: Ingest Triggers Candidate-Edge Computation With Graceful Embedder Degradation

`ingest` MUST trigger candidate-edge computation (the graph-projection
third pass) in the SAME run, so a fresh `ingest` shows candidate edges
without a follow-up invocation. Doing so requires `ingest` to hold an
Embedder dependency it does not have today (it currently builds only a
chat client). An unreachable or failing embedder MUST NOT fail the
`ingest` write: `ingest` MUST keep the Source (and any extracted
derived objects), emit an explanatory note to stderr distinguishing
this degrade from the existing concept-extraction-skipped degrade, and
exit 0 — the same non-fatal shape as today's Ollama-unreachable
extraction degrade. Candidate-edge computation MUST NOT block or delay
the Source/derived-object write path on success or failure.

#### Scenario: Successful ingest surfaces candidate edges in the same run

- GIVEN an initialized workspace, a reachable embedder, and a source
  whose content is close in embedding space to an existing concept
- WHEN `openkos ingest <path>` completes
- THEN candidate edges involving the newly ingested concept(s) are
  visible via the graph projection without any further command

#### Scenario: Unreachable embedder degrades without failing the write

- GIVEN an initialized workspace and an embedder that is unreachable or
  raises an error
- WHEN `openkos ingest <path>` runs
- THEN the Source concept (and any successfully extracted derived
  objects) is still written, a note distinguishing the embedder
  degrade from the concept-extraction degrade appears on stderr, and
  the command exits 0

#### Scenario: Missing or empty vector store does not fail ingest

- GIVEN an initialized workspace whose `vectors.db` is absent or empty
  at the time of ingest
- WHEN `openkos ingest <path>` runs
- THEN the ingest write completes normally and exits 0, with zero
  candidate edges produced for this run
