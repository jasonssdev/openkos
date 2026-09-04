# Ingestion Specification

## Purpose

`openkos ingest <path>` is the CLI entry point for ingesting a raw source:
it gates the workspace, reads the configuration, builds the LLM client,
and performs every snapshot read, then delegates to the ingest
application service, which stages a bounded list of derived objects —
zero up to a post-judge backstop cap of 12, each classified across the
9-type derived-object vocabulary (`Concept`, `Entity`, `Place`, `Event`,
`Procedure`, `Decision`, `Project`, `Person`, `Organization`) — alongside
the generated Source concept. `ingest` itself owns argument parsing,
workspace and client setup, the confirmation gate, rendering the
extraction notices and derived-object preview, catalog (`index.md`) and
log (`log.md`) writes via the shared write helpers, and degrading to
Source-only behavior with zero crashes on any LLM failure.

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
zero up to a post-judge backstop cap of 12 — each of a type in the 9-type
classifiable
vocabulary (`{Concept, Entity, Place, Event, Procedure, Decision, Project,
Person, Organization}`) from the source. WHEN extraction succeeds, for EACH
derived object that passes per-item validation and survives staging, `ingest`
MUST write that derived object IN ADDITION to the Source concept, with
`provenance` pointing to the Source and `sensitivity` inherited from the
Source. WHEN extraction fails, is unavailable, times out, errors, or leaves
no valid surviving object, `ingest` MUST degrade to Source-only behavior —
write only the Source concept, emit an explanatory note to stderr, and exit 0
(no crash). Extraction always runs regardless of `--auto`; `--auto` only
skips the confirmation prompt. WHEN the source decodes as UTF-8 text, the
Source concept's BODY MUST embed that text verbatim under a labeled section,
followed by `# Citations`. WHEN the source is not valid UTF-8 text, the body
MUST instead contain a short, honest note that the content could not be
embedded as text (no crash), followed by `# Citations`. An empty source MUST
render a body distinct from both the verbatim and undecodable cases. The
generated Source concept MUST pass `check_conformance`. The `description`
MUST remain a single line (no newlines) and MUST state that the raw source's
content was embedded verbatim, and MUST NOT claim extraction or splitting
into derived concepts.

`ingest` MUST derive `title` from the decoded raw content, in this
precedence, and MUST use the same derived value for the frontmatter `title`,
the Source document's own `# ` heading line, the `index.md` bullet label,
and the `log.md` entry label:

1. The first ATX H1 (`# ` line) that is not inside a fenced code block.
2. Otherwise, only the first non-blank body line is considered, and only
   when it is **title-plausible**; derivation does NOT scan further lines
   looking for one that qualifies.
3. Otherwise, `_titleize(src.stem)` (today's behavior), unchanged.

A candidate from (1) or (2) MUST be normalized — strip surrounding
whitespace, collapse internal whitespace runs to one space, strip a trailing
ATX closing `#` sequence — then validated; any validation failure falls back
to (3).

A line is **title-plausible** only when ALL hold: non-empty after strip;
followed by a blank line or end-of-file; at most 120 characters; does not
end in `.`, `,`, `;`, or `:`; does not begin with markdown block syntax
(`-`, `*`, `>`, `#`, a table pipe, or a code fence).

A normalized candidate MUST be rejected (falling back to (3)) when it
contains any ASCII control character, `\n`, `\r`, `[`, `]`, `(`, `)`, a
backtick, `*`, `_`, `<`, `>`, `|`, or exceeds 120 characters.

It MUST also be rejected when it contains a Unicode invisible or
direction-altering character: the Arabic letter mark `U+061C`, the
zero-width and directional marks `U+200B`-`U+200F`, the bidirectional
embedding and override controls `U+202A`-`U+202E`, the bidirectional
isolates `U+2066`-`U+2069`, the line and paragraph separators
`U+2028`-`U+2029`, the byte-order mark `U+FEFF`, any character in the
Unicode Tag block `U+E0000`-`U+E007F`, or any character in the Variation
Selectors Supplement `U+E0100`-`U+E01EF`. These reach the terminal and the
markdown link labels unescaped; `U+202E` in particular can visually
reorder the text that follows it, and both the Tag block and the Variation
Selectors Supplement can carry an invisible payload into the extraction
prompt alongside text that renders as clean.

The Variation Selectors in the BMP, `U+FE00`-`U+FE0F`, MUST NOT be
rejected, even though they are invisible and share the Variation Selectors
Supplement's Unicode general category. `U+FE0F` is a component of ordinary
emoji presentation sequences, so rejecting the range would send any title
containing a common emoji back to the filename fallback (3).

Ordinary non-ASCII text MUST NOT be rejected. Accented letters, CJK
characters, emoji and typographic dashes are all valid title content.

A leading `---` line MUST be skipped as frontmatter only when a closing
`---` line exists later in the file; otherwise it is ordinary content.
WHEN `raw_content` could not be decoded as UTF-8, or is blank or
whitespace-only, title derivation MUST NOT run and `title` MUST be (3).

`slug` remains derived from the filename only and is unaffected by this
requirement; the Source document's filename and concept id do not change.
This requirement does NOT read a source's own YAML `title:` field, does NOT
recognize setext headings, and does NOT backfill already-ingested Sources.

(Previously: `title` was always `_titleize(src.stem)`, with no content-derived
candidate or fallback chain.)

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
  source — i.e. the existing raw copy is owned by a Source whose recorded
  `origin_key` equals this candidate's, or (for a Source predating that
  key) holds byte-identical content
- WHEN `openkos ingest <path>` runs, with content differing from the
  existing copy
- THEN it refuses in Phase A, exits non-zero with a clear error, and
  writes nothing

