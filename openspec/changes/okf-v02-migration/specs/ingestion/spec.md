# Delta for Ingestion

## ADDED Requirements

### Requirement: `sources` Is A Generated, One-Way Projection Of `provenance`

At the single frontmatter write point in `model/okf.py`, every written
Source and derived concept's `sources` frontmatter list MUST be generated
from that concept's `provenance` list at write time — one `sources` entry
per `provenance` path, with `resource` set to the bundle-relative Source
path and `id`/`title` populated where known. `provenance` MUST remain the
sole internal source of truth for trust, sensitivity, and merge decisions;
no engine code outside this single projection point MUST read the
`sources` key back as an input to any of those decisions.

#### Scenario: `sources` matches the projection of `provenance`

- GIVEN any concept written by `ingest`
- WHEN its frontmatter is inspected
- THEN its `sources` list is exactly the projection of its `provenance`
  list, one entry per provenance path

#### Scenario: No module outside the projection reads `sources` back

- GIVEN the `openkos` source tree
- WHEN every module outside `model/okf.py`'s projection point is inspected
  for reads of the `sources` frontmatter key
- THEN none of them read `sources` as an input to sensitivity, trust, or
  merge logic

## RENAMED Requirements

### Requirement: OKF §9 Conformance — Reserved File Structure (Rule 3) → OKF §11 Conformance — Reserved File Structure (Rule 3)

(Reason: OKF v0.2 renumbers the conformance section from §9 to §11.)
(Migration: the body changes too; see the MODIFIED requirement of the same new name.)

### Requirement: index.md Frontmatter Conformance (§6 + §11 Root Exception) → index.md Frontmatter Conformance (§8 + §12 Root Exception)

(Reason: OKF v0.2 renumbers index files from §6 to §8 and versioning from §11 to §12.)
(Migration: the body changes too; see the MODIFIED requirement of the same new name.)

### Requirement: Reference Bundle Full §9 Conformance → Reference Bundle Full §11 Conformance

(Reason: OKF v0.2 renumbers the conformance section from §9 to §11.)
(Migration: the body changes too; see the MODIFIED requirement of the same new name.)

### Requirement: OKF §9 Conformance — `relations:` Field Shape → OKF §11 Conformance — `relations:` Field Shape

