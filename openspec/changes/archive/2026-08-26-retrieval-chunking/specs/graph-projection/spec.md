# Delta for Graph Projection

## ADDED Requirements

### Requirement: Embedding-Proximity Pairs Are Derived From Chunk-Backed Document Vectors

`VectorProximitySource.pairs()` MUST continue to call `neighbors()` per
document id exactly as before; the vector `neighbors()` ranks by is now a
chunk-derived document vector rather than a single truncated embedding, but
`pairs()`'s own contract, signature, and never-raises degrade are
unaffected.

#### Scenario: A document with zero stored chunks degrades to no pairs, never raises

- GIVEN a document with zero stored chunks (no `doc_vectors` row)
- WHEN `pairs()` is called with that document among its candidates
- THEN it contributes no pairs and `pairs()` does not raise

#### Scenario: Nomination reflects full-document content, not a truncated prefix

- GIVEN two long documents that share a boilerplate opening chunk but
  diverge in later chunks
- WHEN `build_graph()`'s third pass runs
- THEN their proximity is judged by their full chunk-derived document
  vectors, not solely by the shared opening chunk
