# Delta for Query Answer

## ADDED Requirements

### Requirement: Chunk-Backed Dense Retrieval Reaches A Document's Tail

WHEN a document exceeds the embedder's chunking window, dense retrieval
MUST be able to retrieve that document for a question answerable only from
content past the window boundary (the document's tail), via
`vector_store.query()`'s document-level `VecHit`s.

#### Scenario: A tail-only question retrieves a long document with FTS disabled

- GIVEN a long document whose only content answering a given question sits
  past the point where earlier truncated embeddings would have stopped, and
  `fts_index` is `None`
- WHEN `answer(...)` is called with that question
- THEN the document appears in `citations` via the dense path alone

### Requirement: Chunk Collapse Is Invisible To Citation, Attribution, And Save Provenance

Because `VectorStoreDB.query()` collapses chunk hits to one `VecHit` per
`concept_id` before returning, `answer`'s citation identity,
`_split_attribution`'s positional block-to-citation mapping, and
`query --save`'s `concept_id`-list provenance MUST remain byte-identical in
shape to their pre-chunking contract: one context block and one `Citation`
per document, never per chunk.

#### Scenario: A multi-chunk document yields exactly one citation

- GIVEN a document whose best dense match came from a non-first chunk
- WHEN it is fused, placed in context, and cited
- THEN exactly one `Citation` for that document's `concept_id` appears,
  never one per chunk

#### Scenario: Save provenance is unaffected by chunking

- GIVEN an answer citing a chunked document
- WHEN `query --save` files provenance
- THEN the provenance list is `concept_id`s exactly as before chunking,
  with no chunk identity present

### Requirement: The Sensitivity Re-Check Still Runs Before Any Chunk's Content Reaches The LLM

`_assemble_context`'s per-document fail-closed sensitivity re-read MUST
still run, over the whole document body freshly re-read from disk, before
any of that document's content — chunked or not — reaches the LLM context.
The vector store MUST continue to return only `(concept_id, distance)`,
never document text.

#### Scenario: A confidential chunked document is still excluded

- GIVEN a document whose freshly re-read frontmatter marks it confidential,
  and it is a top dense hit assembled from chunk vectors
- WHEN `answer(...)` is called
- THEN it is excluded from context and citations exactly as it would be for
  a non-chunked document
