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

### Requirement: An Unjudged Source's Own Pairs Are Withheld And Disclosed

The candidate pass MUST withhold any nominated pair whose BOTH endpoints
cite (`provenance:`) a common source carrying one of #772's judge-degrade
`extraction_notice` tokens (`judge-selection-unavailable` or
`judge-selection-empty`) — issue #841: objects stored without judge
selection are mutually proximate almost by construction, and each retained
pair becomes one LLM call downstream and permanent typed structure once
accepted. The gate MUST match EXACTLY those two tokens (every other
`extraction_notice` value discloses output produced as designed, and
gating on it would punish judged sources), and a cross pair with one
healthy endpoint MUST survive. Withheld pairs MUST be removed BEFORE
ranking and the per-run cap, so they never consume cap slots or shift the
paging window, and MUST be recorded on the candidate report (pairs and
quarantined source ids).

The withholding MUST be disclosed, never silent, on every surface that
renders the cap's truncation notice (`suggest-relations`,
`contradictions`, `curate`'s Structure gate), with the visible count
re-derived through the same sensitivity filter the truncation notice uses
and the remedy named (a re-ingest whose judge answers clears the
quarantine; the projection is derived state a rebuild reconsiders).

A generic per-source share cap is deliberately NOT specified: no
threshold separates a productive source from a degraded one without a
measurement harness (issue #841's own analysis), while this gate bounds
the documented failure at deterministic cost.

#### Scenario: A quarantined source's mutual pair is withheld

- GIVEN a source stamped `judge-selection-unavailable` with two derived
  objects, and a healthy source with one
- WHEN the graph builds with a candidate source nominating the mutual
  pair and a cross pair
- THEN only the cross pair becomes a candidate edge, AND the report
  carries the withheld pair and the quarantined source id

#### Scenario: Non-judge notice tokens do not gate

- GIVEN a source carrying any other `extraction_notice` value
- WHEN its derived objects' mutual pair is nominated
- THEN the pair becomes a candidate edge and nothing is withheld

#### Scenario: The withholding is disclosed with the remedy

- GIVEN a build that withheld at least one visible pair
- WHEN `openkos suggest-relations` runs
- THEN stdout carries one line with the withheld count, the unjudged
  source, and the re-ingest-then-reindex remedy

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
