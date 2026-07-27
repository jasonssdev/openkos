# Delta for Status

## ADDED Requirements

### Requirement: Needs-Attention Reports Concept-to-Concept Edge State

`openkos status` MUST report, under "needs attention" (or an adjacent
informational line), the count of concept-to-concept edges in the graph
projection and the count of those that are typed, using three mutually
exclusive states: (1) the graph has zero concept-to-concept edges at all;
(2) concept-to-concept edges exist (typed and/or candidate); (3) candidate
edges are not computable yet because embeddings are missing (`vectors.db`
absent or empty). State 3 MUST be reported distinctly from state 1 — an
absent/empty `vectors.db` MUST NOT be reported using the same message as a
graph that genuinely has zero edges. This check MUST remain read-only and
informational: its presence MUST NOT cause a non-zero exit.

#### Scenario: Empty graph reports no concept relationships

- GIVEN a bundle whose graph projection has zero concept-to-concept edges,
  and `vectors.db` is present and populated
- WHEN `openkos status` runs
- THEN it reports "no concept relationships yet" (state 1), distinct from
  the embeddings-missing message, and exits 0

#### Scenario: Edges present reports counts

- GIVEN a bundle whose graph projection has concept-to-concept edges, some
  typed
- WHEN `openkos status` runs
- THEN it reports the total edge count and the typed subset count, and
  exits 0

#### Scenario: Missing embeddings reports a distinct not-computable state

- GIVEN a bundle whose `vectors.db` is absent or empty, so candidate edges
  cannot be computed
- WHEN `openkos status` runs
- THEN it reports that candidate edges are not computable yet (state 3),
  using a message distinguishable from "no concept relationships yet"
  (state 1), and exits 0
