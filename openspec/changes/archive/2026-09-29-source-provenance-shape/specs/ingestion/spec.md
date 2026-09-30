# Delta for Ingestion

## MODIFIED Requirements

### Requirement: Ingest Raw Copy and Source Concept Generation

`openkos ingest <path>` MUST copy the raw source into the bundle's raw
storage as an exclusive (create-only) binary write and generate exactly one
OKF Source concept with frontmatter `type`, `title`, `description`,
`resource`, `tags`, `generated: { by: openkos/<version>, at: <ISO-8601 Z> }`,
`status: stable`, plus OpenKOS-layer `version`, `freshness`, and
`sensitivity`. The Source concept MUST carry no `provenance` frontmatter key
and no `sources` key (see "OKF-Native Provenance" and "`sources` Is A
Generated, One-Way Projection Of `provenance`" above): its `resource` field
already names its one raw original, so neither a `provenance` list nor a
`sources` projection of it would add anything a reader does not already
have. WHEN the decoded source's
leading frontmatter parses to a mapping, the generated Source concept's
frontmatter also carries the extension key `source_frontmatter` holding
that mapping verbatim (see "The `source_frontmatter` Namespace Preserves
The Incoming Mapping Verbatim"), and its `tags` include any tags lifted
from that mapping (see "Source Tag Lift And Re-Ingest Union"); otherwise
`source_frontmatter` is absent and `tags` carries only what this
capability otherwise resolves. In addition, `ingest` MUST attempt LLM-driven
extraction of a **bounded list** of derived objects —
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
Source concept's BODY MUST embed that text verbatim under a labeled section.
WHEN the source is not valid UTF-8 text, the body MUST instead contain a
short, honest note that the content could not be embedded as text (no
crash). Neither case MUST append a `# Citations` heading; the Source's
provenance is its `resource` field alone, per OKF v0.2 §5.1/§13.1's
`resource`-only Source shape (see "OKF-Native Provenance" above). An empty
source MUST render a body distinct from both
the verbatim and undecodable cases. The generated Source concept MUST pass
`check_conformance`. The `description` MUST remain a single line (no
newlines) and MUST state that the raw source's content was embedded
verbatim, and MUST NOT claim extraction or splitting into derived concepts.

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
`---` line exists later in the file; otherwise it is ordinary content. This
is the SAME block-boundary rule the incoming-frontmatter parser uses (see
"Incoming Frontmatter Parse Is Fail-Closed And Bounded"); the two MUST
never disagree about where a leading frontmatter block ends. WHEN
`raw_content` could not be decoded as UTF-8, or is blank or
whitespace-only, title derivation MUST NOT run and `title` MUST be (3).

`slug` remains derived from the filename only and is unaffected by this
requirement; the Source document's filename and concept id do not change.
This requirement does NOT read a source's own YAML `title:` field, does NOT
recognize setext headings, and does NOT backfill already-ingested Sources.

(Previously: `title` was always `_titleize(src.stem)`, with no content-derived
candidate or fallback chain.)
(Previously: frontmatter carried `timestamp` and `status: active`, and both
the verbatim and undecodable body cases ended with a bare `# Citations`
heading; OKF v0.2 supersedes both with `generated`/`status: stable`/
`sources`, and the heading is no longer written.)
(Previously: the generated Source concept's frontmatter never carried
`source_frontmatter`, and `tags` was always empty regardless of any
incoming frontmatter the raw source carried.)
(Previously: `ingest` also wrote `provenance: [resource]` on the Source
concept itself; see "OKF-Native Provenance" above for why that stopped.)

#### Scenario: Successful ingest embeds verbatim text

- GIVEN an initialized workspace and a readable UTF-8 text source at
  `<path>`
- WHEN `openkos ingest <path>` completes (confirmed or `--auto`)
- THEN the raw source is copied, one Source concept exists whose body
  contains that source's text verbatim under a labeled section with no
  trailing `# Citations` heading, `check_conformance` reports no
  violations, and `index.md`/`log.md` reflect the new entry

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

#### Scenario: Valid incoming frontmatter yields source_frontmatter and lifted tags

- GIVEN a source whose decoded content opens with a well-formed YAML
  frontmatter block carrying `tags: [alpha, beta]`
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept's frontmatter carries
  `source_frontmatter` equal to the parsed mapping, its `tags` include
  `alpha` and `beta`, and its body still contains the YAML block verbatim
  under `## Source content`

#### Scenario: Malformed incoming frontmatter yields neither, and ingest still succeeds

- GIVEN a source whose decoded content opens with a `---` block containing
  malformed YAML followed by a closing `---`
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept carries no `source_frontmatter` key, no
  tag is lifted, the raw copy and body embedding are unaffected, and the
  command exits 0

### Requirement: OKF-Native Provenance

The system MUST record a Source's provenance as its `resource:` frontmatter
field alone — the raw path under `raw/` that field already names — with no
separate provenance store and no `provenance:` frontmatter key on the
Source concept itself. A derived object's provenance MUST still be recorded
as a `provenance:` frontmatter list of Concept IDs (never raw paths) naming
the Source(s)/concept(s) it was derived from (see "Derived Object
Provenance and Sensitivity Inheritance").

(Reason: a Source's own `provenance: [raw/<file>]` entry duplicated
`resource`, was never projected into `sources`, and read as exactly the
raw-path provenance shape every other conformance rule forbids.)

(Previously: `ingest` wrote `provenance: [resource]` on every Source
concept; a Source written before this change keeps that on-disk entry
untouched, and `lint`/`status` MUST NOT start flagging it.)

#### Scenario: A Source's provenance is its resource field alone

- GIVEN a successful ingest of `<path>`
- WHEN the generated Source concept's frontmatter is inspected
- THEN it carries a `resource` field naming the raw copy and no
  `provenance` frontmatter key

#### Scenario: A derived object's provenance still names Concept IDs

- GIVEN a successful ingest of `<path>` whose extraction yields a derived
  object
- WHEN that derived object's frontmatter is inspected
- THEN its `provenance` list names the Source's Concept ID, never a raw
  path

#### Scenario: A pre-existing Source's raw-path provenance is left alone

- GIVEN a Source concept written by an older `openkos` version, carrying
  `provenance: [raw/<file>]`
- WHEN `openkos lint` or `openkos status` runs against that bundle
- THEN neither command flags that entry as dangling, and it is left
  byte-identical on disk
