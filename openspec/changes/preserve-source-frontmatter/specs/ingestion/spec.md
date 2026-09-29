# Delta for Ingestion

## ADDED Requirements

### Requirement: Incoming Frontmatter Parse Is Fail-Closed And Bounded

WHEN a raw source decodes as UTF-8 text, `ingest` MUST attempt to parse a
leading YAML frontmatter block from the decoded text, using the SAME
block-boundary rule as title derivation's frontmatter skip: a leading
`---` line counts as frontmatter only when a closing `---` line exists
later in the file. The parse MUST use `yaml.safe_load` semantics only; a
leading `+++` (TOML) or `;;;` (JSON) fence MUST NOT be auto-detected as an
alternate frontmatter format and MUST NOT be parsed as one — only the YAML
`---` fence is recognized. The parse MUST enforce a bounded input size and
an alias/anchor expansion guard, so a maliciously large or alias-amplified
("billion laughs") block cannot exhaust memory or stall the ingest.

WHEN the parsed root is not a mapping, WHEN the YAML is malformed, WHEN the
block exceeds the bounded size, WHEN alias/anchor expansion would exceed
the guard, or WHEN any other parse exception occurs, the system MUST treat
the source as having no frontmatter to preserve: it MUST NOT raise, MUST
NOT fail or abort the ingest, MUST leave the raw copy unaffected, MUST
leave the Source's body embedding of the decoded text unaffected, and MUST
lift nothing — no `source_frontmatter` key, no tag lift, no sensitivity
fold, no event-date tier. A source that does not decode as UTF-8 text MUST
NOT be parsed for frontmatter at all.

#### Scenario: A non-mapping root lifts nothing

- GIVEN a source whose decoded content opens with a well-formed `---`
  block whose YAML root is a list or a scalar, not a mapping
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept carries no `source_frontmatter` key,
  no tag is lifted, and the command exits 0

#### Scenario: Malformed YAML lifts nothing and does not fail the ingest

- GIVEN a source whose decoded content opens with a `---` block containing
  syntactically invalid YAML followed by a closing `---`
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept carries no `source_frontmatter` key,
  no tag is lifted, the raw copy and body embedding are unaffected, and the
  command exits 0

#### Scenario: A TOML or JSON fence is not auto-detected

- GIVEN a source whose decoded content opens with a `+++` (TOML) or `;;;`
  (JSON) fenced block instead of a `---` YAML fence
- WHEN `openkos ingest <path>` completes
- THEN nothing is lifted and no `source_frontmatter` key is written; the
  block is never parsed as TOML or JSON

#### Scenario: An oversized block lifts nothing

- GIVEN a source whose leading `---`-fenced block exceeds the parser's
  bounded size limit
- WHEN `openkos ingest <path>` completes
- THEN nothing is lifted, no `source_frontmatter` key is written, and the
  command exits 0 with no crash or hang

#### Scenario: An alias/anchor expansion bomb lifts nothing

- GIVEN a source whose leading frontmatter block uses YAML anchors and
  aliases to expand into content exceeding the alias-expansion guard
- WHEN `openkos ingest <path>` completes
- THEN nothing is lifted, no `source_frontmatter` key is written, and the
  command exits 0 with no memory exhaustion or hang

#### Scenario: A source that is not valid UTF-8 is never parsed for frontmatter

- GIVEN a source whose bytes do not decode as UTF-8
- WHEN `openkos ingest <path>` completes
- THEN the incoming-frontmatter parser is never invoked, no
  `source_frontmatter` key is written, and no tag or sensitivity is lifted

#### Scenario: The parse boundary agrees with title derivation's skip boundary

- GIVEN a source starting with a `---` line with no later closing `---`
  anywhere in the file — the same file title derivation treats as ordinary
  content, not frontmatter
- WHEN `openkos ingest <path>` completes
- THEN the incoming-frontmatter parser also treats the whole file as
  carrying no frontmatter block and lifts nothing, agreeing with title
  derivation's treatment of the same file

### Requirement: The `source_frontmatter` Namespace Preserves The Incoming Mapping Verbatim

