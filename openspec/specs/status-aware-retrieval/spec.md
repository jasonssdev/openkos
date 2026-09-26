# Status-Aware Retrieval Specification

## Purpose

Concept lifecycle state (`status` frontmatter, `supersedes` edges written by
`reconcile`) currently has no read-side effect: deprecated/superseded
concepts retrieve exactly like live ones. This spec makes lifecycle state
govern visibility across every retrieval input (FTS, vector, graph) and
candidate-load surface (adjudication, contradiction detection), via one
shared effective-status predicate.

## Non-Goals

`forget`/tombstones (S2); sensitivity fail-closed filtering (S3); export
confidential exclusion (S4); anchor-based reconcile conflict detection
(#1619, deferred); down-ranking or partial-visibility strategies (exclusion
only, by product decision); any change to how `status`/`supersedes` are
written.

## Requirements

### Requirement: Effective Status Resolution

The system MUST resolve each concept's effective retrieval status from (1)
its own `status` field and (2) whether it is the TARGET of an inbound
`supersedes` edge authored by a DIFFERENT concept. A concept MUST be treated
as deprecated WHEN its `status` equals `"deprecated"` OR it is targeted by
such an edge. A self-referencing `supersedes` edge (source == target) MUST
NOT mark a concept deprecated. Supersession that forms a cycle is
contradictory and unresolved, so the system fails safe: EVERY concept
targeted by a non-self `supersedes` edge is deprecated — including both
members of a mutual (2-node) cycle and every member of a longer supersedes
cycle of any length.

An inbound `revises` edge (written by `reconcile --revision`) MUST NOT mark
either end deprecated. Deprecation is governed only by the `status` field
and inbound `supersedes` edges as described above; this holds uniformly
across every consumer of the shared effective-status predicate, including
`lifecycle.deprecated_concept_ids` and the `list` STATUS column.
(Previously: this requirement defined deprecation via `status` and
`supersedes` only, with no explicit statement about `revises`; this adds
the explicit non-deprecation guarantee for `revises` as a tested
requirement, since `reconcile --revision` newly writes that edge type.)

#### Scenario: status field alone marks deprecated
- GIVEN a concept with `status: deprecated` and no supersedes edges
- WHEN its effective status is resolved
- THEN it is deprecated

#### Scenario: superseded concept is deprecated regardless of its own status
- GIVEN concept A has an outbound `supersedes` edge targeting concept B,
  and B's own `status` is `"active"`
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
  reports both as `active`
### Requirement: Deprecated Concepts Excluded By Default

By default, retrieval and candidate-generation paths MUST NOT return, rank,
or surface any concept whose effective status is deprecated. This applies
uniformly to FTS hits, vector hits, graph/PPR hits, the fused list feeding
`answer`, and candidate pairs loaded for adjudication and contradiction
detection.

#### Scenario: Deprecated concept absent from a matching query
- GIVEN a deprecated concept whose content matches a question lexically and
  semantically
- WHEN `query`/`answer` runs without `--include-deprecated`
- THEN it is absent from FTS hits, vector hits, graph hits, the fused list,
  and citations

#### Scenario: Superseded concept absent from contradiction candidates
- GIVEN a superseded concept connected to another by a typed graph edge
- WHEN contradiction-detection candidate generation runs
- THEN no candidate pair includes the superseded concept

#### Scenario: Only match is deprecated yields the standard no-match result
- GIVEN the only concept matching a question anywhere (lexically,
  semantically, or via graph proximity) is deprecated
- WHEN `query`/`answer` runs without `--include-deprecated`
- THEN the result is the standard no-match outcome, not an error — this is
  documented, expected behavior

### Requirement: `--include-deprecated` Escape Flag

Retrieval-facing commands MUST offer an opt-in `--include-deprecated` flag
that restores deprecated and superseded concepts to full participation in
results, identical to a live concept.

#### Scenario: Flag restores a deprecated concept
- GIVEN the only-deprecated-match scenario above
- WHEN `query --include-deprecated` runs
- THEN the concept appears in hits, the fused list, and citations

#### Scenario: Flag is opt-in, not the default
- GIVEN a mixed bundle of live and deprecated concepts
- WHEN `query` runs without any flag
- THEN deprecated concepts are excluded

### Requirement: Uniform Enforcement Across All Retrieval Inputs

Exclusion (or inclusion, under the escape flag) MUST be enforced
identically regardless of which input would surface a deprecated concept —
lexical, semantic, or structural — so no single input leaks a deprecated
concept back into the fused result.

#### Scenario: No leak via any single input
- GIVEN a deprecated concept that would rank highly in FTS, vector, AND
  graph retrieval independently
- WHEN `query`/`answer` runs without `--include-deprecated`
- THEN it is absent from the final fused, limit-truncated result

#### Scenario: Live concept reachable only through a deprecated neighbor
- GIVEN live concept C is graph-adjacent only to deprecated concept D
  (D → C)
- WHEN graph retrieval runs
- THEN C may still surface in `graph_hits` on its own merits, while D never
  appears as a hit

### Requirement: Live Retrieval Behavior Is Unchanged

For a bundle with no deprecated or superseded concepts, retrieval, fusion,
and adjudication/contradiction candidate behavior MUST be identical to
current (status-blind) behavior.

#### Scenario: All-live bundle is unaffected
- GIVEN a bundle where every concept's effective status is live
- WHEN `query`/`answer`/`contradictions` run
- THEN results are identical to pre-change behavior
