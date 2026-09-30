# Delta for Status-Aware Retrieval

## MODIFIED Requirements

### Requirement: Effective Status Resolution

The system MUST resolve each concept's effective retrieval status from (1)
its own human-authored `status` field and (2) whether it is the TARGET of
an inbound `supersedes` edge authored by a DIFFERENT concept. A concept MUST
be treated as deprecated WHEN its `status` equals `"deprecated"` without a
valid `status_derived_from` export marker OR it is targeted by such an
edge. A `status: deprecated` carrying a valid export marker is the
engine's own deprecated-status export (`deprecated-status-export`) and MUST
NOT, on its own, mark the concept deprecated: the export is written FROM
this predicate's edge rule and is never read back into it. A self-referencing `supersedes` edge (source == target) MUST
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
(Previously: every `status: deprecated` counted as the concept's own declaration; the engine wrote none, so no export marker existed.)
(Previously: this requirement defined deprecation via `status` and
`supersedes` only, with no explicit statement about `revises`; this adds
the explicit non-deprecation guarantee for `revises` as a tested
requirement, since `reconcile --revision` newly writes that edge type.)
(Previously: the scenarios below and the `list` STATUS column example used
the OKF v0.1 status value `active`; OKF v0.2 writers now emit `stable`, and
every reader treats a legacy `active` identically to `stable` — neither is
deprecated. See `okf-format-migration`'s "Legacy Lifecycle Values Are Not
Deprecated" for the general dual-reader guarantee.)

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