WHEN incoming frontmatter parses successfully to a mapping,
`build_source_concept` MUST emit that mapping, whole and unmodified, under
a new frontmatter extension key `source_frontmatter` on the generated
Source concept. This namespace key MUST hold every key the incoming
mapping carried, including keys this capability separately lifts (`tags`,
`sensitivity`, `date`) and keys it never lifts (see "Never-Lifted Incoming
Frontmatter Keys"). Emitting `source_frontmatter` MUST NOT change the
Source's body: the YAML block MUST still appear verbatim under `## Source
content` (or the source's undecodable/empty-body case), unaffected by this
requirement.

#### Scenario: Valid frontmatter is preserved verbatim under source_frontmatter

- GIVEN a source whose decoded content opens with a well-formed YAML
  frontmatter block
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept's frontmatter carries
  `source_frontmatter` equal to the parsed mapping, unmodified

#### Scenario: No incoming frontmatter means no source_frontmatter key

- GIVEN a source whose decoded content carries no leading frontmatter block
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept's frontmatter carries no
  `source_frontmatter` key

#### Scenario: The body still contains the YAML block verbatim

- GIVEN a source whose decoded content opens with a well-formed YAML
  frontmatter block
- WHEN `openkos ingest <path>` completes
- THEN the Source concept's body under `## Source content` still contains
  that YAML block, byte-identical to the decoded source text

### Requirement: Never-Lifted Incoming Frontmatter Keys

The system MUST NOT lift `status`, `type`, `provenance`, `version`,
`timestamp`, `generated`, `verified`, `sources`, `author`, `updated`, or
`created` from incoming frontmatter onto the generated Source's own
frontmatter values for those keys, whatever their incoming value. These
keys, when present in the incoming mapping, MUST remain readable only
inside `source_frontmatter`, verbatim. An incoming file asserting any of
these keys MUST NOT change the generated Source's own `status`, `type`,
`provenance`, `version`, `generated`, or `sources` value, and MUST NOT
change the outcome of event-date resolution (an incoming `created:` value
is never consulted there; see "Event-Date Precedence On First Ingest" and
"Re-Ingest Event-Date Carry-Forward").

#### Scenario: An incoming type/status/provenance is never asserted onto the Source

- GIVEN a source whose incoming frontmatter carries `type: Entity`,
  `status: draft`, and `provenance: ["elsewhere.md"]`
- WHEN `openkos ingest <path>` completes
- THEN the generated Source's own `type`, `status`, and `provenance`
  values are unaffected by those incoming keys, and all three remain
  readable, unmodified, only inside `source_frontmatter`

#### Scenario: author and updated stay namespace-only

- GIVEN a source whose incoming frontmatter carries `author: "A. Person"`
  and `updated: 2026-07-14`
- WHEN `openkos ingest <path>` completes
- THEN neither value is lifted onto any frontmatter field outside
  `source_frontmatter`

#### Scenario: created is never lifted for event-date resolution

- GIVEN a source whose incoming frontmatter carries `created: 2026-01-01`
  and no `date:` key, ingested with no `--event-date` flag and no dated
  file name
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept's frontmatter carries no `event_date`
  key; `created` never fills that precedence chain

### Requirement: Source Tag Lift And Re-Ingest Union

WHEN incoming frontmatter carries a `tags` key, the system MUST normalize
it to a lifted tag list: a YAML list whose items are all strings lifts
each non-empty, trimmed string as one tag; a single YAML string value
lifts that one whole string, trimmed, as one tag (so `tags: a, b` lifts as
the single tag `"a, b"`, never as two tags `"a"` and `"b"`); any other
shape — a list containing a non-string item, a mapping, a number, a
boolean, `null`, or a result that trims to empty — MUST lift no tags at
all.

The lifted tags MUST be added to the Source concept's `tags`. On a FRESH
ingest (no prior Source concept), the Source's `tags` MUST be exactly the
lifted tags, deduplicated and order-preserving, or empty when none lift.
On a RE-INGEST, the Source's `tags` MUST be the UNION of the tags already
present on the on-disk Source and the newly lifted tags, deduplicated and
order-preserving; a tag a human added by hand to the on-disk Source MUST
NEVER be removed by a re-ingest, whatever the incoming frontmatter now
carries or no longer carries.

#### Scenario: A YAML list of tag strings lifts each tag

- GIVEN a source whose incoming frontmatter carries `tags: [alpha, beta]`
- WHEN `openkos ingest <path>` completes
- THEN the generated Source's `tags` include `alpha` and `beta`

#### Scenario: A single string tag lifts as one tag

- GIVEN a source whose incoming frontmatter carries `tags: solo`
- WHEN `openkos ingest <path>` completes
- THEN the generated Source's `tags` include exactly one tag, `solo`

#### Scenario: A comma-separated string lifts as one tag, not split

- GIVEN a source whose incoming frontmatter carries `tags: "a, b"`
- WHEN `openkos ingest <path>` completes
- THEN the generated Source's `tags` include exactly one tag, `"a, b"`,
  never two separate tags `"a"` and `"b"`

#### Scenario: A non-string list item lifts no tags

- GIVEN a source whose incoming frontmatter carries `tags: [alpha, 3]`
- WHEN `openkos ingest <path>` completes
- THEN no tag is lifted from that key at all

#### Scenario: Re-ingest unions lifted tags with on-disk tags

- GIVEN a Source already on disk with `tags: [alpha]`, and a re-ingest
  whose incoming frontmatter now carries `tags: [beta]`
- WHEN `openkos ingest <path>` re-ingests that source
- THEN the regenerated Source's `tags` are `[alpha, beta]` (union,
  deduplicated, order-preserving)

