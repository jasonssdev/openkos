# Status-Aware Retrieval Specification

## Purpose

Concept lifecycle state (`status` frontmatter, `supersedes` edges written by
`reconcile`) governs read-side visibility: deprecated/superseded concepts do
not retrieve like live ones. This spec makes lifecycle state govern
visibility across every retrieval input (FTS, vector) and candidate-load
surface (adjudication, contradiction detection), via one shared
effective-status predicate.

## Non-Goals

`forget`/tombstones (S2); sensitivity fail-closed filtering (S3); export
confidential exclusion (S4); down-ranking or partial-visibility strategies (exclusion
only, by product decision); how `status`/`supersedes` are written (a
marked `status: deprecated` export is defined by `deprecated-status-export`
and is never read back by this predicate).

## Requirements

### Requirement: Effective Status Resolution

The system MUST resolve each concept's effective retrieval status from (1)
its own human-authored `status` field, (2) whether it is the TARGET of an
inbound `supersedes` edge authored by a DIFFERENT concept, and (3) whether it
is a PROVENANCE ORPHAN of a superseded Source. A concept MUST be treated as
deprecated WHEN its `status` equals `"deprecated"` without a valid
`status_derived_from` export marker OR it is targeted by such an edge OR it is
a provenance orphan. A `status: deprecated` carrying a valid export marker is
the engine's own deprecated-status export (`deprecated-status-export`) and
MUST NOT, on its own, mark the concept deprecated: the export is written FROM
this predicate's edge rule and is never read back into it. A self-referencing
`supersedes` edge (source == target) MUST NOT mark a concept deprecated.
Supersession that forms a cycle is contradictory and unresolved, so the
system fails safe: EVERY concept targeted by a non-self `supersedes` edge is
deprecated — including both members of a mutual (2-node) cycle and every
member of a longer supersedes cycle of any length.

A PROVENANCE ORPHAN is a concept, other than a superseded Source itself, whose
`provenance` is non-empty and whose every entry is, directly or through
other provenance orphans, a Source that is the target of a non-self
`supersedes` edge. It MUST be computed with the same orphan closure
`forget --scope source` uses, rooted at the superseded Sources, so a concept
with ANY provenance entry outside that closure, and a concept with empty or
absent provenance, MUST NOT be a provenance orphan. The determination MUST
be derived at read time from `supersedes` edges and `provenance` and MUST
write nothing.

An inbound `revises` edge (written by `reconcile --revision`) MUST NOT mark
either end deprecated. Deprecation is governed only by the `status` field,
inbound `supersedes` edges, and provenance orphanhood as described above;
this holds uniformly across every consumer of the shared effective-status
predicate, including `lifecycle.deprecated_concept_ids` and the `list`
STATUS column.

#### Scenario: status field alone marks deprecated
- GIVEN a concept with `status: deprecated` and no supersedes edges
- WHEN its effective status is resolved
- THEN it is deprecated

#### Scenario: superseded concept is deprecated regardless of its own status
- GIVEN concept A has an outbound `supersedes` edge targeting concept B,
  and B's own `status` is `"stable"` (or a legacy `"active"`, `"draft"`, or
  absent altogether)
- WHEN B's effective status is resolved
- THEN B is deprecated; A remains live

#### Scenario: self-reference stays live, but supersedes cycles fail safe to deprecated
- GIVEN a concept whose `supersedes` edge targets itself
- WHEN its effective status is resolved
- THEN it remains live
- GIVEN concept A supersedes B and B supersedes A (mutual 2-node cycle)
- WHEN their effective status is resolved
- THEN both A and B are deprecated
- GIVEN a longer cycle A supersedes B, B supersedes C, C supersedes A
- WHEN their effective status is resolved
- THEN A, B, and C are all deprecated

#### Scenario: A revises edge deprecates neither end, in retrieval or in list STATUS
- GIVEN concept A holds an outbound `revises` edge targeting concept B
- WHEN the effective status of A and B is resolved by
  `lifecycle.deprecated_concept_ids` and by the `list` STATUS column
- THEN neither A nor B appears in `deprecated_concept_ids`, and `list`
  reports both as `stable`

#### Scenario: An engine export alone does not deprecate
- GIVEN a concept with `status: deprecated` and `status_derived_from:
  supersedes`, and no inbound `supersedes` edge
- WHEN its effective status is resolved
- THEN it is NOT deprecated
- GIVEN the same concept is the target of another concept's `supersedes`
  edge
- WHEN its effective status is resolved
- THEN it is deprecated, by the edge rule

#### Scenario: A concept derived only from a superseded Source is deprecated
- GIVEN Source `sources/v2` supersedes Source `sources/v1`, and concept C
  has `provenance: [sources/v1]` and `status: stable`
- WHEN C's effective status is resolved
- THEN C is deprecated, and `sources/v2` and any concept citing it are live

#### Scenario: A concept that also cites a live Source stays live
- GIVEN `sources/v2` supersedes `sources/v1`, and concept C has
  `provenance: [sources/v1, sources/v2]`
- WHEN C's effective status is resolved
- THEN C is live

#### Scenario: The orphan closure is transitive
- GIVEN C has `provenance: [sources/v1]` and Insight I has `provenance:
  [concepts/c]` only, and `sources/v2` supersedes `sources/v1`
