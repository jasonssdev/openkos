# Candidate Edge Seeding Specification

## Purpose

`candidate-edge-seeding` is the deterministic, config-free module that
scores embedding-proximity candidate concept-to-concept edges, consumed
by the `graph-projection` third pass and, transitively, by
`suggest-relations` and `contradictions`. It mirrors the architecture of
`resolution/candidates.py` + `resolution/similarity.py`: ephemeral
dataclass output, no LLM call, no persisted-schema change.

## Non-Goals

This spec does not define: LLM-emitted links at extraction time; durable
persistence of candidate edges to `relations:` frontmatter; the
`graph-projection` third-pass wiring itself (see the `graph-projection`
delta); or the concrete row-typing representation of a candidate edge
(`relation_type = NULL` vs. a new synthesized type) — either
representation is acceptable so long as the observable behavior below
holds.

## Requirements

### Requirement: Deterministic, Config-Free Candidate Scoring

The system MUST provide a scoring function that, given a concept's
embedding and the set of other concepts' embeddings (via `vectorstore`'s
k-NN `query`), returns candidate target concepts ordered by ascending
distance, filtered to a fixed distance/similarity cutoff and bounded to a
fixed top-K. The function MUST take no workspace-specific configuration
and MUST be pure with respect to its embedding inputs: identical inputs
MUST produce identical, identically-ordered output.

#### Scenario: Deterministic ordering for fixed inputs

- GIVEN a fixed concept embedding and a fixed set of candidate
  embeddings
- WHEN the scoring function runs twice
- THEN both runs return the same candidate list in the same order

#### Scenario: Cutoff and top-K are respected

- GIVEN candidate embeddings, some within the cutoff and some beyond it,
  exceeding top-K in count
- WHEN the scoring function runs
- THEN only candidates within the cutoff are returned, capped at top-K

### Requirement: Candidate Edges Reach Suggestion And Are Excluded From Contradiction Candidates

Regardless of the internal row-typing representation chosen at design
time, a candidate edge produced by this module MUST be observable by
`suggest-relations` as a suggestion candidate, and MUST NOT be
observable by `contradictions` as a contradiction candidate pair — the
same treatment `derived_from` provenance-mirror edges already receive.

#### Scenario: Candidate edge appears in suggest-relations

- GIVEN a candidate edge produced by this module between two concepts
- WHEN `suggest-relations` runs
- THEN that pair appears as a suggestion candidate

#### Scenario: Candidate edge is excluded from contradiction candidates

- GIVEN a candidate edge produced by this module between two concepts,
  and no other typed edge between them
- WHEN `contradictions` runs
- THEN that pair is NOT included in the contradiction candidate set

### Requirement: No Persisted Schema Change

Candidate edges MUST be represented as ephemeral dataclasses only;
producing them MUST NOT require changes to `model/okf.py`'s `Relation`
type, its codec, or `bundle/relations.py`, and MUST NOT alter merge/unmerge
reversibility.

#### Scenario: Merge/unmerge behavior is unaffected

- GIVEN a bundle containing candidate edges produced by this module
- WHEN `merge`/`unmerge` runs on unrelated concepts
- THEN its behavior and reversibility guarantees are unchanged from
  before this module existed
