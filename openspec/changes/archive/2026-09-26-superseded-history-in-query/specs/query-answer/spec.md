# Delta for Query Answer

## ADDED Requirements

### Requirement: Revision History Is Attached To A Retrieved Concept When Requested

WHEN `revision_history` is enabled, `_assemble_context` MUST, for each
successor concept placed in context, walk that concept's outbound
`supersedes` and `revises` edges — read from its already-loaded frontmatter
via `okf.decode_relations` — breadth-first to attach its predecessors as
separate history blocks. The walk MUST NOT read the bundle-wide graph and
MUST NOT perform any full-bundle scan; it re-reads only the chain members it
visits.

The walk MUST be bounded: depth 3 hops from the successor, a `visited` set
guarding against cycles, and at most 3 history blocks attached per
successor. Truncation MUST be disclosed, via
`AnswerResult.history_truncated_titles` (see "AnswerResult Carries Retrieval
Metadata"), whenever either bound is what stops a reachable predecessor from
being shown: WHEN a successor's reachable-predecessor set within depth 3
exceeds 3, `answer` MUST select the first 3 in the deterministic order below
and disclose the truncation; WHEN a node reached exactly at depth 3 itself
has an outbound `supersedes`/`revises` edge whose target was not already
visited or skipped, `answer` MUST also disclose the truncation, even though
that depth-3 node's own block IS attached. Truncation MUST be determined
without any additional read beyond the edges already present in the
frontmatter of nodes already visited.

At each node, targets MUST be visited in a deterministic order: `supersedes`
edges before `revises` edges, and within the same relation, ascending by
concept id. This order MUST be identical across repeated calls against the
same bundle state.

WHEN `revision_history` is `False` (the default), `answer` MUST perform zero
additional reads beyond the existing per-hit re-read, and the assembled
context — and therefore the prompt sent to the LLM — MUST be byte-identical
to the pre-history-feature behavior.

#### Scenario: A supersedes predecessor is attached as a history block

- GIVEN `revision_history` is enabled and a retrieved successor concept S
  holds an outbound `supersedes` edge to predecessor P
- WHEN `answer(...)` assembles context for S
- THEN P is re-read and attached as its own separately numbered history
  block

#### Scenario: A 4-long chain stops at depth 3 and a cycle terminates

- GIVEN a chain of 4 successive `supersedes` edges from the successor, and
  separately a bundle where two concepts `supersedes` each other
- WHEN `answer(...)` walks each with `revision_history` enabled
- THEN the 4-long chain attaches history only through depth 3 and discloses
  the successor's truncation via `history_truncated_titles`, while the
  mutual-cycle walk terminates via the `visited` guard rather than looping

#### Scenario: More than 3 predecessors truncates deterministically

- GIVEN a successor with more than 3 reachable predecessors within depth 3
- WHEN `answer(...)` attaches history blocks
- THEN exactly 3 are attached, chosen by the `supersedes`-before-`revises`,
  then-concept-id order, and the truncation is disclosed via
  `history_truncated_titles`

#### Scenario: Disabled by default makes zero extra reads

- GIVEN `revision_history` is not passed (defaults to `False`)
- WHEN `answer(...)` is called against a bundle containing `supersedes` and
  `revises` chains
- THEN no predecessor is re-read, no history block is attached, and the
  assembled context is byte-identical to the pre-history-feature behavior

### Requirement: Revision History Deduplication And Send-Time Guards

A `revises` predecessor that already appears as an ordinary hit in the same
`answer()` call MUST NOT be attached again as a history block. A
predecessor reachable from more than one successor in the fused list MUST
be attached exactly once, to the first successor that reaches it in fused
order.

Every existing send-time guard that a hit must pass MUST also gate a
predecessor re-read before it is attached: the `blocked` concept-id set, the
`sensitivity.should_block` re-check, and the skip-on-unreadable rule. A
predecessor that fails any of these guards MUST be omitted from the history
blocks without raising, exactly as a hit failing the same guard is omitted
from context. A refused predecessor MUST be a dead end: the walk MUST NOT
read or enqueue that node's own outbound `supersedes`/`revises` edges, so no
later history label can ever name a node that was never admitted.

#### Scenario: A revises predecessor already a hit is not repeated

- GIVEN predecessor P is both an ordinary fused hit and reachable from
  successor S via a `revises` edge, with `revision_history` enabled
- WHEN `answer(...)` assembles context
- THEN P appears exactly once, as its ordinary hit block, and is not also
  attached as a history block

#### Scenario: A predecessor shared by two successors is attached once

- GIVEN predecessor P is reachable from both successor S1 and successor S2,
  which both appear in the fused list with S1 ranked before S2
- WHEN `answer(...)` attaches history with `revision_history` enabled
- THEN P is attached exactly once, as history under S1

#### Scenario: A confidential predecessor is excluded like a hit

- GIVEN a predecessor whose freshly re-read frontmatter marks it
  confidential, and the sending guard would block a hit with that mark
- WHEN `answer(...)` attaches history with `revision_history` enabled
- THEN that predecessor is omitted from history blocks and from context,
  exactly as a confidential hit would be

#### Scenario: An unreadable predecessor is skipped without raising

- GIVEN a predecessor file referenced by a `supersedes`/`revises` edge is
  missing or has unparsable frontmatter
- WHEN `answer(...)` attaches history with `revision_history` enabled
- THEN that predecessor is silently omitted and no exception propagates

#### Scenario: A refused predecessor is a dead end, never traversed

- GIVEN a predecessor P fails a send-time guard (blocked, confidential, or
  unreadable), and P itself holds an outbound `supersedes` edge to another
  predecessor Q
- WHEN `answer(...)` walks with `revision_history` enabled
- THEN Q is never read and never attached — the walk does not follow P's
  edges because P was never admitted

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
unreached. The label MUST NEVER substitute the concept's ingest timestamp
for an unresolved event date. An event date is resolved by reading only the
predecessor's own already-admitted `provenance:` entries and, at most one
hop further, the `provenance:` of any non-Source entry among them; it MUST
NOT depend on any read outside that bound.

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
  predecessor's ingest timestamp

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

### Requirement: Revision History Budget Is A Nested Split Of The Successor's Share Plus Unspent Budget

WHEN one or more history blocks are attached to a successor, the character
budget MUST be allocated in two stages:

1. **Outer stage (unchanged).** The share computation MUST first compute
   each hit block's own share exactly as it does without history, over the
   same hit sizes and the same total budget as today (`fair_shares` over the
   hit sizes). An unrelated hit's share, and therefore its bounded body,
   MUST be byte-identical to what it would be without the history feature —
   the cost of history is never charged to a hit that does not carry it.
2. **Slack stage.** Whatever budget the outer stage leaves unspent (the
   total budget minus the sum of outer shares) MUST be made available, via a
   further fair split across every successor carrying history, IN ADDITION
   to that successor's own outer share. A successor's nested pool for
   `[successor, *its history blocks]` is its own outer share PLUS its fair
   slice of this unspent budget, minus the frame overhead its history adds
   to the prompt.

Within a successor's nested pool, the split across `[successor, *its
history blocks]` MUST use a fair split (`prompt_budget.nested_shares`). This
split MUST NEVER reduce the successor's own body to zero, to make room for
its history, when the successor's outer share was non-zero. WHEN the nested
pool cannot give the successor a non-zero share under this constraint, ALL
of that successor's history for this pool MUST be dropped instead, and the
successor's share reverts to its unchanged outer share. A dropped group's
history blocks MUST NOT appear in the prompt or in citations.

The existing excerpt-and-omission disclosure (issue #882) MUST apply
unchanged to a history block within a group that is not dropped as a whole:
a history block that must be excerpted to fit its sub-share MUST be marked
partial exactly like a hit, and a history block left zero room within an
otherwise kept group MUST be dropped from the prompt and disclosed as
omitted, exactly like a hit. Every disclosed history title — excerpted,
omitted individually, or omitted because its whole group was dropped — MUST
carry the suffix ` (earlier version)`.

Across every hit and history body combined, the total spent MUST NEVER push
`len(user_content) + max(system prompt lengths)` above the bound that today's
plan already allows, computed over the same hit labels and the same budget.

#### Scenario: Adding history to one successor leaves other hits' bodies unchanged

- GIVEN four hits, one of which is a successor with attached history blocks
- WHEN the shared budget is distributed
- THEN the three unrelated hits' bounded bodies are byte-identical to what
  they would be without history, and the successor's own nested pool is its
  outer share plus a slice of unspent budget

#### Scenario: Unspent budget lets a small history block fit without excerpting

- GIVEN a window where every hit's outer share already comfortably fits its
  body, leaving unspent budget, and one successor carries one small history
  block
- WHEN `answer(...)` assembles the prompt
- THEN that history block is sent in full, unexcerpted, funded from the
  unspent budget rather than carved out of the successor's own outer share

#### Scenario: A fully-spent window still splits within the successor's own share

- GIVEN a window where every hit's outer share is fully spent, leaving no
  unspent budget
- WHEN a successor carries history
- THEN its nested pool equals exactly its own outer share minus the history
  frame overhead, split across `[successor, *history]`

#### Scenario: An oversized history block is excerpted, not dropped

- GIVEN a history block whose body exceeds its computed sub-share, within a
  group that is not dropped as a whole
- WHEN `answer(...)` assembles the prompt
- THEN it receives an even-coverage excerpt of its sub-share, its citation
  is marked partial, and its title carries the ` (earlier version)` suffix

#### Scenario: A zero-share history block within a kept group is dropped and disclosed as omitted

- GIVEN a history block whose sub-share of the successor's nested split
  leaves it zero characters, while the successor itself keeps a non-zero
  share
- WHEN `answer(...)` assembles the prompt
- THEN it is dropped from the prompt and from citations, and its title,
  suffixed ` (earlier version)`, is reported as omitted exactly as a
  zero-share hit would be

#### Scenario: A history group that would cost its successor its whole body is dropped entirely instead

- GIVEN a successor whose nested pool, if split across itself and its
  history, would leave the successor's own share at zero even though its
  outer share was non-zero
- WHEN `answer(...)` assembles the prompt
- THEN none of that successor's history blocks are sent, the successor's
  share reverts to its unchanged outer share, and every dropped history
  title is disclosed as omitted, suffixed ` (earlier version)`

### Requirement: Citation.history Marks An Attributed Revision History Block

`Citation` MUST gain a field `history: Literal["superseded", "refined"] |
None = None`, mirroring the shape of `excerpted` and `confidential`. It
MUST be `"superseded"` for a citation produced from a `supersedes` history
block, `"refined"` for one produced from a `revises` history block, and
`None` for every ordinary hit citation.

`_split_attribution` and the issue #753 subset rule MUST apply to history
citations unchanged: a history block is cited only when the model's
attribution line names its block number, `citations` remains a subset of
the sent blocks narrowed by what the reply reports using, and the
`"absent"`/`"unparsed"` fallback (every context-included block, hit or
history) applies identically to history blocks.

#### Scenario: A reported attribution naming a history block sets history

- GIVEN a superseded history block sent as block N, and the reply's
  attribution line names block N
- WHEN `answer(...)` returns
- THEN `citations` includes a `Citation` for that predecessor with
  `history="superseded"`

#### Scenario: An ordinary hit citation always carries history=None

- GIVEN an ordinary hit with no revision history attached
- WHEN `answer(...)` returns
- THEN its `Citation.history` is `None`

#### Scenario: Absent attribution keeps every included block, hit or history

- GIVEN a reply with no attribution line, and context including both hit
  and history blocks
- WHEN `answer(...)` returns
- THEN `citations` holds one `Citation` per context-included block —
  ordinary hits with `history=None` and history blocks with their relation
  — and `attribution` is `"absent"`

## MODIFIED Requirements

### Requirement: AnswerResult Carries Retrieval Metadata

`AnswerResult` MUST carry: `fts_hit_count` (int, raw `FtsIndex.search` hit
count before guarded re-read filtering), `llm_invoked` (bool),
`no_match_cause` (`NoMatchCause = Literal["none", "empty_query", "zero_hits",
"all_unreadable"]`, `"none"` on a successful answer, else whichever guard
tripped), and `skip_notices` (`list[str]`, copied from `FtsIndex.skipped` for
that build) — UNCHANGED from the existing contract. `AnswerResult` MUST
additionally, and PURELY ADDITIVELY, carry: `dense_hit_count` (int, raw
`vector_store.query` hit count), `fused_count` (int, number of distinct
`concept_id`s in the FINAL fused, limit-truncated list), and
`dense_degraded` (bool). `AnswerResult` MUST NOT carry any graph metadata:
`graph_hit_count`, `graph_degraded`, and `graph_contributed_count` MUST all
be absent. The module MUST remain config-free.

WHEN `revision_history` is enabled, `context_block_count` MUST count every
context block actually sent to the model, INCLUDING attached history
blocks; `fused_count`, `fts_hit_count`, and `dense_hit_count` MUST continue
to count only ordinary hits and MUST NOT include history blocks.
`AnswerResult` MUST additionally carry `history_truncated_titles: list[str]
= field(default_factory=list)`, holding the TITLE of every retrieved
successor whose OWN attached-history chain was cut short — whether by the
per-successor block cap, by the depth bound, or by both — listed in fused
order. A successor whose full reachable chain within the depth bound was
shown in full MUST NOT appear in this list. WHEN `revision_history` is
disabled (the default), `history_truncated_titles` MUST be `[]`, and
`context_block_count` MUST be unaffected by this requirement.
(Previously: those three fields existed — `graph_hit_count` the raw
personalized-PageRank candidate pool, `graph_degraded` whether the graph
stage could run, and `graph_contributed_count` how many reserved slots the
graph filled with concepts absent from the FTS+dense pool. All three
described a channel issue #434 removed; a field that could only ever report
zero would read as a channel that contributed nothing, rather than one that
is not there.)
(Previously: `context_block_count` counted only ordinary hit blocks, and
there was no `history_truncated_titles` field, because history blocks did
not exist.)

#### Scenario: Successful answer sets success metadata

- GIVEN a question with readable, matching hits
- WHEN `answer(...)` returns a non-`NO_MATCH` answer
- THEN `llm_invoked` is `True` and `no_match_cause` is `"none"`

#### Scenario: AnswerResult reports no graph metadata

- GIVEN the `AnswerResult` dataclass
- WHEN its fields are inspected
- THEN `graph_hit_count`, `graph_degraded`, and `graph_contributed_count`
  are all absent

#### Scenario: context_block_count includes attached history blocks

- GIVEN `revision_history` is enabled and a successor with 2 attached
  history blocks among otherwise ordinary hits
- WHEN `answer(...)` returns
- THEN `context_block_count` equals the hit-block count plus 2, while
  `fused_count`, `fts_hit_count`, and `dense_hit_count` are unaffected by
  the attached history

#### Scenario: The truncation signal names the capped successor's title

- GIVEN a successor S, titled "T", whose reachable history within depth 3
  exceeds 3 predecessors, with `revision_history` enabled
- WHEN `answer(...)` returns
- THEN `history_truncated_titles` contains "T"

#### Scenario: A chain cut by the depth bound also sets the truncation signal

- GIVEN a successor S, titled "T", whose `supersedes`/`revises` chain
  continues past depth 3 with no other truncation cause
- WHEN `answer(...)` returns with `revision_history` enabled
- THEN `history_truncated_titles` contains "T", even though fewer than 3
  history blocks were attached to S

#### Scenario: Disabled by default reports no truncation

- GIVEN `revision_history` is `False` (the default)
- WHEN `answer(...)` returns
- THEN `history_truncated_titles` is `[]`, and `context_block_count` is
  unaffected by this requirement

### Requirement: Module Is Config-Free And Backend-Injected

`retrieval/answer.py` MUST NOT import `openkos.config`. `LLMBackend`,
`Embedder`, `VectorStore`, and `fts_index` instances MUST all be supplied
by the caller; the module MUST NOT construct, open, or select any of them
itself.

`answer()` MUST additionally accept a keyword-only `revision_history: bool
= False` parameter controlling whether it walks and attaches revision
history. This parameter MUST NOT be sourced from `openkos.config` inside
the module — the caller (`run_query`) resolves it from configuration and
passes it explicitly, keeping the module itself config-free.
(Previously: `LLMBackend`, `Embedder`, and `VectorStore` were caller-injected;
`fts_index` and `graph_index` did not exist as parameters — the module built
its own FTS index and graph internally. Previously: `graph_index` was a
fourth injected handle; issue #434 removed the stage that read it.)
(Previously: `answer()` had no `revision_history` parameter.)

#### Scenario: Module has no config dependency

- GIVEN a static import check of `retrieval/answer.py`
- WHEN its imports are inspected
- THEN `openkos.config` is absent, and the only sources of `LLMBackend`,
  `Embedder`, `VectorStore`, and `fts_index` are the parameters passed by
  the caller

#### Scenario: revision_history is caller-supplied, not config-read

- GIVEN a static import check of `retrieval/answer.py` and the signature of
  `answer()`
- WHEN both are inspected
- THEN `openkos.config` is absent, and `revision_history` is a
  keyword-only parameter with no internal config read