#### Scenario: A hand-added tag survives re-ingest even when the incoming file's tags changed

- GIVEN a Source already on disk with `tags: [alpha, hand-added]`, where
  `hand-added` was added directly to the Source document and never
  appeared in any incoming frontmatter, and a re-ingest whose incoming
  frontmatter now carries `tags: [beta]`
- WHEN `openkos ingest <path>` re-ingests that source
- THEN the regenerated Source's `tags` include `hand-added`, `alpha`, and
  `beta`; nothing already on disk is removed

#### Scenario: No incoming tags key leaves the Source's tags unaffected

- GIVEN a source whose incoming frontmatter carries no `tags` key
- WHEN `openkos ingest <path>` completes (fresh or re-ingest)
- THEN no tag is lifted, and an existing on-disk Source's `tags` are
  carried forward unchanged

## RENAMED Requirements

### Requirement: Converged Re-Ingest Date-Only Rewrite → Converged Re-Ingest Source-Only Rewrite

(Reason: decision D generalizes the rewrite's trigger from "the resolved `event_date` differs from what is stored" to any of the resolved `event_date`, the parsed `source_frontmatter`, the tag union, or the resolved `sensitivity` differing from what is stored, so the requirement's name and scope both widen from a single date field to the Source's frontmatter generally.)
(Migration: any reference to "the date-only rewrite" now means "the Source-only rewrite"; the event_date-only trigger becomes one case among several under the same gate, preview, and autocommit shape, with no behavior change for a source carrying no frontmatter of its own.)

## MODIFIED Requirements

### Requirement: Ingest Raw Copy and Source Concept Generation

`openkos ingest <path>` MUST copy the raw source into the bundle's raw
storage as an exclusive (create-only) binary write and generate exactly one
OKF Source concept with frontmatter `type`, `title`, `description`,
`resource`, `tags`, `generated: { by: openkos/<version>, at: <ISO-8601 Z> }`,
`status: stable`, and `sources` (see "`sources` Is A Generated, One-Way
Projection Of `provenance`" above), plus OpenKOS-layer `version`,
`freshness`, `sensitivity`, and `provenance`. WHEN the decoded source's
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
provenance is carried entirely in frontmatter (`sources`, `provenance`), per
OKF v0.2 §5.1/§13.1. An empty source MUST render a body distinct from both
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

### Requirement: Event-Date Precedence On First Ingest

On a source with no prior Source concept, `ingest` MUST resolve
`event_date` in this precedence: the `--event-date` flag, when given and
valid; otherwise a valid `date:` key lifted from the source's incoming
frontmatter (per "Source Tag Lift And Re-Ingest Union"'s sibling
date-lift rule); otherwise the file-name inference above; otherwise unset
(the key is omitted). An incoming `created:` key, or any other
frontmatter date-like key, MUST NOT be consulted for this precedence.
(Previously: the file-name inference immediately followed the flag, with
no frontmatter `date:` tier between them.)

#### Scenario: The flag wins over a dated file name

- GIVEN a source file named `call-2026-07-14.txt`, ingested with
  `--event-date 2026-08-01`
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept's `event_date` is `2026-08-01`, not
  `2026-07-14`

#### Scenario: The flag wins over a frontmatter date

- GIVEN a source whose incoming frontmatter carries `date: 2026-07-14`,
  ingested with `--event-date 2026-08-01`
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept's `event_date` is `2026-08-01`, not
  `2026-07-14`

#### Scenario: A frontmatter date wins over the dated file name

- GIVEN a source file named `call-2026-01-01.txt` whose incoming
  frontmatter carries `date: 2026-07-14`, ingested with no `--event-date`
  flag
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept's `event_date` is `2026-07-14`, not
  `2026-01-01`

#### Scenario: The file name is used when no flag or frontmatter date is given

- GIVEN a source file named `call-2026-07-14.txt`, carrying no incoming
  frontmatter `date:` key, ingested with no `--event-date` flag
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept's `event_date` is `2026-07-14`

#### Scenario: Neither source leaves the key unset

- GIVEN a source file with no dated name and no incoming frontmatter
  `date:` key, ingested with no `--event-date` flag
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept's frontmatter carries no `event_date`
  key

#### Scenario: created is ignored for this precedence

- GIVEN a source whose incoming frontmatter carries `created: 2026-07-14`
  and no `date:` key, ingested with no `--event-date` flag and no dated
  file name
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept's frontmatter carries no `event_date`
  key; `created` does not fill the precedence chain

### Requirement: Re-Ingest Event-Date Carry-Forward

On a re-ingest of a Source that already exists, `ingest` MUST resolve
`event_date` in this precedence: the `--event-date` flag, when given and
valid; otherwise the value already stored on the existing Source's
frontmatter; otherwise a valid `date:` key lifted from the source's
incoming frontmatter; otherwise file-name inference; otherwise unset. An
incoming `created:` key, or any other frontmatter date-like key, MUST NOT
be consulted for this precedence. A re-ingest with no flag MUST NEVER
clobber a stored `event_date` with a different or absent value, and MUST
NEVER invent one from ingest time. WHEN the on-disk stored value was
written as an unquoted YAML date scalar and is read back as a native date
object rather than a string, `ingest` MUST recognize it as the same
stored value the precedence chain compares against, exactly as if it had
been read back as a string.

WHEN an explicit `--event-date` flag differs from the currently stored
value, `ingest` MUST overwrite the stored value with the flag's value.

WHEN the stored `event_date` value cannot be read as a valid calendar date
— for example a hand-edited value, a `datetime` instead of a `date`, or any
other malformed value — `ingest` MUST treat it as absent for the
precedence chain above: it MUST NEVER be carried forward as-is. `ingest`
MUST print exactly one warning to stderr naming the malformed value and
the Source it was found in, and MUST NOT fail the run.
(Previously: the file-name inference immediately followed the stored
value, with no frontmatter `date:` tier between them.)

#### Scenario: Re-ingest with no flag keeps the stored value

- GIVEN a Source previously ingested with `event_date: 2026-07-14`
- WHEN `openkos ingest <path>` re-ingests the same source with no
  `--event-date` flag
- THEN the regenerated Source's `event_date` remains `2026-07-14`

#### Scenario: A differing flag overwrites the stored value

- GIVEN a Source previously ingested with `event_date: 2026-07-14`
- WHEN `openkos ingest <path> --event-date 2026-08-01` re-ingests the same
  source
- THEN the regenerated Source's `event_date` is `2026-08-01`

#### Scenario: A stored value wins over a frontmatter date

- GIVEN a Source previously ingested with `event_date: 2026-07-14`, and a
  re-ingest whose incoming frontmatter now carries `date: 2026-09-01`
- WHEN `openkos ingest <path>` re-ingests the same source with no
  `--event-date` flag
- THEN the regenerated Source's `event_date` remains `2026-07-14`,
  unaffected by the frontmatter `date:` value

#### Scenario: A frontmatter date fills a Source with no stored value, ahead of the file name

- GIVEN a Source with no stored `event_date`, whose raw file name carries
  the dated token `2026-01-01`, and whose incoming frontmatter now carries
  `date: 2026-07-14`
- WHEN `openkos ingest <path>` re-ingests the same source with no
  `--event-date` flag
- THEN the regenerated Source's `event_date` is `2026-07-14`, not the file
  name's `2026-01-01`

#### Scenario: A stored date read back as a YAML date object is still recognized

- GIVEN a Source whose on-disk `event_date: 2026-07-14` was parsed by
  PyYAML into a native `date` object rather than a string
- WHEN `openkos ingest <path>` re-ingests the same source with no
  `--event-date` flag
- THEN the regenerated Source's `event_date` remains `2026-07-14`, carried
  forward correctly regardless of the on-disk value's parsed type

#### Scenario: A legacy Source with no stored value falls back to the file name

- GIVEN a Source previously ingested before this feature existed, carrying
  no `event_date`, whose raw file name carries a single valid dated token
- WHEN `openkos ingest <path>` re-ingests the same source with no
  `--event-date` flag
- THEN the regenerated Source's `event_date` is inferred from the file
  name

#### Scenario: A legacy Source with no stored value and no dated file name stays unset

- GIVEN a Source previously ingested before this feature existed, carrying
  no `event_date`, whose raw file name carries no dated token
- WHEN `openkos ingest <path>` re-ingests the same source with no
  `--event-date` flag
- THEN the regenerated Source's frontmatter carries no `event_date` key; it
  is never defaulted to the re-ingest's timestamp

#### Scenario: A malformed stored value is not carried forward and warns

- GIVEN a Source whose on-disk `event_date` value is malformed — for
  example a `datetime` value, or a string that is not a real calendar date
- WHEN `openkos ingest <path>` re-ingests the same source with no
  `--event-date` flag and no dated file name
- THEN the malformed value is not carried forward, exactly one warning is
  printed to stderr naming the malformed value and the Source, and the run
  completes without failing

#### Scenario: A malformed stored value is filled by the flag or the file name

- GIVEN a Source whose on-disk `event_date` value is malformed
- WHEN `openkos ingest <path>` re-ingests the same source with a valid
  `--event-date` flag, or with no flag but a dated file name
- THEN the malformed value is treated as absent, the resolved value (the
  flag's or the inferred one) fills the gap, and the same stderr warning is
  printed

### Requirement: Converged Re-Ingest Source-Only Rewrite

WHEN a re-ingest's Source is otherwise unchanged, already extracted, and
carries an `origin_key` — the case that ordinarily converges and writes
nothing — BUT any of the following differs from what is currently stored:
the resolved `event_date`, the parsed incoming `source_frontmatter`
mapping, the tag union (the on-disk tags unioned with newly lifted tags),
or the resolved `sensitivity` (the on-disk value combined with the
workspace default and any incoming frontmatter sensitivity) — `ingest`
MUST rewrite the Source concept only. It MUST NOT invoke extraction (no
LLM call is made), MUST NOT create, modify, or remove any derived object,
and MUST carry forward the Source's existing extraction markers
(`extraction_status` and `extraction_notice`) unchanged. This rewrite MUST
go through the same preview, confirm gate, drift guard, and autocommit as
any other regenerate. WHEN NONE of `event_date`, `source_frontmatter`, the
tag union, or the resolved `sensitivity` differ from what is currently
stored, the run MUST continue to converge and write nothing, exactly as
`ingest` behaved before this feature existed.

The preview for this rewrite MUST print one additional line naming
`source frontmatter recorded ({n} key(s))`, where `{n}` is the number of
top-level keys in the parsed incoming mapping, WHEN AND ONLY WHEN the
`source_frontmatter` delta is one of the deltas that fired. It MUST print a
second additional line naming `tags added: {a}, {b}`, listing the tags the
union added in order, WHEN AND ONLY WHEN the tag-union delta is one of the
deltas that fired. Each line's presence is independent of the other and of
the `event_date` origin-disclosure line: a rewrite triggered by a single
delta MUST print only that delta's line(s), and a rewrite triggered by
several deltas at once MUST print each fired delta's line. WHEN this
rewrite raises the Source's resolved `sensitivity` (the sensitivity delta
fired), `ingest` MUST additionally print one stderr advisory stating that
existing derived objects keep their own already-stamped sensitivity and
naming `openkos set-sensitivity` (ADR-0009) as the command to raise them
explicitly.
(Previously: named "Converged Re-Ingest Date-Only Rewrite", and triggered
only by a differing resolved `event_date`; a differing `source_frontmatter`,
tag union, or sensitivity did not trigger a rewrite, so a Source ingested
before this change kept none of those values until `--re-extract`. The
rewrite's preview carried no `source frontmatter recorded` or `tags added`
line, and no rewrite ever printed the `set-sensitivity` advisory, because
none of those deltas existed.)

#### Scenario: A differing flag on a converged Source rewrites it with no extraction

- GIVEN a Source that is unchanged, already extracted, carries an
  `origin_key`, and is stored with `event_date: '2026-07-10'`
- WHEN `openkos ingest <path> --event-date 2026-07-14` re-ingests it
- THEN the Source concept is rewritten with `event_date: '2026-07-14'`, no
  LLM call is made, no derived object is created, modified, or removed, and
  the Source's existing extraction markers are carried forward unchanged

#### Scenario: A pre-feature Source with a dated file name gains the date on plain re-ingest

- GIVEN a Source that is unchanged, already extracted, carries an
  `origin_key`, carries no stored `event_date`, and whose raw file name
  carries a single valid dated token
- WHEN `openkos ingest <path>` re-ingests it with no `--event-date` flag
- THEN the Source concept is rewritten with the inferred `event_date`, no
  LLM call is made, and no derived object changes

#### Scenario: A converged Source with no changes writes nothing

- GIVEN a Source that is unchanged, already extracted, carries an
  `origin_key`, and whose stored `event_date`, `source_frontmatter`, tag
  set, and `sensitivity` all already match what this re-ingest would
  resolve
- WHEN `openkos ingest <path>` re-ingests it (with or without
  `--event-date` naming the already-stored value)
- THEN convergence is preserved: nothing is written, exactly as before
  this feature existed

#### Scenario: The Source-only rewrite is idempotent

- GIVEN a converged Source that was just rewritten by a Source-only
  rewrite
- WHEN the same re-ingest command runs again unchanged
- THEN the second run resolves every value as already-stored, and
  convergence writes nothing

#### Scenario: Newly-present incoming frontmatter on an otherwise converged Source triggers a rewrite

- GIVEN a Source ingested before this feature existed — unchanged, already
  extracted, carries an `origin_key`, and stores no `source_frontmatter` —
  whose raw file now carries a leading frontmatter block this run parses
  successfully
- WHEN `openkos ingest <path>` re-ingests it with no flags
- THEN the Source concept is rewritten to carry the parsed
  `source_frontmatter` and any lifted tags, no LLM call is made, and no
  derived object is created, modified, or removed

#### Scenario: A raised sensitivity via incoming frontmatter on an otherwise converged Source triggers a rewrite

- GIVEN a Source that is unchanged, already extracted, carries an
  `origin_key`, and is stored at `private`, whose incoming frontmatter now
  carries `sensitivity: confidential`
- WHEN `openkos ingest <path>` re-ingests it with no flags
- THEN the Source concept is rewritten with `sensitivity: confidential`,
  no LLM call is made, and no derived object changes

#### Scenario: A tag-only difference on an otherwise converged Source triggers a rewrite

- GIVEN a Source that is unchanged, already extracted, carries an
  `origin_key`, and is stored with `tags: [alpha]`, whose incoming
  frontmatter now carries `tags: [beta]`
- WHEN `openkos ingest <path>` re-ingests it with no flags
- THEN the Source concept is rewritten with `tags: [alpha, beta]`, no LLM
  call is made, and no derived object changes

#### Scenario: The preview names the recorded frontmatter when that delta fires

- GIVEN a Source-only rewrite triggered solely by a newly-parsed
  `source_frontmatter` mapping holding 3 top-level keys
- WHEN the preview is shown before Phase B writes
- THEN it includes a line reading `source frontmatter recorded (3 key(s))`,
  and no `tags added` line

#### Scenario: The preview names the added tags when that delta fires

- GIVEN a Source-only rewrite triggered solely by a tag-union delta that
  adds `alpha` and `beta`
- WHEN the preview is shown before Phase B writes
- THEN it includes a line reading `tags added: alpha, beta`, and no
  `source frontmatter recorded` line

#### Scenario: A rewrite triggered only by the event-date delta prints neither new line

- GIVEN a Source-only rewrite triggered solely by a differing resolved
  `event_date`, with no `source_frontmatter` or tag-union delta firing
- WHEN the preview is shown before Phase B writes
- THEN it includes the existing event-date origin-disclosure line and
  neither the `source frontmatter recorded` nor the `tags added` line

#### Scenario: A rewrite triggered by several deltas prints each fired delta's line

- GIVEN a Source-only rewrite triggered by a newly-parsed
  `source_frontmatter` mapping AND a tag-union delta in the same run
- WHEN the preview is shown before Phase B writes
- THEN it includes both the `source frontmatter recorded` line and the
  `tags added` line

#### Scenario: A raised sensitivity on the Source-only rewrite advises set-sensitivity

- GIVEN a Source-only rewrite that raises the Source's resolved
  `sensitivity` via the sensitivity delta
- WHEN `openkos ingest <path>` completes the rewrite
- THEN a stderr advisory states that existing derived objects keep their own
  already-stamped sensitivity and names `openkos set-sensitivity`

#### Scenario: A rewrite that does not raise sensitivity prints no advisory

- GIVEN a Source-only rewrite triggered only by the `source_frontmatter` or
  tag-union delta, with no sensitivity delta firing
- WHEN `openkos ingest <path>` completes the rewrite
- THEN no `set-sensitivity` advisory is printed

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

A successfully validated derived object created in the SAME `ingest` run
MUST also inherit the Source concept's resolved `tags` — the same tags
recorded on the Source actually staged in this run, including any tags
lifted from that Source's incoming frontmatter — as its own `tags` at
creation time. This inheritance applies only to derived objects created in
the same run as the Source; it does NOT re-tag a derived object that
already exists on disk from an earlier run.
(Previously: a derived object's `tags` was always empty; no tag was
inherited from the Source at creation.)

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

#### Scenario: A derived object created in the run inherits the Source's tags

- GIVEN a source whose incoming frontmatter lifts tags onto the Source,
  and successful extraction of one derived object in the same run
- WHEN `openkos ingest <path>` completes
- THEN that derived object's `tags` include the Source's lifted tags

#### Scenario: An existing derived object's tags are unaffected by a later Source tag change

- GIVEN a Source with one existing derived object on disk, and a re-ingest
  whose incoming frontmatter lifts a tag not previously on the Source
- WHEN `openkos ingest <path>` completes
- THEN the existing derived object's file remains byte-unchanged; only a
  derived object newly created in that same run would inherit the new tag

### Requirement: Default Sensitivity from Config

On a FRESH ingest (no prior `bundle/sources/<slug>.md`), the generated
Source concept's `sensitivity` MUST equal
`okf.combine_sensitivity(cfg.default_sensitivity, frontmatter_candidate)`
when the source's incoming frontmatter lifts a `sensitivity` candidate,
and MUST equal `cfg.default_sensitivity` otherwise; no `--sensitivity`
flag is offered in this slice. This is a narrowing of a previously
unconditional guarantee, not new behavior: on a RE-INGEST
(`regenerate=True`), the Source's `sensitivity` MUST instead be resolved
as the high-water mark, via `okf.combine_sensitivity`, of the on-disk
value, `cfg.default_sensitivity`, and any `sensitivity` candidate lifted
from the source's incoming frontmatter for this run — combined in any
order, since `combine_sensitivity` is associative and commutative under
its ranked ordering — and that resolved value MUST be both written to
`concept_path` and passed as `stamp_sensitivity` to derived-object staging,
so re-ingest can only raise or preserve a Source's sensitivity, never
lower it. The only sanctioned downgrade path remains `set-sensitivity
--allow-downgrade`. The extraction gate's `workspace_floor` parameter MUST
equal `okf.combine_sensitivity(cfg.default_sensitivity, frontmatter_candidate)`
when the source's OWN incoming frontmatter for THIS run lifts a
`sensitivity` candidate, and MUST equal `cfg.default_sensitivity` otherwise
— unrelated to the Source's resolved or on-disk value in either case
(`sensitivity-aware-llm` Requirement 4, which gates the separate standalone
`extract` command, is unaffected). Because this floor can only be raised by
the source's own incoming declaration, never lowered, a source whose
incoming frontmatter declares `sensitivity: confidential`, or a value that
is not a member of `SENSITIVITY_ORDER`, MUST cause this run's extraction
call to be blocked (`blocked-by-sensitivity`) exactly as a workspace floor
of `confidential` already blocks it — UNLESS `--include-confidential` is
passed, in which case extraction proceeds unchanged. An incoming
`sensitivity` value LOWER than `cfg.default_sensitivity` MUST NOT lower
this floor. An on-disk `sensitivity` value that is unrecognized or
non-string MUST rank as `confidential` under the existing `_rank`
fallback, so resolution fails closed toward the MORE restrictive level
rather than escalating silently; a missing key or a blank/whitespace-only
string instead ranks as `private` -- the config default floor -- per
`_rank`'s existing behavior, never `confidential`. An incoming frontmatter
`sensitivity` value that is not a member of `SENSITIVITY_ORDER` MUST
likewise rank as `confidential` when folded via `combine_sensitivity`,
exactly as an unrecognized on-disk value does — a malformed VALUE for a
recognized `sensitivity` key is not the same case as a malformed or
non-mapping frontmatter BLOCK, which lifts nothing at all (see "Incoming
Frontmatter Parse Is Fail-Closed And Bounded"). `timestamp`, `description`,
`resource`, `provenance`, and the body MUST continue to refresh exactly as
before this change; only the `sensitivity` field is carried forward, as a
merge into the freshly built metadata, never a restore of the prior
document. WHEN a regenerated Source's resolved `sensitivity` exceeds
`cfg.default_sensitivity`, the re-ingest preview line for that Source MUST
name the preserved level.
(Previously: stated unconditionally that the Source's `sensitivity` equals
`cfg.default_sensitivity`, with no distinction between a fresh ingest and a
re-ingest, so a re-ingest silently reset any level a human had raised via
`set-sensitivity`.)
(Previously: sensitivity resolution folded only the on-disk value and
`cfg.default_sensitivity`; a source's own incoming frontmatter
`sensitivity` value played no part in resolution, on either a fresh
ingest or a re-ingest.)

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

#### Scenario: Extraction gate floor is unrelated to the resolved or on-disk value

- GIVEN a Source whose resolved `sensitivity` differs from
  `cfg.default_sensitivity` after re-ingest resolution, and a source whose
  incoming frontmatter for this run lifts no `sensitivity` candidate
- WHEN extraction's LLM-send gate (`blocks_llm_send`) evaluates whether to
  call the LLM
- THEN it reads `workspace_floor` (`cfg.default_sensitivity`) literally,
  never the Source's resolved or on-disk value

#### Scenario: An incoming confidential declaration blocks this run's extraction call

- GIVEN a workspace with `default_sensitivity: private`, and a source whose
  incoming frontmatter carries `sensitivity: confidential`
- WHEN `openkos ingest <path>` runs without `--include-confidential`
- THEN `workspace_floor` is raised to `confidential` for this run, the
  extraction gate blocks the LLM call (`blocked-by-sensitivity`), and only
  the Source concept is written

#### Scenario: An unrecognized incoming sensitivity also raises the extraction floor

- GIVEN a workspace with `default_sensitivity: private`, and a source whose
  incoming frontmatter carries a `sensitivity` value that is not a member of
  `SENSITIVITY_ORDER`
- WHEN `openkos ingest <path>` runs without `--include-confidential`
- THEN `workspace_floor` folds that value as `confidential`, and the
  extraction gate blocks the LLM call (`blocked-by-sensitivity`)

#### Scenario: --include-confidential still allows the send past a frontmatter-raised floor

- GIVEN the same workspace and source as above
- WHEN `openkos ingest <path> --include-confidential` runs
- THEN the extraction gate does not block the call, and extraction proceeds
  as if the floor had not been raised

#### Scenario: A lower incoming sensitivity does not lower the extraction floor

- GIVEN a workspace with `default_sensitivity: confidential`, and a source
  whose incoming frontmatter carries `sensitivity: public`
- WHEN `openkos ingest <path>` runs
- THEN `workspace_floor` remains `confidential`, and the extraction gate
  blocks the LLM call exactly as it would with no incoming frontmatter at
  all

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

#### Scenario: Fresh ingest also folds an incoming frontmatter sensitivity, raising above the config default

- GIVEN a workspace config with `default_sensitivity: private`, no prior
  `bundle/sources/<slug>.md` for this source, and a source whose incoming
  frontmatter carries `sensitivity: confidential`
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept's `sensitivity` field is `confidential`

#### Scenario: Fresh ingest ignores a lower incoming frontmatter sensitivity

- GIVEN a workspace config with `default_sensitivity: confidential`, no
  prior `bundle/sources/<slug>.md` for this source, and a source whose
  incoming frontmatter carries `sensitivity: public`
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept's `sensitivity` field remains
  `confidential`

#### Scenario: Re-ingest folds the on-disk value, the config default, and an incoming frontmatter sensitivity together

- GIVEN a Source on disk at `private`, config `default_sensitivity:
  private`, and a re-ingest whose incoming frontmatter now carries
  `sensitivity: confidential`
- WHEN `openkos ingest <path>` re-ingests that source
- THEN the Source's `sensitivity` is raised to `confidential`

#### Scenario: An unrecognized incoming frontmatter sensitivity value fails closed to confidential

- GIVEN a source whose incoming frontmatter carries a `sensitivity` value
  that is not a member of `SENSITIVITY_ORDER` (e.g. `sensitivity:
  banana`), and config `default_sensitivity: public`
- WHEN `openkos ingest <path>` completes
- THEN the resolved `sensitivity` is `confidential`

### Requirement: Event-Date Origin Disclosure Line

WHEN a re-ingest or fresh ingest records an `event_date` on the generated
Source concept, `ingest` MUST print exactly one line reporting the
recorded value and its origin — `flag`, `file name`, `frontmatter` (the
value was lifted from the source's incoming `date:` key), or `kept` (the
value was already stored and carried forward unchanged). WHEN an explicit
`--event-date` flag overwrites a differing stored value on re-ingest, that
line MUST name both the old and the new value. WHEN no `event_date` is
recorded at all, `ingest` MUST print no such line.
(Previously: the origin vocabulary was `flag`, `file name`, or `kept`;
`frontmatter` did not exist as an origin.)

#### Scenario: The line names the flag origin

- GIVEN a fresh ingest with `--event-date 2026-07-14`
- WHEN `openkos ingest <path>` completes
- THEN one line is printed naming `2026-07-14` and `flag` as its origin

#### Scenario: The line names the file-name origin

- GIVEN a fresh ingest with no flag and a source file named
  `call-2026-07-14.txt`
- WHEN `openkos ingest <path>` completes
- THEN one line is printed naming `2026-07-14` and `file name` as its
  origin

#### Scenario: The line names the frontmatter origin

- GIVEN a fresh ingest with no flag, no dated file name, and incoming
  frontmatter carrying `date: 2026-07-14`
- WHEN `openkos ingest <path>` completes
- THEN one line is printed naming `2026-07-14` and `frontmatter` as its
  origin

#### Scenario: The line names a carried-forward value as kept

- GIVEN a re-ingest with no flag, whose stored `event_date` is
  `2026-07-14`
- WHEN `openkos ingest <path>` completes
- THEN one line is printed naming `2026-07-14` and `kept` as its origin

#### Scenario: An overwrite names both the old and new value

- GIVEN a re-ingest whose stored `event_date` is `2026-07-14`, run with
  `--event-date 2026-08-01`
- WHEN `openkos ingest <path>` completes
- THEN one line is printed naming the change from `2026-07-14` to
  `2026-08-01`

#### Scenario: No line is printed when no event date is recorded

- GIVEN an ingest with no flag and no dated file name, of a source with no
  stored `event_date`
- WHEN `openkos ingest <path>` completes
- THEN no line reporting an `event_date` is printed
