# Delta for Reindex Command

## ADDED Requirements

### Requirement: Embedding-Model Tag Gate Forces Full Re-Embed On Mismatch

At the start of the vector reindex pass, `reindex()` MUST read the stored
`embedding_model` tag from `vectors.db`'s `meta` table and compare it against
the explicit `model_tag` param passed in for this run. If the stored tag is
absent OR differs from `model_tag`, the vector-store pass for this run MUST
behave as if `force=True` (bypass the content_hash cache gate; every
discovered, readable doc is queued for re-embedding via the existing
`upsert_many` DELETE+INSERT path — no vec0 DROP), and after the embed batch
completes, the new `model_tag` MUST be persisted as the stored tag. This gate
is independent of the `--force` CLI flag (either can trigger the same
force-mode behavior) and MUST NOT affect the `_reindex_fts`/graph pass,
which stays gated solely by the bundle-manifest hash.

#### Scenario: Model mismatch forces full re-embed regardless of content_hash

- GIVEN a `vectors.db` with a stored tag `'model-a'` and every doc's
  content_hash already matching `vector_meta`
- WHEN `reindex()` runs with `model_tag='model-b'`
- THEN every discovered doc is re-embedded and upserted, and the stored tag
  becomes `'model-b'`

#### Scenario: Absent tag (pre-slice vectors.db) forces one re-embed then self-heals

- GIVEN a `vectors.db` created before this change, with no `meta` table row
  for `embedding_model`
- WHEN `reindex()` runs once with `model_tag='model-a'`
- THEN every discovered doc is re-embedded this run, the stored tag becomes
  `'model-a'`, and the NEXT `reindex()` run with the same `model_tag` is
  purely incremental (content_hash gate governs normally)

#### Scenario: Matching tag leaves the content_hash gate unchanged

- GIVEN a stored tag equal to the current `model_tag`
- WHEN `reindex()` runs
- THEN cache-hit/changed/new classification for each doc follows the
  existing content_hash comparison exactly as before this change

#### Scenario: Model-tag mismatch does not trigger an FTS/graph rebuild

- GIVEN a stored tag that differs from `model_tag`, and a bundle whose
  documents are otherwise unchanged
- WHEN `reindex()` runs
- THEN the FTS and graph derived indexes are NOT rebuilt by this gate (only
  the bundle-manifest hash, unaffected by the model tag, governs their
  rebuild)

### Requirement: `reindex()` Accepts An Explicit Model Tag Parameter

`state.reindex.reindex()` MUST accept an explicit string parameter (the
current `cfg.embedding_model` value) used solely to compare against and
update the stored `embedding_model` tag. The `Embedder` Protocol MUST NOT
gain a model-identity accessor — the tag flows only through this explicit
param, never through the embed-only seam.

#### Scenario: CLI wires the configured model into reindex

- GIVEN `cfg.embedding_model` resolved from `openkos.yaml`
- WHEN `openkos reindex` invokes the orchestrator
- THEN that exact string is passed as the model-tag param, and the
  `Embedder` Protocol's method surface is unchanged

## MODIFIED Requirements

### Requirement: Content-Hash Cache Gate

For each discovered doc, the orchestrator MUST compare its current
`content_hash` against `vector_meta`. An unchanged hash MUST be a cache-hit
that makes zero Embedder calls; a changed or absent hash MUST trigger
re-embedding and upsert. WHEN this run's stored `embedding_model` tag is
absent or differs from the current `model_tag` (Embedding-Model Tag Gate),
this per-doc comparison MUST be bypassed entirely for the vector pass —
every discovered, readable doc is treated as changed and re-embedded,
regardless of its content_hash.
(Previously: the content_hash comparison was the only gate; no model-tag
condition could override it.)

#### Scenario: Unchanged content_hash is a cache-hit with zero Ollama calls

- GIVEN a doc whose stored `vector_meta.content_hash` matches its current
  on-disk hash, and the stored model tag matches `model_tag`
- WHEN `reindex` runs (no `--force`)
- THEN the fake Embedder records zero calls for that doc, and its stored
  vector is unchanged

#### Scenario: Changed content re-embeds and upserts

- GIVEN a doc whose current hash differs from `vector_meta`
- WHEN `reindex` runs
- THEN the Embedder is called for that doc and its vector/hash are upserted

#### Scenario: New doc is embedded and upserted

- GIVEN a doc with no `vector_meta` row
- WHEN `reindex` runs
- THEN it is embedded, its vector is inserted, and `vector_meta` gains a row

#### Scenario: Model-tag mismatch overrides an otherwise-matching content_hash

- GIVEN a doc whose `content_hash` already matches `vector_meta`, but the
  stored `embedding_model` tag differs from `model_tag`
- WHEN `reindex` runs
- THEN that doc is re-embedded and upserted despite the matching hash
