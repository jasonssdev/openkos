# Delta for Query Answer

## MODIFIED Requirements

### Requirement: Revision History Block Labels Name The Relation, Edge Holder, And Event Date

Each attached predecessor MUST be rendered as its own separately numbered
context block — never spliced into the successor's body — with a label of
the exact shape: `(earlier version, superseded|refined by concept_id: <H>;
<date phrase>; no longer current|still current)`.

`<H>` MUST be the concept id of the EDGE HOLDER that reached this
predecessor — the concept whose own outbound `supersedes` or `revises` edge
the walk followed to attach it — and NOT necessarily the top-level retrieved
successor. For a predecessor at depth 1, the holder IS the retrieved
successor. For a predecessor reached at depth 2 or deeper, the holder is the
intermediate predecessor whose edge reached it, not the successor at the top
of the chain.

The relation MUST render as `superseded` for a `supersedes` edge and
`refined` for a `revises` edge.

The currency clause MUST render as `no longer current` for a `supersedes`
edge — a `supersedes` target is always deprecated. For a `revises` edge, the
currency clause MUST render as `still current` UNLESS that predecessor is
ITSELF, independently of this edge, in the bundle's deprecated set (i.e. it
is superseded elsewhere), in which case it MUST render as `no longer
current`. Currency is decided by the predecessor's own deprecated-set
membership, never by the traversing edge's role alone.

`<date phrase>` MUST be one of: `event date <D>` when the predecessor's
event-date resolution is unambiguous; `event dates <D1> to <D2>` (earliest
to latest, ISO) when multiple distinct dates were found across the chain
member's provenance; or `event date unknown` when resolution is missing or
unreached. The label MUST NEVER substitute the concept's generation time
(`generated.at`, or a legacy `timestamp` on an unmigrated document) for an
unresolved event date. An event date is resolved by reading only the
predecessor's own already-admitted `provenance:` entries and, at most one
hop further, the `provenance:` of any non-Source entry among them; it MUST
NOT depend on any read outside that bound.
(Previously: this requirement said the label "MUST NEVER substitute the
concept's ingest timestamp for an unresolved event date," naming only the
OKF v0.1 `timestamp` field; OKF v0.2 records that value as `generated.at`,
with legacy `timestamp` still read on an unmigrated document, and the
prohibition covers both.)

The successor's own label MUST remain byte-identical to its non-history
label; the history relationship is carried only on the predecessor's block.
Adding history blocks MUST NOT change the system prompt text.

#### Scenario: A depth-1 superseded predecessor's label names the retrieved successor as holder

- GIVEN a predecessor P superseded by retrieved successor S, with a single
  resolved event date
- WHEN `answer(...)` renders P's history block
- THEN its label reads `(earlier version, superseded by concept_id: S;
  event date <D>; no longer current)`, naming S because P sits at depth 1

#### Scenario: A depth-2 predecessor's label names its immediate holder, not the top-level successor

- GIVEN retrieved successor S supersedes P, and P separately revises Q, with
  `revision_history` enabled
- WHEN `answer(...)` renders Q's history block
- THEN Q's label names P's concept id as the holder (`refined by
  concept_id: P`), not S's

#### Scenario: A refined predecessor not deprecated elsewhere states it is still current

- GIVEN a predecessor P revised by successor S, and P is not itself in the
  bundle's deprecated set
- WHEN `answer(...)` renders P's history block
- THEN its label names the `revises` relation, S's concept id as holder, and
  states P is still current

#### Scenario: A refined predecessor that is also deprecated elsewhere states it is no longer current

- GIVEN a predecessor P revised by successor S, and P is separately
  superseded by some other concept in the bundle, placing P in the
  deprecated set
- WHEN `answer(...)` renders P's history block
- THEN its label names the `revises` relation but states P is no longer
  current, even though the edge that reached it was `revises`

#### Scenario: An unresolved date renders as unknown, never ingest time

- GIVEN a predecessor whose event-date resolution is missing or unreached
- WHEN `answer(...)` renders its history block label
- THEN the label reads `event date unknown`, and never substitutes that
  predecessor's `generated.at` (or legacy `timestamp`) value

#### Scenario: A confidential Source's date resolves as unknown, without excluding the predecessor's own block

- GIVEN a predecessor P whose `provenance:` names only a Source that the
  send-time sensitivity gate would refuse to admit (for example
  confidential, with `include_confidential` off), while P's own frontmatter
  and body pass their own guards
- WHEN `answer(...)` resolves P's event date
- THEN the date resolution reads as missing and P's label shows
  `event date unknown` — the Source's refusal affects only the date lookup,
  and P's own history block is still attached

#### Scenario: Multiple distinct dates render as an earliest-to-latest range

- GIVEN a predecessor whose provenance resolves to more than one distinct
  event date
- WHEN `answer(...)` renders its history block label
- THEN the label reads `event dates <earliest> to <latest>`

#### Scenario: The successor's own label is unaffected

- GIVEN a successor with one or more attached history blocks
- WHEN `answer(...)` renders the successor's own context block label
- THEN it is byte-identical to the label it would carry with
  `revision_history` disabled