> "**for this source**" is decided by ORIGIN, never by basename alone
> (#552). A raw copy that merely SHARES a basename with this candidate,
> while belonging to a different file, is not "this source" and MUST NOT
> trigger this refusal — see the disambiguation requirement below.

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

#### Scenario: First ATX H1 becomes the title

- GIVEN a source whose first non-fenced `# ` line reads `# Introduction to
  Stoicism`
- WHEN `openkos ingest <path>` completes
- THEN the Source's frontmatter `title`, its own `# ` heading line, its
  `index.md` bullet, and its `log.md` entry all read `Introduction to
  Stoicism`

#### Scenario: An H1 inside a fenced code block is ignored

- GIVEN a source whose first `# ` line appears inside a fenced code block,
  followed later by a real `# Chapter One` heading outside any fence
- WHEN `openkos ingest <path>` completes
- THEN the title is `Chapter One`, not the fenced line's text

#### Scenario: No H1, a title-plausible first line is used

- GIVEN a source with no `# ` heading anywhere, whose first line is `Call
  with Maria Salazar — 2026-07-14` followed by a blank line
- WHEN `openkos ingest <path>` completes
- THEN the title is `Call with Maria Salazar — 2026-07-14`

#### Scenario: Wrapped prose first line is not title-plausible

- GIVEN a source with no `# ` heading, whose first line is the start of a
  wrapped prose paragraph with no blank line immediately after it
- WHEN `openkos ingest <path>` completes
- THEN the title falls back to `_titleize(src.stem)`

#### Scenario: A candidate carrying a forbidden character falls back

- GIVEN a candidate title (from an H1 or a title-plausible line) that,
  after normalization and balanced-span stripping, contains an unbalanced
  `[`, `]`, `(`, `)`, a backtick, or another forbidden character
- WHEN `openkos ingest <path>` completes
- THEN the title falls back to `_titleize(src.stem)`

#### Scenario: A balanced parenthetical span is stripped, not fatal (#592)

- GIVEN a candidate title whose only forbidden characters form balanced
  `(...)` or `[...]` spans (e.g. `MCP (Model Context Protocol)`)
- WHEN `openkos ingest <path>` completes
- THEN the title is the candidate with those spans removed and whitespace
  re-collapsed (`MCP`), never the filename fallback
- AND a candidate that is NOTHING BUT a span strips to empty and falls
  back exactly like an empty heading

#### Scenario: A candidate over 120 characters falls back

- GIVEN a candidate title that, after normalization, exceeds 120 characters
- WHEN `openkos ingest <path>` completes
- THEN the title falls back to `_titleize(src.stem)`, with no truncation

#### Scenario: A well-formed leading frontmatter block is skipped

- GIVEN a source starting with `---`, a YAML block, and a closing `---`
  line, followed by a real `# Chapter One` heading
- WHEN `openkos ingest <path>` completes
- THEN the title is `Chapter One`; the frontmatter's own `title:` key, if
  present, is not read

#### Scenario: An unclosed leading `---` is treated as content

- GIVEN a source starting with a `---` line with no later closing `---`
  anywhere in the file
- WHEN `openkos ingest <path>` completes
- THEN the `---` line is evaluated as an ordinary candidate line, fails the
  title-plausible predicate (begins with markdown block syntax), and the
  title falls back to `_titleize(src.stem)`

#### Scenario: A binary source uses the slug title

- GIVEN a source whose bytes do not decode as UTF-8
- WHEN `openkos ingest <path>` completes
- THEN title derivation does not run and the title is
  `_titleize(src.stem)`

#### Scenario: An empty source uses the slug title

- GIVEN a source at `<path>` that is zero-length or whitespace-only
- WHEN `openkos ingest <path>` completes
- THEN title derivation does not run and the title is
  `_titleize(src.stem)`

### Requirement: Idempotent Title Derivation

Title derivation MUST be a pure function of the raw bytes: re-ingesting a
byte-identical raw file MUST produce a byte-identical Source document,
including its derived `title`.

#### Scenario: Byte-identical re-ingest yields a byte-identical Source

- GIVEN a source previously ingested, whose raw bytes are unchanged
- WHEN that same source is ingested again (e.g. after a revert and re-run)
- THEN the resulting Source document, including its derived `title`, is
  byte-identical to the one produced by the original ingest

### Requirement: Type Classification Prefers Specific Types Over the Entity Fallback

Extraction MUST classify each derived object's type using a closed
vocabulary of `{Concept, Entity, Place, Event, Procedure, Decision, Project,
Person, Organization}`. `Entity` is a fallback only, used when no more
specific type fits; `Concept` MUST be preferred whenever content describes
an idea, topic, theory, or framework — including one named after a person,
organization, or place. The rubric MUST apply PER CANDIDATE OBJECT, not per
source: the model MUST first identify the candidates a source contains, then
classify EACH independently — never answer "what is this document about" as
one question with one answer. A person, place, or organization merely
mentioned in passing is an attribute of a richer object, not an independent
target; extraction MUST prefer FEWER, RICHER objects over many shallow ones.

Extraction MUST decide MULTIPLICITY per source via a stated test: a source
developing several distinct subjects (a person, an idea, a choice) MUST
yield one object per subject; a source developing one subject MUST still
yield exactly one. A candidate whose title and scope merely restate its
Source's own title and scope (a "twin") MUST NOT be produced ALONGSIDE
another genuine candidate: when a source develops more than one distinct
subject, the twin is dropped and the genuine subjects are kept. A source
whose ONE genuine subject IS what its own title already names is not
redundant with anything and still yields that subject — the unconditional
form of this rule is unsatisfiable together with the floor below, since a
single-subject source's only object would then have to be suppressed. The
rule is enforced deterministically, after per-item validation
(`_drop_source_title_twins`), not by prompt wording alone: prompt wording
could not carry the unconditional rule at the 8B tier, and a clause naming
a concrete forbidden title measurably worsened the defect (priming).

A `Procedure` MUST NOT be treated as a twin, whatever its title. Extraction
already instructs the model to choose `Procedure` when an instructional
source teaches a repeatable how-to, and for a tutorial the title IS the
procedure — so a title-equality test collided with that instruction across
the whole class of instructional documents, and collided in the wrong
direction: a source yielding only its `Procedure` kept it via the floor
below, while a source ALSO yielding genuine secondary subjects lost the
primary object precisely because it was richer. The exemption keys on the
object's ROLE, not on its body or its title: the Source is the
bibliographic anchor, the `Procedure` is the how-to a reader retrieves.
Every other type is unaffected — a content-free echo of the source title
alongside genuine objects MUST still be dropped, including when the object
sharing its title is an exempt `Procedure`.

The floor is unchanged: genuine, intelligible content MUST yield AT LEAST
ONE object; blank, boilerplate-only, or unintelligible content MUST still
yield `[]`.

#### Scenario: Entity chosen only when no specific type fits

- GIVEN a fake backend that only plausibly fits a concrete artifact
- WHEN extraction runs
- THEN the object's `type` is `Entity`, not any more specific type

#### Scenario: Concept preferred when content fits

- GIVEN a fake backend describing an idea or framework
- WHEN extraction runs
- THEN the object's `type` is `Concept`

#### Scenario: Self-narrating decision classifies as Decision

- GIVEN a source narrating a choice, its rationale, alternatives, and status
- WHEN extraction runs
- THEN the object's `type` is `Decision`, not `Concept` or `Event`

#### Scenario: Ongoing goal-directed effort classifies as Project

- GIVEN a source about an ongoing, goal-directed effort, not one happening
- WHEN extraction runs
- THEN the object's `type` is `Project`, not `Event`

#### Scenario: Named entities in passing are not enumerated

- GIVEN a source about one happening that names attendees only in passing
- WHEN extraction runs
- THEN the result keeps the richer objects and adds no per-attendee Person

#### Scenario: Multi-topic source yields one object per distinct subject

- GIVEN `examples/good-life-demo/raw/call-with-maria-2026-07-14.txt`,
  which discusses a person, a philosophical correction, and a choice made
- WHEN `openkos ingest` completes
- THEN three objects are written: `Person` (`people/maria-salazar.md`) and
  two `Concept` objects, one for the philosophical correction (typically
  titled "Apatheia") and one for the choice made (typically titled
  "Dichotomy of Control")

Note: the reference bundle also declares a `Decision`
(`decisions/frame-the-essay-on-the-dichotomy-of-control.md`) for the choice
made. Three targeted prompt wordings over roughly 28 samples produced zero
`Decision` objects with the default model, which consistently renders that
choice as `Concept: Dichotomy of Control` instead — an 8B-tier limit,
tracked separately as model/fixture work (proposal assumption 4), not
required by this scenario.

#### Scenario: Single-topic source still yields exactly one object

- GIVEN a source developing one subject only
- WHEN extraction runs
- THEN exactly one derived object is written

#### Scenario: Single-subject source keeps the object its title already names

- GIVEN a source with exactly one genuine subject, and that subject is what
  the source's own title already names
- WHEN extraction runs
- THEN that one object is still written — the anti-twin rule does not
  suppress a source's only genuine subject

#### Scenario: A twin object is not produced alongside a genuine candidate

- GIVEN a candidate whose title/scope merely restate the Source's own,
  alongside at least one other candidate that is a genuine, distinct
  subject
- WHEN extraction runs
- THEN the twin candidate is absent from the written derived objects and
  the genuine candidate(s) are kept

#### Scenario: A tutorial's primary Procedure survives its own secondary subjects

- GIVEN an instructional source whose primary `Procedure` is titled the way
  the document titles itself, alongside the genuine secondary subjects the
  same source yields
- WHEN extraction runs
- THEN the `Procedure` is written together with those secondary subjects —
  the anti-twin rule never deletes it

#### Scenario: A non-Procedure echo is still dropped beside an exempt Procedure

- GIVEN a candidate of any other type whose title merely restates the
  Source's own, alongside both an exempt `Procedure` sharing that title and
  a genuine, distinct subject
- WHEN extraction runs
- THEN the echo is absent from the written derived objects, and both the
  `Procedure` and the genuine subject are kept

#### Scenario: Blank or unintelligible content still yields no objects

- GIVEN a source that is blank, boilerplate-only, or unintelligible
- WHEN extraction runs
- THEN zero objects are written and `ingest` degrades to Source-only

### Requirement: Bounded Re-Ask When the Only Object Restates the Source Title

When a source's FINAL, filtered object list is exactly ONE object AND that
object restates the TOPIC the source title names — its normalized title
equals the source title, or one title's meaningful tokens are contained in
the other's, WHATEVER its type — extraction MUST ask the model once more
and MUST ADD whatever that second ask returns to the object already
produced. The trigger MUST be evaluated on the final
merged, filtered list of the whole extraction (both the single-run and the
union+judge path, chunked or not), never per run or per chunk: "the source
returned exactly one object" is not a statement any slice can make.

The re-ask MUST carry an instruction DIFFERENT from the one that produced
the collapsed list — a second identical ask is measurably useless, since
the union path already runs two identical passes and the collapse survives
10 of 10 runs. It MUST use a SEPARATE prompt; the extraction prompt itself
MUST NOT be modified, because the collapse probe pins its identity and a
change there destroys the before/after baseline.

The re-ask MUST only ADD. It MUST NOT remove, replace, or rewrite the
object the first pass produced, whatever it returns: a guard that replaces
the single object is unbounded on a genuinely single-subject source, while
one that only adds is bounded whatever its false-positive rate. A re-ask
that returns nothing MUST leave the extracted objects exactly as they were.

The re-ask prompt MUST make an EMPTY answer explicitly correct and expected
for a source that genuinely covers one subject. A genuinely single-subject
source triggers this re-ask too, and an instruction that pressures the
model to produce more is how such a source acquires an invented subject.

The extra call MUST be reported: extraction MUST carry both the fact that a
re-ask was spent and the titles it contributed, and `ingest` MUST surface
them, with wording distinct from the cap, judge, and pre-judge notices.

BOTH of the re-ask's decisions — whether to fire, and whether a returned
candidate is an answer — MUST ignore the `Procedure` exemption that governs
the DROP rule, and MUST therefore discard any returned candidate whose
title restates the source title, of ANY type. That exemption exists to
prevent a DELETION of a PRIMARY object the first pass found — dropping a
rich tutorial's primary how-to was silent data loss — and neither additive
decision can delete anything, so its rationale does not transfer.
Admitting a same-titled addition would also contradict the re-ask
instruction itself, which forbids restating the kept subject under another
name or another type, and would surface two objects sharing one title
whenever their types differ.

Measured (`qwen3:8b`, `--runs 5 --seed 7`): the `lesson` treatment arm
returns one object in 5 of 5 runs and it is a `Procedure` every time, so a
type-aware trigger never fires on the fixture that reproduces this defect.

Containment MUST be token-level, never raw substring: `Rust` is not
contained in `Trust Boundaries`, and only token equality gets that right.
Tokens too short to carry topic signal MUST be dropped first, and the
contained title MUST retain at least two of them — a single generic token
contained in anything is the failure `resolution/similarity.py` records
(#555), where one manufactured single-token title landed in eleven
duplicate groups. Partial overlap is NOT containment: a title sharing one
token with the source title while naming its own subject MUST NOT fire.

The predicate MUST also recognise ACRONYM/EXPANSION as the same topic
(#586): one title's token being the initials of a contiguous run of words
in the other, in EITHER direction, so `Model Context Protocol` under a
source titled `MCP` is recognised despite sharing no token with it. The
initialism MUST carry at least three letters — two-letter initialisms are
far too common to carry identity — and MUST abbreviate at least two words,
or every title sharing a first letter would match.

This recognition MUST live in the ADDITIVE predicate only. It MUST NOT
reach the drop rule: an object named after its source's expansion is the
model writing the fuller name, and deleting it for that is the #413
mistake. It also MUST NOT be presented as settling entity IDENTITY, which
`resolution/similarity.py`'s acronym tier (#397) already decides and routes
through `adjudicate`/`merge`.

The title comparison shared with chunk-merge dedup MUST stay exact.
Containment and acronym matching belong to the additive predicate alone;
folding either into that comparison would merge distinct subjects across
chunks — `MCP` and `Model Context Protocol` becoming one object.

The DROP rule's exemption is UNCHANGED: a `Procedure` restating the source
title MUST still never be dropped. The type exemption MUST live in exactly
one place — the deletion — and the two predicates MUST share one title
comparison and differ only by that type conjunct.

The re-ask MUST NOT fire on a source whose final list holds more than one
object, nor when the lone object does not restate the source title. Its own
backend failure MUST degrade to "added nothing", keeping the object already
produced, exactly as the selector judge's failure degrades.

#### Scenario: An expansion object under an acronym source restates it

- GIVEN a source titled with an acronym whose sole object is titled with
  that acronym's expansion, sharing no token with it
- WHEN extraction runs
- THEN the object is recognised as restating the source: one re-ask is
  spent, and if it finds nothing further the Source is marked

#### Scenario: Acronym recognition is symmetric

- GIVEN a source titled with the expansion whose sole object is titled with
  the acronym
- WHEN extraction runs
- THEN it is recognised the same way

#### Scenario: An expansion object beside a genuine subject is never dropped

- GIVEN a source titled with an acronym yielding both an object titled with
  its expansion and a second, distinct subject
- WHEN extraction runs
- THEN both objects are written, and no re-ask is spent

#### Scenario: Acronym and expansion stay distinct across chunk-merge dedup

- GIVEN one extraction reply holding both an acronym-titled object and an
  expansion-titled object, under a source titled as neither
- WHEN extraction runs
- THEN both objects survive as distinct objects

#### Scenario: A two-letter initialism does not match

- GIVEN a source titled `AI` whose sole object is titled `Artificial
  Intelligence`
- WHEN extraction runs
- THEN no re-ask is spent and the Source is not marked

#### Scenario: A sole title-restating object triggers one re-ask whose findings are added

- GIVEN a source whose final object list is exactly one object restating the
  source title, and a second ask that names a further distinct subject
- WHEN extraction runs
- THEN one extra call is made, and both the original object and the further
  subject are written

#### Scenario: A lone Procedure restating the title re-asks but is never dropped

- GIVEN a source whose only object is a `Procedure` titled the way the
  source titles itself
- WHEN extraction runs
- THEN the re-ask fires on it, and the `Procedure` is written whatever the
  second ask returns — the type exemption still bars the drop rule from
  removing it

#### Scenario: A re-ask that finds nothing changes nothing

- GIVEN the same trigger, and a second ask that returns an empty array
- WHEN extraction runs
- THEN the original object is written unchanged and nothing is added

#### Scenario: A returned candidate restating the title is discarded, whatever its type

- GIVEN the same trigger, and a second ask returning both a genuine further
  subject and a candidate whose title restates the source title under a
  different type
- WHEN extraction runs
- THEN only the genuine subject is added, and no two written objects share
  one title

#### Scenario: An object that does not restate the title does not trigger a re-ask

- GIVEN a source whose only object carries a title of its own, of any type
- WHEN extraction runs
- THEN no extra call is made and that object is written unchanged

#### Scenario: A lone object titled after the source's topic triggers a re-ask

- GIVEN a source titled `Lesson 3: Setting Up a Python Project` whose only
  object is titled `Setting Up a Python Project`
- WHEN extraction runs
- THEN the re-ask fires, even though the two titles are not equal

#### Scenario: Sharing one token with the source title does not trigger a re-ask

- GIVEN a source whose only object shares a single token with the source
  title while naming its own distinct subject
- WHEN extraction runs
- THEN no extra call is made

#### Scenario: More than one object does not trigger a re-ask

- GIVEN a source whose final object list holds two or more objects
- WHEN extraction runs
- THEN no extra call is made

#### Scenario: The extra call is reported

- GIVEN a source whose sole droppable twin triggered a re-ask
- WHEN `openkos ingest` completes
- THEN the run reports that the re-ask was spent and what it added, in
  wording distinct from the cap, judge, and pre-judge ceiling notices

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
objects written for a single source MUST NOT exceed a backstop cap of 12,
applied exactly once, after union construction and judge selection (or after
the judge-failure degrade) — never as a pre-judge truncation. During
staging, the system MUST, per candidate in reply order: derive a slug from
the candidate's title and drop a candidate whose title yields an empty slug;
apply an in-batch slug-collision guard that keeps the first and drops later
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
(Previously: hard cap of 5 in spec text, 6 in code, applied as a blind
first-N truncation before any selection step; now a backstop of 20 applied (12 before #564)
once, after union+judge selection.)

#### Scenario: More than the backstop of validated objects is bounded

- GIVEN a source whose union+judge selection would yield more than 12 valid
  objects
- WHEN `openkos ingest <path>` completes
- THEN no more than 12 derived objects are written

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

### Requirement: Chunked-Source Candidates Feed Staging Unchanged

For a source that triggers chunking, staging MUST consume the judge-selected
(or judge-failure-degraded) candidate set produced from the existing
per-chunk extraction and merge, subject to the same backstop of 20 and the
same per-candidate slug/validation rules as an unchunked source. Chunking
itself introduces no separate staging path.

#### Scenario: Chunked source staging uses the same cap and rules

- GIVEN a source long enough to be split into chunks during extraction
- WHEN `openkos ingest <path>` completes
- THEN staged derived objects obey the same 12-object backstop and slug
  rules as an unchunked source, with no chunk-specific exception

### Requirement: Judge-Failure Degrade Is Reported, Ingest Still Succeeds

WHEN the extraction judge call fails (raises `OllamaError`, returns an
empty or unparseable reply), `ingest` MUST proceed with the merged-union
candidates truncated by the backstop cap rather than falling back to
Source-only, MUST emit a note to stderr distinct from the Source-only
degrade notice, and MUST exit 0.

#### Scenario: Judge failure keeps validated candidates, not just the Source

- GIVEN a fake LLM backend whose base extraction succeeds but whose judge
  call raises `OllamaError`
- WHEN `openkos ingest <path>` runs
- THEN the merged-union candidates (backstop-truncated) are written as
  derived objects, a judge-failure note appears on stderr, and the exit
  code is 0

#### Scenario: Full LLM unavailability still falls back to Source-only

- GIVEN a fake LLM backend whose `chat` call raises a backend error during
  the base extraction call itself (not the judge)
- WHEN `openkos ingest <path>` runs
- THEN behavior is unchanged from the existing "Extraction Degrades
  Gracefully on LLM Unavailability" requirement — only the Source concept
  is written

### Requirement: Pre-Archive Measurement Gate

Before this change is archived, before/after runs on
`evals/extraction_cap/run_cap_eval.py` and the AMI type-coverage harness
MUST show recall not regressed on any fixture: genuine-subject retention
MUST NOT decrease and known-facet retention MUST NOT increase, on every
measured fixture including chunked transcripts.

#### Scenario: Eval gate blocks archive on regression

- GIVEN before/after eval runs comparing the single-cap path to the
  union+judge path
- WHEN any fixture shows decreased genuine-subject recall or increased
  facet retention
- THEN the change MUST NOT be archived until the regression is resolved

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
explicit override), proving the value is read, not assumed. WHEN the
derived object's OKF type has a configured per-type sensitivity offset
(`type-sensitivity-defaults`), the inherited Source value is a floor, not
the final value: the born `sensitivity` is
`combine_sensitivity(stamp_sensitivity, raise_by(cfg.default_sensitivity,
offset))`, so a type-defaulted object may be born strictly above the
Source's own resolved value, never below it. The `ingest` run summary MUST
carry the born-above-floor advisory (`type-sensitivity-defaults`) whenever
this raise applies to one or more staged derived objects.

#### Scenario: Provenance and sensitivity inherited from the Source's own value

- GIVEN a source ingested with a configured `sensitivity` value and
  successful extraction, and no per-type sensitivity offset configured for
  the derived object's type
- WHEN `openkos ingest <path>` completes
- THEN the derived object's frontmatter `provenance` includes a reference
  to the Source concept and its `sensitivity` equals the Source's own
  `sensitivity`

#### Scenario: A type-defaulted derived object is born above the Source's value

- GIVEN a source ingested and resolved at `public`, and a per-type
  sensitivity offset configured for the derived object's OKF type (e.g.
  `Person`) that raises the workspace floor to `private`
- WHEN `openkos ingest <path>` completes
- THEN that derived object's `sensitivity` is `private`, strictly above the
  Source's own resolved `public` value, and the run summary carries the
  born-above-floor advisory naming it

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

### Requirement: Byte-Identical Re-Ingest Converges Instead Of Accumulating

WHEN a byte-identical re-ingest resolves to a Source concept that already
exists, records an `origin_key`, and whose previous extraction ran to its
intended conclusion — no `extraction_status: failed`, no judge-degrade
`extraction_notice` token — `ingest` MUST skip extraction entirely (issue
#773): no model call, no write of any kind (not even a regenerated Source,
so prior markers like #585's sole-object disclosure survive untouched), exit
0, and one stderr line disclosing the skip and naming `--re-extract` as the
deliberate redo. Extraction is non-deterministic, so re-running it on an
unchanged source unions every set the model has ever produced — the
create-only dedup below can only catch verbatim-reproduced slugs — and a
re-ingest MUST converge on one set of objects per source, never accumulate.

Extraction MUST still re-run, without any flag, when the previous run left
RETRYABLE DEBT: `extraction_status: failed` (#187) or a judge-degrade
`extraction_notice` token (#772) — the exact states whose `lint` findings
name a plain re-ingest as the remedy. A pre-#552 legacy Source recording no
`origin_key` MUST take the full path once (which backfills the key), so the
no-verb self-migration is not suppressed. `--re-extract` MUST force the full
path on any re-ingest. A post-`forget` regenerate (raw bytes match, concept
absent) is a fresh pipeline run, never a skip.

#### Scenario: An unchanged, extracted source spends nothing and writes nothing

- GIVEN a source already ingested whose extraction succeeded
- WHEN `openkos ingest <path>` runs again with byte-identical content
- THEN no model call is made, no bundle file changes, the exit code is 0,
  and stderr names `--re-extract`

#### Scenario: Retryable debt re-extracts without the flag

- GIVEN a Source carrying `extraction_status: failed` or a judge-degrade
  `extraction_notice`
- WHEN `openkos ingest <path>` runs again with byte-identical content
- THEN extraction re-runs and reconciles per slug as below

#### Scenario: --re-extract is the deliberate redo

- GIVEN a source already ingested whose extraction succeeded
- WHEN `openkos ingest <path> --re-extract` runs
- THEN extraction re-runs and reconciles per slug as below

### Requirement: Re-Extraction Reconciles Derived Objects Per Slug

WHEN a re-ingest DOES run extraction (retryable debt, `--re-extract`, a
legacy origin-key backfill, or a post-`forget` regenerate), `ingest` MUST
reconcile derived objects per slug rather than all-or-nothing: for each
validated candidate, the system MUST check whether an object with that slug
already exists, MUST insert it only when no such slug exists yet
(create-only), and MUST leave any existing derived object file
byte-untouched — no overwrite, no re-typing, no merge.
The slug-existence check for a candidate MUST complete BEFORE any write for
that candidate, so a failed write never leaves a partially-reconciled state.
A genuinely new object CAN be inserted even
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
  content and a successful previous extraction
- WHEN `openkos ingest <path>` runs again
- THEN it short-circuits (issue #773's convergence requirement above), with
  no new derived-object files of any kind

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
(Previously: stated unconditionally that the Source's `sensitivity` equals
`cfg.default_sensitivity`, with no distinction between a fresh ingest and a
re-ingest, so a re-ingest silently reset any level a human had raised via
`set-sensitivity`.)

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

#### Scenario: Preview reports the workspace default after `forget`

- GIVEN a source whose Source concept was removed via `openkos forget`
  (`had_prior_source` is `False`), so there is no on-disk `sensitivity` to
  read
- WHEN the re-ingest preview is shown before Phase B writes
- THEN the preview line for the regenerated Source states the resolved
  level (`cfg.default_sensitivity`) with the trailing clause "from the
  workspace default"

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

### Requirement: Extraction Status Frontmatter Key on Zero-Derived-Object Degrade

WHEN a single `ingest` run writes zero derived objects, the system MUST write
an `extraction_status` frontmatter key on the Source concept, chosen from the
closed vocabulary below, keyed on WHY extraction produced nothing, never on
which specific gate condition fired today. WHEN at least one derived object
is written, `extraction_status` MUST be ABSENT — no `ok`/`none` sentinel.
Readers MUST ignore any value outside this vocabulary without raising.

| Value | Path | Debt |
|---|---|---|
| `no-extractable-text` | empty/undecodable raw content | No |
| `blocked-by-sensitivity` | confidential floor blocks the LLM send | No — deliberate policy, MUST NEVER be reported as retryable |
| `failed` | LLM backend raised an error | Yes — the only retryable value |
| `no-concepts-found` | successful call returned zero candidates | No |

The value MUST be stamped onto the freshly built Source content produced by
`okf.build_source_concept` each run, never merged onto on-disk frontmatter —
a merge would make a stale marker sticky. The system MUST NOT write the raw
exception text (or any other free-text detail) into this or any other
frontmatter field; the full message remains stderr-only, transient, and
local.

This key is independent of, and MUST NOT interact with, the `sensitivity`
resolution shipped for re-ingest (`okf.combine_sensitivity`): those rules
read and combine an on-disk value, while `extraction_status` is never read
from disk — it is recomputed from scratch every run.

#### Scenario: no-extractable-text is written

- GIVEN a source whose content is empty or fails to decode
- WHEN `openkos ingest <path>` completes
- THEN the Source's `extraction_status` is `no-extractable-text`

#### Scenario: blocked-by-sensitivity is written

- GIVEN a workspace `default_sensitivity` floor that blocks the LLM send and
  no `--include-confidential`
- WHEN `openkos ingest <path>` completes
- THEN the Source's `extraction_status` is `blocked-by-sensitivity`

#### Scenario: failed is written

- GIVEN a fake LLM backend whose `chat` call raises `OllamaError`
- WHEN `openkos ingest <path>` completes
- THEN the Source's `extraction_status` is `failed`

#### Scenario: no-concepts-found is written

- GIVEN a fake LLM backend that returns successfully with zero valid
  candidates
- WHEN `openkos ingest <path>` completes
- THEN the Source's `extraction_status` is `no-concepts-found`

#### Scenario: Successful extraction writes no key at all

- GIVEN extraction yields at least one derived object
- WHEN the Source concept's frontmatter is inspected
- THEN it contains no `extraction_status` key

#### Scenario: A previously failed Source self-clears on later success

- GIVEN a Source whose frontmatter currently has `extraction_status: failed`
- WHEN `openkos ingest raw/<name>` is re-run against the same Source and
  extraction now succeeds with at least one derived object
- THEN the rewritten Source's frontmatter has NO `extraction_status` key

#### Scenario: Unrecognized value is ignored without raising

- GIVEN a Source's on-disk `extraction_status` value is outside the closed
  vocabulary (e.g. a value from a future or reverted version)
- WHEN any reader of this field runs
- THEN it ignores the value and does not raise

#### Scenario: Sensitivity resolution is unaffected

- GIVEN a re-ingest that resolves `sensitivity` per `okf.combine_sensitivity`
  (on-disk value combined with `cfg.default_sensitivity`)
- WHEN the same run also stamps `extraction_status`
- THEN `extraction_status` is computed fresh from this run's outcome only,
  never read from or merged with the on-disk frontmatter, unlike
  `sensitivity`

### Requirement: Raw Destination Is Disambiguated By Origin, Not Basename

`raw/` MUST remain a flat namespace of bare basenames derived from
`Path(src).name` — the path-traversal defence — and every destination this
requirement produces MUST still be a bare basename directly under `raw/`.

Each Source MUST record an `origin_key` frontmatter key: a digest
identifying the resolved filesystem path it was ingested from. It MUST be a
digest, never a path or path fragment — the value's only consumer is
equality, and a structured path field would place `$HOME` and the machine's
directory layout into every Source's frontmatter and git history, removable
only by `purge`. The key MUST be derived from the RESOLVED path, so two
spellings of one file (`./notes.txt` from inside a folder,
`folder/notes.txt` from its parent) yield one key. `origin_key` MUST be
ABSENT on any Source written before this key existed, and absence MUST mean
exactly one thing: origin not recorded.

`ingest` MUST resolve its raw destination against the whole COLLISION
FAMILY of the candidate's basename — `<stem><ext>` and every
`<stem>-N<ext>` for positive integer N — matching the extension exactly, so
`notes.txt` and `notes.md` remain distinct basenames that never collide.
Family members MUST be matched NFC-normalized on both sides.

Resolution MUST proceed in family order:

1. a member whose owning Source records an `origin_key` EQUAL to the
   candidate's is the same file — the destination, subject to the
   raw-immutability refusal above;
2. a member whose owning Source records a DIFFERENT `origin_key` is a
   different file — skip it and continue;
3. a member whose owning Source records NO `origin_key`, or whose Source
   cannot be read or parsed, has unknown origin — match it on byte-identical
   content, which is the pre-#552 predicate, and continue otherwise.

When no member matches, the destination MUST be the first free
`<stem>-N<ext>` (N ascending from 2), and `ingest` MUST report the
substitution on stderr, naming both the taken basename and the copy
actually written. It MUST NOT choose a destination the user did not name
without saying so.

Raw immutability MUST be scoped to a file the run can IDENTIFY as the same
one. A member with unknown origin and differing content MUST disambiguate
rather than refuse: it cannot be proven to be the candidate, and refusing
turns away real content, which is the harm this requirement exists to end.
No existing byte in `raw/` may be rewritten under any branch.

A re-ingest matched under rule 3 MUST write the candidate's `origin_key`
onto the regenerated Source, so a legacy workspace self-migrates with no
repair verb and no migration step.

#### Scenario: Same basename, different content, both land

- GIVEN `raw/<name>` is owned by a Source whose origin differs from this
  candidate's
- WHEN `openkos ingest <path>` runs with content differing from that copy
- THEN both files exist — the incumbent untouched at `raw/<name>`, the
  candidate at `raw/<stem>-2<ext>` — each with its own Source

#### Scenario: Same basename, identical content, still two sources

- GIVEN two empty files sharing a basename in two different folders
- WHEN each is ingested in turn
- THEN the second lands at `raw/<stem>-2<ext>` with its own Source, rather
  than being reported as a re-ingest of the first

#### Scenario: Re-ingesting the same file spawns no copy

- GIVEN a source already ingested, whose Source records its `origin_key`
- WHEN `openkos ingest <path>` runs again against that same file, any
  number of times
- THEN exactly one raw copy and one Source exist, with no `-N` member

#### Scenario: A different working directory is still the same file

- GIVEN a source already ingested via a relative path
- WHEN it is re-ingested via its absolute path
- THEN it is a re-ingest, not a new source

#### Scenario: Changed bytes of an identified source still refuse

- GIVEN a source already ingested, whose Source records its `origin_key`
- WHEN the file is edited and re-ingested
- THEN `ingest` refuses with the raw-immutability message and writes no
  disambiguated copy

#### Scenario: A legacy Source matches on content and backfills its key

- GIVEN a Source carrying no `origin_key` whose raw copy is byte-identical
  to the candidate
- WHEN `openkos ingest <path>` runs
- THEN it is a re-ingest, no `-N` copy is created, and the regenerated
  Source carries the candidate's `origin_key`

#### Scenario: A legacy Source with differing content disambiguates

- GIVEN a Source carrying no `origin_key` whose raw copy differs from the
  candidate
- WHEN `openkos ingest <path>` runs
- THEN the candidate lands at `raw/<stem>-2<ext>`, the incumbent raw copy
  is unchanged, and nothing is refused

#### Scenario: The substitution is reported

- GIVEN a basename already held by a different file
- WHEN the candidate is disambiguated
- THEN stderr names both the taken basename and the destination written

### Requirement: Extraction Notice Frontmatter Key on a Sole Restating Object

WHEN a single `ingest` run's extraction retains EXACTLY ONE derived object and
that object restates the topic the Source's own title names, the system MUST
write an `extraction_notice` frontmatter key on the Source concept with the
value `sole-object-restates-source`. When no `extraction_notice` vocabulary
token applies (this one, #772's judge-degrade quarantine tokens — see
"Judge-Degrade Quarantine Marker" — #843's staging-loss marker — see
"Staging-Loss Disclosure Marker" — or any other vocabulary member), the key
MUST be ABSENT — no `ok`/`none` sentinel, mirroring `extraction_status`.
Readers MUST ignore any value outside this vocabulary without raising.

The object itself MUST be kept, unchanged. The system MUST NOT degrade to
zero derived objects on this condition: a genuinely single-subject source
whose only subject IS what its title names — the measured `mcp-launch` shape
protected by `_drop_source_title_twins`' floor — is indistinguishable from
this defect by title alone, so any rule that dropped on the title would emit
nothing for real content. Marking is strictly information-adding: it cannot
regress recall, and it does not need to tell the two cases apart.

This key is SEPARATE from `extraction_status` and MUST NOT be folded into
that vocabulary. `extraction_status` presupposes zero derived objects and
carries a retryable-debt reading (`lint`'s unextracted-source check matches
`failed`); `extraction_notice` presupposes exactly one. The two are mutually
exclusive on any real run and MUST NOT both be written by the same ingest.

The condition MUST be evaluated on the FINAL retained object list — after the
bounded re-ask, after judge selection, and after the per-source cap — never
on an intermediate one. The comparison MUST be the same title predicate the
re-ask trigger uses (exact normalized equality OR token containment in either
direction, type-blind), so the disclosure and the extra question can never
disagree about what "restates the source" means.

Like `extraction_status`, the value MUST be stamped onto freshly built Source
content each run and never merged onto on-disk frontmatter.

#### Scenario: A sole restating object is kept and the Source is marked

- GIVEN extraction retains exactly one derived object whose title restates
  the Source's title
- WHEN `openkos ingest <path>` completes
- THEN the derived object is written to the bundle unchanged, AND the
  Source's `extraction_notice` is `sole-object-restates-source`

#### Scenario: The notice is type-blind

- GIVEN the sole retained object is a `Procedure` restating the Source's
  title, which `_drop_source_title_twins` exempts from deletion
- WHEN `openkos ingest <path>` completes
- THEN the Source's `extraction_notice` is still
  `sole-object-restates-source`

#### Scenario: A sole distinct subject writes no key

- GIVEN extraction retains exactly one derived object whose title does NOT
  restate the Source's title, and staging stores it (no content-losing drop
  — see "Staging-Loss Disclosure Marker")
- WHEN the Source concept's frontmatter is inspected
- THEN it contains no `extraction_notice` key

#### Scenario: Two or more objects write no key

- GIVEN extraction retains two or more derived objects under a judge that
  SELECTED (`judge_status == "ok"`, or a path where the judge was rightly
  skipped), including the case where one of them restates the Source's
  title, and staging stores every candidate (no content-losing drop)
- WHEN the Source concept's frontmatter is inspected
- THEN it contains no `extraction_notice` key

#### Scenario: A zero-object degrade writes no notice

- GIVEN extraction produced zero derived objects and the Source therefore
  carries an `extraction_status` value
- WHEN the Source concept's frontmatter is inspected
- THEN it contains no `extraction_notice` key

#### Scenario: A previously marked Source self-clears on later success

- GIVEN a Source whose frontmatter currently has `extraction_notice:
  sole-object-restates-source`
- WHEN `openkos ingest raw/<name>` is re-run against the same Source and
  extraction now retains a second, distinct object
- THEN the rewritten Source's frontmatter has NO `extraction_notice` key

#### Scenario: The condition is reported on stderr

- GIVEN extraction retains exactly one derived object restating the Source
- WHEN `openkos ingest <path>` runs
- THEN stderr carries a non-blocking line naming both halves of the outcome:
  that the only derived object restates the source, and that it is kept and
  the Source marked

### Requirement: Judge-Degrade Quarantine Marker

WHEN a union+judge `ingest` run stores its merged candidate union WITHOUT a
quality selection — either because every judge attempt was unusable
(`judge_status == "failed"`) or because a well-formed judge reply admitted
no candidate (`judge_status == "empty"`) — the system MUST write an
`extraction_notice` frontmatter key on the Source concept recording which
degrade occurred: `judge-selection-unavailable` for `"failed"`,
`judge-selection-empty` for `"empty"` (issue #772). Fail-open is retained —
the objects are still written — but the admission MUST NOT be silent debt:
the marker is what lets `lint`'s unjudged-extraction scan and `status`'s
needs-attention fold-in guarantee a later surface revisits these objects.

The two tokens MUST stay distinct, preserving on disk the same failed/empty
split #754 established in the terminal notices: the causes carry different
retry expectations. The terminal degrade notice MUST disclose the marking,
mirroring the sole-object notice's `marking the Source (extraction_notice:
<token>)` shape.

When a judge-degrade token applies it MUST take precedence over
`sole-object-restates-source` — the quarantine reading is retryable debt,
the sole-object reading is a disclosure — although the two conditions are
mutually exclusive on any real run (a degrade implies a multi-candidate
union; the sole-object notice requires exactly one retained object).

Like every `extraction_notice` value, the token MUST be stamped onto freshly
built Source content each run and never merged onto on-disk frontmatter, so
a re-ingest whose judge selects rebuilds the Source without the marker.

#### Scenario: A failed judge quarantines the Source

- GIVEN a union+judge run whose judge call is unusable on every attempt
- WHEN `openkos ingest <path>` completes
- THEN the merged union's objects are written, AND the Source's
  `extraction_notice` is `judge-selection-unavailable`, AND stderr's degrade
  notice names the marking

#### Scenario: An empty admission quarantines with the distinct token

- GIVEN a union+judge run whose judge reply is well-formed but admits no
  candidate
- WHEN `openkos ingest <path>` completes
- THEN the Source's `extraction_notice` is `judge-selection-empty`

#### Scenario: A healthy selection writes no quarantine marker

- GIVEN a union+judge run whose judge admitted at least one candidate
- WHEN the Source concept's frontmatter is inspected
- THEN it contains no `extraction_notice` key (unless the sole-object
  condition independently applies)

#### Scenario: The quarantine self-clears on a later judged run

- GIVEN a Source whose frontmatter currently has `extraction_notice:
  judge-selection-unavailable`
- WHEN `openkos ingest raw/<name>` is re-run and the judge now selects
- THEN the rewritten Source's frontmatter has NO `extraction_notice` key

### Requirement: Staging-Loss Disclosure Marker

WHEN staging drops at least one extracted candidate on a CONTENT-LOSING path
— an unslugifiable title, an in-batch slug collision, or a
`okf.build_concept` validation failure — the system MUST write an
`extraction_notice` frontmatter key on the Source concept with the value
`candidates-dropped-in-staging` (issue #843). The per-candidate stderr
echoes remain; the marker is the durable half, so a later `lint`/`status`
pass can learn the bundle may under-represent this source after the
terminal has scrolled. One aggregate stderr line MUST disclose the marking,
mirroring the judge notices' `marking the Source (extraction_notice:
<token>)` shape.

The create-only skip (a slug this same source already owns is on disk) MUST
NOT count toward the marker: the bundle still represents the source, so a
marker would report a loss that never happened. The foreign-source
disambiguation path drops nothing and is likewise out of scope. The
IN-BATCH slug collision MUST NOT count either, on the identical grounds:
when two candidates of one run slugify alike, the first was already staged,
so the content is on disk. Counting it also created debt no command could
clear, since the redo this marker prescribes (`--re-extract`) reproduces the
same collision deterministically. The per-candidate stderr echo still names
the skipped duplicate, which is a fact; what it no longer does is stamp a
loss claim that is not.

`extraction_notice` MUST record EVERY condition a run tripped, not the
highest-precedence one. The key was single-valued and the strongest token
overwrote the weaker at write time, so a Source that both lost a candidate
in staging and stored objects quoting no line reached `lint` claiming only
the first — and `lint` could not disclose the masking from its side either,
because the displaced token was already destroyed. The two judge tokens stay
mutually exclusive (they are two values of one field); every other condition
is independent and MUST be appended independently, in detection order.

On disk, a single condition MUST remain a bare scalar, byte-identical to
what earlier releases wrote; only a genuine co-occurrence widens to a list.
Readers MUST accept both shapes, so an existing Source keeps its disclosure.

Precedence survives only as a PRESENTATION rule: the batch summary MUST
count a file ONCE however many conditions it carries, because that term
measures files. `lint` is the surface that enumerates every condition.

The token MUST NOT be retryable debt (`cli/main._extraction_retry_due`
excludes it, on #801's exact grounds): a plain re-ingest re-runs the same
prompt over the same bytes and is promised to fix nothing about the sample
that failed staging. The `lint` finding (`check_staging_dropped`, kind
`staging-dropped`, its own `Staging-dropped candidates:` section) MUST name
`--re-extract` as the redo and MUST NOT spell a bare re-ingest command.
`status` MUST fold the finding into "needs attention".

This marker covers the formerly silent `plans == [] and skip_reason is
None` state (every candidate individually dropped): that state still writes
no `extraction_status` key, and now carries this notice.

#### Scenario: A sole candidate lost in staging marks the Source

- GIVEN extraction retains exactly one candidate and staging drops it (its
  title slugifies to nothing)
- WHEN `openkos ingest <path>` completes
- THEN the Source's `extraction_notice` is `candidates-dropped-in-staging`,
  AND its frontmatter has no `extraction_status` key, AND stderr disclosed
  the marking

#### Scenario: A partial staging loss marks the Source beside written objects

- GIVEN extraction retains two candidates, staging stores one and drops the
  other on a content-losing path
- WHEN `openkos ingest <path>` completes
- THEN the stored object is written AND the Source's `extraction_notice` is
  `candidates-dropped-in-staging`

#### Scenario: The create-only skip writes no staging marker

- GIVEN a re-extraction whose only candidate's slug is already on disk for
  this same source
- WHEN `openkos ingest <path> --re-extract` completes
- THEN the rewritten Source's frontmatter has NO `extraction_notice` key

#### Scenario: A judge degrade is recorded beside the staging marker

- GIVEN a union+judge run whose judge call is unusable on every attempt AND
  whose staging drops a candidate
- WHEN `openkos ingest <path>` completes
- THEN the Source's `extraction_notice` carries BOTH
  `judge-selection-unavailable` and `candidates-dropped-in-staging`

#### Scenario: The staging marker is recorded beside the evidence disclosure

- GIVEN a run where at least one stored object quotes no line of the source
  AND staging drops another candidate on a content-losing path
- WHEN `openkos ingest <path>` completes
- THEN the Source's `extraction_notice` carries BOTH
  `objects-without-evidence` and `candidates-dropped-in-staging`
- AND `openkos lint` names the Source under BOTH `Unevidenced objects:` and
  `Staging-dropped candidates:`

#### Scenario: An in-batch slug collision is skipped without a loss marker

- GIVEN a run where two candidates slugify to the same value and nothing
  else is lost in staging
- WHEN `openkos ingest <path>` completes
- THEN stderr names the skipped duplicate
- AND the Source's `extraction_notice` does NOT carry
  `candidates-dropped-in-staging`

#### Scenario: A single condition stays a bare scalar on disk

- GIVEN a run tripping exactly one disclosure condition
- WHEN `openkos ingest <path>` completes
- THEN the Source's `extraction_notice` value is that token as a string,
  not a one-element list

#### Scenario: The marker is not retryable debt

- GIVEN a Source whose frontmatter carries `extraction_notice:
  candidates-dropped-in-staging`
- WHEN a byte-identical `openkos ingest` re-run converges on it
- THEN extraction is skipped (zero model calls), the Source is untouched on
  disk, and the marker survives for `lint`/`status` to keep reporting

### Requirement: Ingest Builds The FTS Index Once At The End Of Each Run

`ingest` MUST build the on-disk FTS index (`.openkos/fts.db`) exactly once
per invocation, at the END of the run — after the single-file pipeline
returns, or after a batch's per-file loop completes — so the quickstart
(`init` -> `ingest` -> `query`) gets hybrid retrieval on its first query
without a manual `openkos reindex` in between (issue #553). A batch of N
files MUST pay one build, never one per file. The build MUST be fail-open:
it runs after the ingested Sources and concepts are already written and
committed, so any build failure degrades to one stderr notice naming
`openkos reindex` and MUST NOT change the run's exit code or undo the
ingest. The build MUST NOT require the embedding backend: it is a pure
FTS5 projection of the bundle, so it succeeds even on runs whose embed
degraded. The bundle-manifest-hash gate still applies, so a run that wrote
nothing new costs a hash check, not a rebuild.

#### Scenario: First query after the quickstart uses hybrid retrieval

- GIVEN a freshly initialized workspace
- WHEN `openkos ingest <path>` completes successfully
- THEN `.openkos/fts.db` exists and serves the ingested Source's content

#### Scenario: A batch pays exactly one FTS build

- GIVEN a directory of N ingestable files
- WHEN `openkos ingest <dir> --auto` runs
- THEN the FTS index is built exactly once, after the last file

### Requirement: Batch Cost Gate Announces A Fan-Out-Aware Estimate

The batch cost gate MUST announce an ESTIMATE of the model calls the run
will spend, summed from per-file estimates computed by
`extraction.concept.estimate_extraction_calls` — the same thresholds and
window arithmetic the pipeline branches on — never a one-call-per-file
identity (issue #775: the gate announced 3 and the run made ~16, an
order-of-magnitude consent failure). The line MUST be labelled as an
estimate (`~N LLM call(s) (estimate; ...)`), MUST name how many sources
will be split into roughly how many windows when any source fans out, and
MUST count a file at zero when the pipeline will make no model call for
it: an undecodable or blank source, the confidential floor gate without
`--include-confidential`, and a file #773's convergence skip will not
extract (disclosed as `N unchanged -- extraction will be skipped`). The
skip prediction MUST reuse the same retryable-debt predicate `ingest`'s
own skip decision reads, and MUST fail open — a file whose state cannot be
read is billed at the full estimate, never silently dropped from the count.
The estimate targets the ordinary path; reply-dependent calls (judge retry,
re-ask, single-candidate judge skip) are out of its scope, exactly as they
are out of the documented cost table's.

#### Scenario: A chunking source is billed per window

- GIVEN a batch holding one small prose file and one prose file above its
  chunking threshold
- WHEN the batch cost gate renders
- THEN it names the splitting source's approximate window count and the
  total is the sum of both files' ordinary-path estimates, marked `~` and
  `estimate`

#### Scenario: A convergent re-ingest is billed at zero

- GIVEN a batch whose only file is unchanged and already extracted
- WHEN the batch cost gate renders
- THEN it announces `~0 LLM call(s)` and discloses the skipped file

#### Scenario: A failed FTS build never costs the ingest

- GIVEN an environment where the FTS build raises (e.g. fts5 unavailable)
- WHEN `openkos ingest <path> --auto` runs
- THEN the ingest itself succeeds with an unchanged exit code, and one
  stderr notice names `openkos reindex`

### Requirement: Batch Summary Discloses Extraction Notices

The batch summary — deliberately the run's LAST word on stdout, after every
per-file outcome line (issue #349) — MUST carry a term counting the files
whose Source concept finished the run carrying an `extraction_notice`
(issue #805, item 1), beside the existing ingested / re-ingested / skipped /
extraction-degraded terms.

The term MUST count EVERY member of the `extraction_notice` vocabulary, not
only the two retryable judge-degrade tokens. It is therefore deliberately a
WIDER set than `lint`'s unjudged-extraction scan reports:
`sole-object-restates-source` is a disclosure rather than debt, so no later
surface flags it for repair, which is exactly why the run's last word must
still say it happened. The two numbers answer different questions and MUST
NOT be presented as the same one.

The term MUST NOT overlap `extraction-degraded`: that term counts a
Source-only degrade (zero derived objects), while a notice presupposes at
least one derived object was written, and staging produces the two on
mutually exclusive paths.

When the count is non-zero the summary MUST also name where the notices are
recoverable — the `extraction_notice` frontmatter key carries every kind,
`openkos lint` flags the retryable ones — and MUST stay silent about that on
a run with no notices, following the same healthy-path-is-quiet rule as
every other ingest advisory.

#### Scenario: A batch counts only the files that finished with a notice

- GIVEN a two-file batch in which one file's judge is unusable on every
  attempt and the other's judge selects normally
- WHEN the batch summary renders
- THEN it reports one file with an extraction notice, and the healthy file
  is not counted

The term counts what a Source CARRIES when the run ends, not what the run
stamped. A byte-identical re-ingest converges without re-extracting and
stamps nothing (issue #773), yet leaves the prior run's notice untouched on
disk — so that file MUST still be counted. Reading the prior token back for
this purpose is a READ of frontmatter the convergence guard already
inspects, never a write-back: the never-read-back rule governs what is
written, and this path writes nothing. An absent or unrecognised token MUST
narrow to "no notice" rather than raising, since frontmatter is
hand-editable and a Source written by a later release may spell a token this
build does not know.

The term MUST be rendered like every other count-dependent noun on that
line, with no article that a count above one contradicts.

#### Scenario: A healthy batch reports zero and names nothing

- GIVEN a batch in which every file extracted and was judged
- WHEN the batch summary renders
- THEN the term is present and reads zero, and no recovery pointer is
  printed

#### Scenario: A converged re-ingest still counts its carried notice

- GIVEN a source whose first ingest stamped an `extraction_notice` that is
  not retryable debt
- WHEN the identical bytes are re-ingested in a batch, converge, and no
  extraction runs
- THEN the Source document is unchanged on disk and the batch summary still
  counts that file under the notice term

#### Scenario: The term reads correctly for more than one file

- GIVEN a batch in which two files finish carrying a notice
- WHEN the batch summary renders
- THEN the term names both without asserting a single one
