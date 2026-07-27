# Delta for Graph Projection

## ADDED Requirements

### Requirement: Third Pass — Embedding-Proximity Candidate Edges

`_populate_graph_tables` MUST run a third, embedding-proximity edge
pass on every `build_graph()` call, alongside the existing bundle-link
pass and the `relations:`/provenance-mirror typing pass. For each
concept with a stored embedding in `vectors.db`, it MUST query nearest
neighbors and emit a candidate concept-to-concept edge for neighbors
above a fixed distance/similarity cutoff, up to a fixed top-K. This
pass is projection-ephemeral: it MUST NOT write to `relations:`
frontmatter or any bundle file, and MUST be fully recomputed on every
`build_graph()` call with no cross-run cache. Candidate edges MUST NOT
alter the node/edge output of the existing two passes, and existing
`tests/unit/graph/test_sqlite_graph.py` behavior for those two passes
MUST pass unchanged. The concrete row typing (whether a candidate edge
carries `relation_type = NULL` or a new synthesized type) is
unspecified by this requirement; either representation MUST satisfy it.

#### Scenario: Nearby concepts produce a candidate edge

- GIVEN two concepts with stored embeddings whose distance is at or
  below the cutoff
- WHEN `build_graph()` runs
- THEN a candidate concept-to-concept edge between them exists in the
  projection

#### Scenario: Distant concepts produce no candidate edge

- GIVEN two concepts with stored embeddings whose distance exceeds the
  cutoff
- WHEN `build_graph()` runs
- THEN no candidate edge is produced between them

#### Scenario: Deterministic and non-destructive to existing passes

- GIVEN an unchanged bundle and unchanged `vectors.db`
- WHEN `build_graph()` runs twice
- THEN both runs yield the same candidate-edge set in the same order,
  and the node/edge output of the two existing passes is unchanged

### Requirement: Third Pass Degrades Cleanly Without Embeddings

WHEN `vectors.db` is absent or empty, the embedding-proximity pass MUST
yield zero candidate edges and MUST NOT raise; `build_graph()` MUST
remain a successful, non-fatal read, and the two existing passes MUST
still run and produce their normal output.

#### Scenario: Absent vectors.db yields zero candidates, no crash

- GIVEN a bundle with no `.openkos/vectors.db` file
- WHEN `build_graph()` runs
- THEN it completes successfully with zero candidate edges, and the
  bundle-link and typed-relation passes still produce their normal
  output

#### Scenario: Empty vectors.db yields zero candidates, no crash

- GIVEN a `.openkos/vectors.db` file that exists but has no stored
  embeddings
- WHEN `build_graph()` runs
- THEN it completes successfully with zero candidate edges
