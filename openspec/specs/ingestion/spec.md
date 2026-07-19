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
candidate(s) from the SAME reply that slugify to an already-seen slug; drop,
create-only, a candidate whose slug already exists on disk; and drop a
candidate whose fields fail the stricter single-line concept-build gate. A
slug MUST be reserved only once its candidate survives every check, so a
dropped candidate never reserves a slug for a later one. Each per-candidate
drop MUST be reported to stderr and MUST drop only that candidate, never the
whole batch.

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

#### Scenario: A candidate whose slug already exists is skipped create-only

- GIVEN a validated candidate whose slug already exists on disk
- WHEN staging derived objects for write
- THEN that candidate is skipped (create-only), the existing file is left
  untouched, and a note is emitted to stderr

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
referencing its originating Source concept, and MUST inherit the Source's
`sensitivity` value verbatim.

#### Scenario: Provenance and sensitivity inherited

- GIVEN a source ingested with a configured `sensitivity` value and
  successful extraction
- WHEN `openkos ingest <path>` completes
- THEN the derived object's frontmatter `provenance` includes a reference
  to the Source concept and its `sensitivity` equals the Source's
  `sensitivity`

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
when older objects for the same source already exist.

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

The generated Source concept's `sensitivity` MUST equal the workspace
config's `default_sensitivity`; no `--sensitivity` flag is offered in this
slice.

#### Scenario: Sensitivity matches config default

- GIVEN a workspace config with `default_sensitivity: private`
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept's `sensitivity` field is `private`
