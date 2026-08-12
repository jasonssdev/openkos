# Delta for Reindex Command

Slice 1b (depends on Slice 1a's ledger relocation).

## ADDED Requirements

### Requirement: Composed Embed Text Replaces Raw-Bytes Embedding

For each discovered document, `reindex` MUST embed COMPOSED text —
title, description, tags, and body, assembled with the same composition
`fts.py` uses to build its own index text — rather than the document's
raw frontmatter+body bytes. This closes #554: a document whose earlier
ledger-embedded history dominated its raw byte count no longer truncates
its own concept content out of the embedding, because the ledger no
longer lives in the document at all (Slice 1a) and the embed text is now
a bounded composition rather than the whole file.

#### Scenario: Embed text matches FTS's field composition
- GIVEN a document with distinct `title`, `description`, `tags`, and body
  content
- WHEN `reindex` embeds that document
- THEN the text passed to the Embedder is composed from those same four
  fields, in the same shape `fts.build_index` uses for that document

#### Scenario: A document with no ledger history embeds identically to before, content-wise
- GIVEN a document that was never a merge survivor
- WHEN `reindex` embeds it
- THEN the composed embed text still carries its title/description/tags/
  body content — no field is dropped by the switch away from raw bytes

#### Scenario: A large-history survivor's own content no longer truncates out
- GIVEN a merge survivor whose ledger entries formerly lived in its own
  frontmatter and pushed its raw byte count far past the embedder's
  effective window
- WHEN `reindex` embeds that survivor post-relocation
- THEN the composed embed text is bounded to its own title/description/
  tags/body — none of the (now-relocated) ledger history is embedded, and
  the concept's own content is fully represented
