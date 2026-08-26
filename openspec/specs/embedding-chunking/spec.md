# Embedding Chunking Specification

## Purpose

Defines the embed-text windowing contract that splits one document into N
chunk embed-texts, and the rule that derives one document-level vector from
those chunks. Consumed exclusively by `state/reindex.py` (chunk production,
embed calls) and `state/vectorstore.py` (document-vector derivation,
storage).

## Non-Goals

The question-vector space (`state/question_vectors.py`,
`resolution/insight_identity.py`) — a separate store, never chunked.
Extraction's own `_MEETING_CHUNK_THRESHOLD`/`_CHUNK_THRESHOLD` tuning and
meeting-shape branching — not reused here. FTS behaviour — unchanged.
Assembled-context token budgeting (#882).

## Requirements

### Requirement: Body-Only Chunking With A Repeated Header

For each document, the embed text MUST split into a `header` (title +
description + tags) and a `body`. The body MUST be packed into windows
using the existing line-packing primitive with an explicit target size,
never splitting inside a line. Each chunk's embed text MUST be
`header + "\n\n" + body_chunk`.

#### Scenario: Header repeats on every chunk

- GIVEN a document whose body produces 3 chunks
- WHEN embed texts are composed
- THEN all 3 chunk texts start with the identical header string

#### Scenario: Empty body still yields one chunk

- GIVEN a document whose body is empty
- WHEN embed texts are composed
- THEN exactly one chunk is produced, containing the header alone

### Requirement: Chunk Coverage Is Lossless

Rejoining a document's body chunks in order with `"\n"` MUST reproduce the
original body byte-for-byte.

#### Scenario: Rejoined chunks equal the original body

- GIVEN any document body split into chunks by the packing primitive
- WHEN the chunks are rejoined with `"\n".join(...)`
- THEN the result equals the original body exactly, byte for byte

### Requirement: Document Vector Is A Normalized Mean Of Normalized Chunk Vectors

The document-level vector MUST be computed as
`normalize(mean(normalize(chunk_i) for all chunks))`, unweighted by chunk
length, and MUST NOT be computed by truncating the document to fit the
embedder's window.

#### Scenario: A single-chunk document's derived vector equals that chunk

- GIVEN a document with exactly one chunk
- WHEN its document vector is derived
- THEN the derived vector equals that chunk's normalized vector

#### Scenario: The truncation property is gone for a multi-chunk document

- GIVEN a document long enough to produce more than one chunk
- WHEN its document vector is compared against its first chunk's vector via
  cosine similarity
- THEN the similarity is strictly less than `1.0` — the document vector is
  no longer identical to a prefix's vector

#### Scenario: Long boilerplate does not dominate via an unweighted mean

- GIVEN a document whose first chunk is far longer than its remaining
  chunks
- WHEN the document vector is derived
- THEN it is the unweighted mean of normalized per-chunk vectors, never a
  length-weighted mean
