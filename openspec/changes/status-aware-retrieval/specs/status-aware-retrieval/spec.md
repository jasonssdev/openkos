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
NOT mark a concept deprecated. WHEN two concepts mutually supersede each
other, both MUST be treated as live.

#### Scenario: status field alone marks deprecated
- GIVEN a concept with `status: deprecated` and no supersedes edges
- WHEN its effective status is resolved
- THEN it is deprecated

#### Scenario: superseded concept is deprecated regardless of its own status
- GIVEN concept A has an outbound `supersedes` edge targeting concept B,
  and B's own `status` is `"active"`
- WHEN B's effective status is resolved
- THEN B is deprecated; A remains live

#### Scenario: self-reference and cycles are guarded to live
- GIVEN a concept whose `supersedes` edge targets itself
- WHEN its effective status is resolved
- THEN it remains live
- GIVEN concept A supersedes B and B supersedes A (mutual cycle)
- WHEN their effective status is resolved
- THEN both remain live

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