- WHEN I's effective status is resolved
- THEN I is deprecated; an Insight citing `concepts/c` and a live Source is
  live

#### Scenario: Empty provenance is never an orphan
- GIVEN a hand-written concept with no `provenance`, and a superseded Source
  exists
- WHEN its effective status is resolved
- THEN it is live

#### Scenario: Un-superseding restores the concepts, writing nothing
- GIVEN C is deprecated only as a provenance orphan
- WHEN the `supersedes` edge to `sources/v1` is removed with `unrelate`
- THEN C is live again and C's file was never modified by either step

#### Scenario: A superseded non-Source does not propagate
- GIVEN concept D (a `Decision`) is superseded by concept E, and Insight I
  has `provenance: [decisions/d]` only
- WHEN I's effective status is resolved
- THEN I is live (only a superseded Source roots the orphan closure)
### Requirement: Deprecated Concepts Excluded By Default

By default, retrieval and candidate-generation paths MUST NOT return, rank,
or surface any concept whose effective status is deprecated. This applies
uniformly to FTS hits, vector hits, the fused list feeding
`answer`, and candidate pairs loaded for adjudication and contradiction
detection.

A deprecated (superseded) concept's ONLY path back into an answer's prompt
is as a history block attached to the hit that supersedes or revises it,
when `revision_history` is enabled (see `query-answer`'s "Revision History
Is Attached To A Retrieved Concept When Requested"). It MUST NEVER be
counted in `fused_count`, `fts_hit_count`, `dense_hit_count`, or any other
hit-count metadata, and it MUST NEVER be rendered or attributed as a
current answer — its history block is always labelled as an earlier,
non-current version.

#### Scenario: Deprecated concept absent from a matching query
- GIVEN a deprecated concept whose content matches a question lexically and
  semantically
- WHEN `query`/`answer` runs without `--include-deprecated`
- THEN it is absent from FTS hits, vector hits, the fused list,
  and citations

#### Scenario: Superseded concept absent from contradiction candidates
- GIVEN a superseded concept connected to another by a typed graph edge
- WHEN contradiction-detection candidate generation runs
- THEN no candidate pair includes the superseded concept

#### Scenario: Only match is deprecated yields the standard no-match result
- GIVEN the only concept matching a question anywhere (lexically and
  semantically) is deprecated
- WHEN `query`/`answer` runs without `--include-deprecated`
- THEN the result is the standard no-match outcome, not an error — this is
  documented, expected behavior

#### Scenario: A superseded concept reaches the prompt only as an attached history block

- GIVEN a superseded concept P, superseded by a retrieved hit S, and
  `revision_history` is enabled
- WHEN `query`/`answer` runs without `--include-deprecated`
- THEN P is absent from FTS hits, vector hits, the fused list, and every
  hit-count field, and P appears in the prompt or in citations, if at all,
  only as a history block attached to S — never as an ordinary retrieval
  hit, and never labelled as current

### Requirement: `--include-deprecated` Escape Flag

Retrieval-facing commands MUST offer an opt-in `--include-deprecated` flag
that restores deprecated and superseded concepts to full participation in
results, identical to a live concept.

WHEN `--include-deprecated` is passed, the revision-history walk (see
`query-answer`'s "Revision History Is Attached To A Retrieved Concept When
Requested") MUST NOT run at all: no history block is attached to any
successor. Under the flag, a formerly deprecated predecessor is admitted as
an ordinary hit, and attaching it again as a history block would carry a
non-current label that contradicts its restored hit status.

#### Scenario: Flag restores a deprecated concept
- GIVEN the only-deprecated-match scenario above
- WHEN `query --include-deprecated` runs
- THEN the concept appears in hits, the fused list, and citations

#### Scenario: Flag is opt-in, not the default
- GIVEN a mixed bundle of live and deprecated concepts
- WHEN `query` runs without any flag
- THEN deprecated concepts are excluded

#### Scenario: History is suppressed under --include-deprecated

- GIVEN `revision_history` is enabled and `--include-deprecated` is passed
- WHEN `query`/`answer` runs
- THEN the revision-history walk does not run at all: no history block is
  attached to any successor, and every predecessor that is now an ordinary
  hit under the flag carries no history label
### Requirement: Uniform Enforcement Across All Retrieval Inputs

Exclusion (or inclusion, under the escape flag) MUST be enforced
identically regardless of which input would surface a deprecated concept —
lexical or semantic — so no single input leaks a deprecated
concept back into the fused result.

#### Scenario: No leak via any single input
- GIVEN a deprecated concept that would rank highly in FTS AND vector
  retrieval independently
- WHEN `query`/`answer` runs without `--include-deprecated`
- THEN it is absent from the final fused, limit-truncated result

### Requirement: Live Retrieval Behavior Is Unchanged

For a bundle with no deprecated or superseded concepts, retrieval, fusion,
and adjudication/contradiction candidate behavior MUST be identical to
current (status-blind) behavior.

#### Scenario: All-live bundle is unaffected
- GIVEN a bundle where every concept's effective status is live
- WHEN `query`/`answer`/`contradictions` run
- THEN results are identical to pre-change behavior