(Reason: OKF v0.2 renumbers the conformance section from §9 to §11; this requirement's body names no other v0.1 section number, so only the title changes.)
(Migration: none — the checked behavior is unchanged.)

### Requirement: log.md ISO-8601 Date Heading Conformance (§7) → log.md ISO-8601 Date Heading Conformance (§9)

(Reason: OKF v0.2 renumbers the "Log files" section from v0.1's §7 to §9; this requirement's body names no section number, so only the title changes.)
(Migration: none — the checked behavior is unchanged.)

## MODIFIED Requirements

### Requirement: Ingest Raw Copy and Source Concept Generation

`openkos ingest <path>` MUST copy the raw source into the bundle's raw
storage as an exclusive (create-only) binary write and generate exactly one
OKF Source concept with frontmatter `type`, `title`, `description`,
`resource`, `tags`, `generated: { by: openkos/<version>, at: <ISO-8601 Z> }`,
`status: stable`, and `sources` (see "`sources` Is A Generated, One-Way
Projection Of `provenance`" above), plus OpenKOS-layer `version`,
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
`---` line exists later in the file; otherwise it is ordinary content.
WHEN `raw_content` could not be decoded as UTF-8, or is blank or
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

### Requirement: OKF §11 Conformance — Reserved File Structure (Rule 3)

`check_conformance` MUST enforce OKF §11 rule 3 (reserved-file structure) in
addition to rules 1-2, via an additive walk over `index.md` and `log.md`
files that MUST NOT alter the existing rule 1-2 walk (`_iter_docs`) or its
output. `check_conformance` MUST continue to return `list[str]` violation
messages in the existing `f"{path}: {message}"` shape; rule-3 violations
MUST be appended to the same list as rules 1-2. Rule 3 covers exactly the two
structural checks below; validating an `index.md`'s body shape
(heading/bullet structure per §8) is explicitly OUT OF SCOPE for this
requirement, as is any change to the freshness/orphan lint.
(Previously: this requirement cited OKF §9, the v0.1 conformance section
number, and §6 for index-file body shape; OKF v0.2 renumbers these to §11
and §8 respectively, with no change in behavior.)

#### Scenario: Reserved-file walk does not perturb rules 1-2

- GIVEN a bundle previously evaluated under rules 1-2 only
- WHEN `check_conformance` runs after rule 3 is added
- THEN the rule 1-2 portion of the violation list is byte-identical to
  before

### Requirement: index.md Frontmatter Conformance (§8 + §12 Root Exception)

For every `index.md` in the bundle tree, `check_conformance` MUST treat a
frontmatter FENCE (opening `---` delimiter with a closing `---`, whether or
not its YAML parses) as a violation UNLESS the file is the bundle-root
`index.md` (`path.parent == bundle_dir`), where §12 permits an `okf_version:
"0.2"` frontmatter block as the sole exception.
(Previously: titled "index.md Frontmatter Conformance (§6 + §11 Root
Exception)", citing OKF v0.1's §6 (index files) and §11 (versioning), and
the permitted root-exception value was `okf_version: "0.1"`; OKF v0.2
renumbers these sections to §8 and §12 respectively, and the exception
value is now `"0.2"`.)

#### Scenario: Root index.md with okf_version frontmatter passes

- GIVEN a bundle-root `index.md` containing `okf_version: "0.2"`
  frontmatter
- WHEN `check_conformance` runs
- THEN no violation is reported for that file

#### Scenario: Non-root index.md with frontmatter is a violation

- GIVEN an `index.md` at any depth other than the bundle root, containing a
  frontmatter FENCE (opening and closing `---` delimiters)
- WHEN `check_conformance` runs
- THEN a violation naming that file's path is reported

### Requirement: Reference Bundle Full §11 Conformance

The reference bundle at `examples/good-life-demo/bundle` MUST pass
`check_conformance` with an empty violation list under all three §11 rules,
asserted by a test that runs in CI's existing `test` job with no CI
configuration changes required.
(Previously: titled "Reference Bundle Full §9 Conformance", citing OKF v0.1's
§9 conformance section number; OKF v0.2 renumbers conformance to §11 with no
change in the checked behavior.)

#### Scenario: Reference bundle passes all three rules

- GIVEN the bundle at `examples/good-life-demo/bundle`
- WHEN `check_conformance` runs against it
- THEN it returns an empty list

### Requirement: Source Event Date Frontmatter Key

`ingest` MUST support an optional Source frontmatter key, `event_date`,
holding a strict `YYYY-MM-DD` calendar date naming when the recorded event
happened — distinct from `generated.at` (or a legacy `timestamp` on a
pre-v0.2 or unmigrated document), which continues to record when the
Source's content was last generated, unconditionally set at ingest time.
`event_date` is an OKF §4.1 extension key: `ingest` MUST emit it on the
generated Source concept ONLY when a value is available (from the
`--event-date` flag, file-name inference, or carry-forward on re-ingest); a
Source ingested with no such evidence MUST omit the key entirely, producing
output byte-identical to `ingest`'s behavior before this key existed.
`event_date` MUST NEVER be defaulted to ingest time or any other derived
value — "no evidence" and "unknown" MUST be represented by the key's
absence, never by a stand-in value. WHEN `ingest` writes the key, it MUST
emit a quoted ISO-8601 date string (`event_date:
'2026-07-14'`), never a bare, unquoted date scalar.
(Previously: contrasted `event_date` with `timestamp` alone; OKF v0.2
supersedes `timestamp` with `generated.at` for fresh writes, with legacy
`timestamp` still read on unmigrated documents.)

#### Scenario: No flag and no dated file name omits the key

- GIVEN an initialized workspace and a source file whose name carries no
  `YYYY-MM-DD` token, ingested with no `--event-date` flag
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept's frontmatter contains no `event_date`
  key, and the document is otherwise byte-identical to `ingest`'s output
  before this key existed

#### Scenario: event_date is never the ingest timestamp

- GIVEN a source ingested with no `--event-date` flag and no dated file
  name
- WHEN the generated Source concept's frontmatter is inspected
- THEN its `generated.at` reflects the ingest time as before, and no
  `event_date` key exists carrying that same or any other derived value

#### Scenario: Derived objects never carry an event date

- GIVEN a source ingested with a resolved `event_date`, whose extraction
  yields one or more derived objects
- WHEN the derived objects' frontmatter is inspected
- THEN none of them carry an `event_date` key; the value exists only on
  the Source concept, reachable from a derived object through its
  `provenance`
