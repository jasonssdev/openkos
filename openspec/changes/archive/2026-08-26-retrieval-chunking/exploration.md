# Exploration: retrieval-chunking — no chunking on the read side (#888)

> **Provenance.** Produced by the `sdd-explore` phase and materialized here by
> the orchestrator: that phase agent has no file-write capability, so the
> hybrid artifact store could only be satisfied through Engram
> (`sdd/retrieval-chunking/explore`) at authoring time. Content is the
> phase's, unedited.
>
> Six load-bearing claims were spot-checked by the orchestrator before this
> phase was accepted: `_chunk_lines`' lossless-join docstring at
> `src/openkos/extraction/concept.py:2069`; `EMBED_COMPOSITION_TAG =
> "compose-v1"` at `src/openkos/state/reindex.py:74`; `_split_attribution` at
> `src/openkos/retrieval/answer.py:256`; the positional citation filter at
> `answer.py:940-944`; `pairs`/`open_proximity_source` at
> `src/openkos/graph/proximity.py:115,178`; and the presence of
> `evals/edge_typing/`, `evals/contradictions/`, `evals/query_identity/`.
> All six hold.

## Why this change exists

The measurements behind issue #888, taken against a live Ollama during the
0.2.10 manual E2E and treated as given by this phase:

1. `state/reindex.py:320-330` composes title+description+tags+FULL BODY via
   `_compose_embed_text` and sends it in ONE `embedder.embed([text])[0]` call.
2. `bge-m3` allows 8192 tokens (`/api/show` -> `bert.context_length = 8192`).
3. Embedding `bundle/sources/transcription3.md` (56,037 chars, about 15,785
   tokens) whole versus its halves gave `cos(full, FIRST half) = 1.0000`
   exactly, `cos(full, SECOND half) = 0.6582`, `cos(FIRST, SECOND) = 0.6582`.
   The full document's embedding IS its first half's embedding.
4. `SELECT count(*) FROM vector_meta` = 32 for 32 embeddable docs: one vector
   per document, never per chunk.
5. Spanish token ratio measured at 3.55 chars/token.
6. FTS5 has no length limit and indexes the same four fields
   (`state/fts.py:220-234`), so the lexical half of `10 FTS + 10 dense ->
   5 fused` sees the whole document while the dense half sees only a prefix.

## Scope decision (made by the maintainer before this phase)

Chunks become the single source of truth, and the document-level vector is
DERIVED from its chunks. This deliberately accepts that `neighbors()`
semantics change and that duplicate detection, edge typing and contradiction
candidate planning must be re-measured.

Rejected alternative, recorded so it is not re-proposed: keeping the
doc-level vector as today and adding a separate chunk table used only by
`query()`. Rejected because it leaves the doc-level vector truncated, so the
blindness persists for duplicates and edge suggestion.

## Current State

### 1. Vector store surface — `src/openkos/state/vectorstore.py`

`VectorStore` Protocol (149-223) declares: `upsert` (170), `upsert_many`
(177, no commit), `query` (183), `meta_hashes` (188), `prune` (192),
`prune_many` (197, no commit), `commit` (203), `read_model_tag` (208),
`write_model_tag` (214, no commit), `close` (221). `VectorStoreDB` (383-549)
implements all of these plus `neighbors` (437-479), which is DELIBERATELY NOT
on the Protocol — its own docstring (441-446) says why: "That Protocol is the
narrow write/query surface every fake in the test suite already implements;
adding a method there would break all of them for a capability only the real
on-disk store can provide — it reads a blob back out of `vectors`, which a
dict-backed fake has no notion of." `neighbors` reads the concept's OWN
stored blob (`_SELECT_VECTOR_BLOB_SQL`, 122), runs the same KNN SQL INCLUDING
the anchor itself at distance ~0, then re-sorts by `(distance, concept_id)`
(459-463) because vec0 forbids a secondary ORDER BY and ties otherwise follow
insertion order.

Schema (52-79): `vectors` is a `vec0` virtual table with
`embedding float[EMBED_DIM]`, `concept_id`, `content_hash` — ONE embedding
column, sized for ONE vector per row. `vector_meta` (60-65) mirrors it 1:1 as
`(concept_id PRIMARY KEY, content_hash)` for the cache-hit gate. `meta`
(67-72) is a separate generic table for the model-tag singleton.
`upsert`/`upsert_many` (393-423) always DELETE-then-INSERT keyed by
`concept_id` — the schema has no notion of multiple rows per document.

Constraint quoted verbatim (112-118): "vec0 permits exactly one
`ORDER BY distance` clause on a KNN query and rejects a secondary sort key
outright... so equidistant rows come back in rowid — i.e. insertion —
order... Callers that need a reproducible order must break ties themselves;
`neighbors` does." Any chunk-level KNN implementation inherits this same
tie-break burden.

Residual limit quoted verbatim (465-472, `neighbors` docstring): "this fixes
the ORDER of the rows vec0 returns, not WHICH rows it returns. The `k` cut
happens inside the extension, so if more rows tie exactly at the `k`-th
distance than fit, which of them arrive here is still vec0's choice... Exact
ties require byte-identical embeddings... the residue is narrow, but it is
not zero." Chunking multiplies row count per document, which could make
near-ties more common at the KNN boundary.

### 2. Every consumer, and the unit it needs

**Group A — document-level KNN via `VectorStoreDB.query()`** (Protocol
method), consumed ONLY by `retrieval/answer.py::_dense_search` (703-751,
calls `vector_store.query` at 743). This is the only consumer of `.query()`.

**Group B — document-level proximity via `VectorStoreDB.neighbors()`**
(DB-only), consumed exclusively through
`graph/proximity.py::open_proximity_source` (178-199) ->
`VectorProximitySource.pairs()` (115-152), which calls
`self._query.neighbors(concept_id, TOP_K + 1)` (126) per document id and
nominates `ProximityPair`s at a fixed L2 floor (`MAX_NEIGHBOR_DISTANCE`,
63-65, derived from `CANDIDATE_SIMILARITY_THRESHOLD`, 60-61). Every call site
needs DOCUMENT-level pairs, because the consumer on the other end is always
`build_graph(..., candidates=source)` (`graph/sqlite_graph.py:624-651`),
which creates document-to-document edges:

- `cli/main.py:4710` — `_refresh_derived_after_write`'s graph rebuild.
- `cli/main.py:13311-13332` — `_open_proximity_or_degrade`, the single
  chokepoint every other call site routes through.
- `cli/main.py:15283` — `contradictions` command's `build_graph(candidates=)`
  before `plan_candidates`.
- `cli/main.py:17115` — `suggest-relations` command (same pattern).
- `cli/curate.py:720` — `_preconditions_probe`, a binary availability gate
  (opens then immediately closes; never reads a pair).
- `cli/curate.py:1070` — `_structure_probe`, feeds `candidate_edges(...)`.
- `cli/curate.py:1602` — `_contradiction_plan`, feeds `plan_candidates(...)`.
- `cli/main.py:4620,4728,16938` — direct `open_vector_store` calls for the
  `reindex`/`ingest` WRITE path (upsert/prune), not proximity reads.
- `cli/main.py:12116` — `vector_store_is_empty` (row-count check only).

`graph/base.py::GraphStore.neighbors` (55-57) is a DIFFERENT, unrelated
method — out-edge lookup over the typed/untyped edge table, not vector KNN.
Do not conflate the two `neighbors()`s.

**Group C — separate vector space.** `state/question_vectors.py` /
`resolution/insight_identity.py::near_duplicate_insights` embeds filed-Insight
SOURCE QUESTIONS, with its own `question_vectors` table
(`state/question_vectors.py:60-232`) and its own `QuestionVectorCache`
Protocol (`insight_identity.py:220-253`). It never touches
`vectors.db`/`VectorStore`, embeds one short question per filed Insight, and
needs no chunking. OUT OF SCOPE for this change's mechanics — but its
identity-signal finding (section 6) is directly relevant precedent.

### 3. Retrieval path end to end — `src/openkos/retrieval/answer.py`

- `_fts_search` (681-700) calls `fts_index.search(...)`, degrades to
  `([], [])` if `fts_index is None`.
- `_dense_search` (703-751) calls `embedder.embed([question])[0]` then
  `vector_store.query(embedding, k=pool_limit)` (743), degrading to
  `([], True)` on `VecUnavailable`/`sqlite3.Error`/generic `OllamaError`; the
  three FATAL `OllamaError` subclasses re-raise (744-749).
- `fusion.fuse(hits, vec_hits)[: max(limit, 0)]` (866) — Reciprocal Rank
  Fusion over the two `concept_id` lists, position-only, is the FINAL ranking.
- `_assemble_context` (425-536) re-reads each fused `concept_id`'s FULL FILE
  from disk (`okf.concept_path_for(...).read_text(...)`, 491-494), re-parses
  frontmatter, applies the sensitivity re-check, and builds ONE context block
  = the WHOLE document body per `concept_id`, plus ONE
  `Citation(concept_id, title, confidential)` per surviving concept,
  index-aligned with `context_blocks`.
- `Citation` (329-343) carries ONLY `concept_id` + `title` + `confidential` —
  no chunk/offset identity exists today.
- `_user_content` (607-624) numbers blocks `[1]`, `[2]`; the model's closing
  `USED: 1, 3` line is parsed by `_split_attribution` (256-301) into 1-based
  BLOCK POSITIONS, then mapped back to the SAME-INDEX `citations` entry
  (940-944) — never by `concept_id`. **This is the exact seam a chunk-level
  hit must cross: whatever unit is fused and placed in a context block becomes
  the attribution/citation unit.** Either (a) each chunk still resolves to one
  whole-document `Citation`, or (b) `Citation` grows a chunk identity and every
  downstream consumer must be re-examined. That decision belongs to
  `sdd-design`.
- `query --save` files the answer as an Insight with provenance to every
  `Citation.concept_id` (`cli/main.py:16218-16222`) — provenance is a
  `concept_id` list, so chunk identity must collapse back to `concept_id`
  before reaching this write path regardless of design.

### 4. The reindex path — `src/openkos/state/reindex.py`

`reindex()` (180-413) walks `okf._iter_docs(bundle_dir)` once. Per doc,
`content_hash(raw_bytes)` is gated against `db.meta_hashes()` to decide
cache-hit vs. queue-for-embed (302-306). `_compose_embed_text` (96-107) builds
ONE string per doc, embedded in ONE `embedder.embed([text])[0]` call (330).
Every queued doc gets its OWN call (per-doc grain, 17-18, 200-201) so one
doc's transient `OllamaError` never aborts siblings (324-366); the three FATAL
subclasses re-raise and MUST be checked before the generic `OllamaError`
(order is safety-critical per the comment at 340-349). Successful embeds
accumulate into ONE `db.upsert_many(items)` call (365).

`cached_hashes = db.meta_hashes()` (275) is read ONCE and diffed against
`seen` to compute the prune set (368-377). **This diff is keyed 1:1 on
`concept_id`.** N rows per document requires either (a) chunk-keyed
`vector_meta`/`meta_hashes()` with a per-document rollup, or (b) a second
`concept_id -> [chunk_ids]` mapping table, so the prune diff and cache-hit
gate still operate per WHOLE document. `prune_many`'s contract (197-201,
496-505) currently assumes ONE row removed per `concept_id`; a chunked
implementation must delete ALL chunk rows atomically, and `upsert_many`'s
DELETE-then-INSERT-per-item loop (412-423) must be revisited so re-embedding a
document with a DIFFERENT chunk count leaves no orphans.

The model-tag gate (`_effective_model_tag`, 87-93; `EMBED_COMPOSITION_TAG`,
74-84) composes a tag suffix specifically so an embed-text-composition change
forces exactly one full re-embed through the SAME gate a model change uses:
"Bump this token... on the next embed-text-shape change". This is the intended
mechanism to reuse, not something to reinvent.

Per-doc embed-failure isolation (17-37, `ReindexReport.embed_failed`/`skipped`,
123-158) currently isolates at DOCUMENT grain. If one document is embedded via
N chunk calls, `sdd-design` must decide the failure grain: all-or-nothing per
document, or partial per-chunk state. The existing invariant text is written
entirely in per-doc terms, so this is a genuine new decision surface.

### 5. Existing chunking prior art — `src/openkos/extraction/concept.py`

`fans_out(source_text, *, source_title)` (1918-1941) — PUBLIC, decides whether
a source takes the CHUNKED extraction path, branching on `_chunk_threshold_for`
(1907-1915: `_MEETING_CHUNK_THRESHOLD = 12_000` at 1871 vs `_CHUNK_THRESHOLD =
18_000` at 1839) and `_is_meeting_shaped`. Documented "Never raises... total by
construction" (1934-1939) because `cli/main.py` calls it inside an exception
handler.

`_chunk_lines(text, target=None)` (2051-) packs LINES into windows of at most
`_CHUNK_TARGET` chars, NEVER splitting inside a line, documented "Lossless by
construction: `\"\\n\".join(_chunk_lines(text)) == text`" (2069). A clean,
dependency-free, line-based windowing primitive already proven in production.

`_fan_out_windows(windows, source_title, llm, ...)` (2287-2349) drives one
`_extract_once` chat call PER window then merges results; the caller's contract
is all-or-nothing (a window failure discards partial results, per the comment
at 3243-3246).

**Reusability assessment.** `_fan_out_windows` is coupled to `ExtractionResult`
merging and an LLM-chat-per-window contract — NOT reusable for float-vector
aggregation. `_chunk_lines` itself is general-purpose, already tested, and has
no extraction-specific coupling in its signature: it is the strongest reuse
candidate, and reusing it avoids two different definitions of "window" in one
codebase. The 12k/18k thresholds and meeting-shape branching are
extraction-specific tuning and should NOT be assumed to transfer to embedding's
8192-token bge-m3 budget without separate justification.

### 6. What must be re-measured, and where those evals live

- **`evals/edge_typing/`** (`run_edge_typing_eval.py`) — scores
  `suggest_edge_types`' TYPE accuracy against a FIXED, constructed 17/23-edge
  fixture. It measures the CLASSIFIER given a pair, not which pairs get
  nominated, so it is downstream of but does not exercise
  `VectorProximitySource`. Baselines: accuracy 0.44 (`qwen3:8b`), 0.41-0.45
  noise band across 4 runs; `related_to` 67% of emissions on a real bundle;
  direction-trap hits 3/18 (qwen3:8b) vs 17/18 (gemma2:27b, 0.00 reversed
  accuracy).
- **`evals/contradictions/`** (`run_contradictions_eval.py`) — drives the REAL
  `find_contradictions` path over an 18-pair CONSTRUCTED fixture, so it also
  does not depend on live proximity nomination. Adopted-prompt baseline:
  antonym FP 0.24-0.28 (down from 0.40), TP retention 1.00, accuracy 0.88-0.90.
- **`evals/query_identity/`** (`run_query_identity_probe.py`) — the closest
  existing identity-signal eval, though for filed-Insight identity. Over
  14,365 pairs / 170 filings (`bge-m3`): title margin -0.1579 (fails),
  answer-BODY-embedding margin -0.0620 (fails — "two answers about one topic
  are textually similar whether or not they answer the same question"),
  question-embedding margin +0.0745 (separates). Directly relevant precedent:
  whole-embedding comparison of long text does not reliably signal "same
  object."
- **`evals/insight_scan_bound/`** — measures COST only (~11.8ms per filed
  insight), not accuracy; documents a linear-cost baseline a chunk-multiplied
  embed workload could threaten if reused incorrectly for that other space.
- **Gap:** no committed eval measures `VectorProximitySource`'s live
  candidate-PAIR recall/precision. Both named evals score downstream
  classifiers against hand-built fixtures. `sdd-design`/`sdd-tasks` may need a
  new gate rather than relying solely on the three evals above.

### 7. Test surface

- `tests/unit/state/test_vectorstore.py` — `VectorStoreDB` schema and CRUD,
  including the vec0-0.1.9-semantics spike tests referenced at module
  docstring lines 20-26. Confirm exact `neighbors()` coverage during
  `sdd-tasks` RED-test planning.
- `tests/unit/state/test_reindex.py` — cache-hit gate, prune pass, model-tag
  gate, embed-failure isolation.
- `tests/unit/cli/test_reindex_cmd.py` — CLI-level `reindex`.
- `tests/unit/graph/test_proximity.py` — `VectorProximitySource`,
  `open_proximity_source`, `ProximityPair` canonicalization, the
  `MAX_NEIGHBOR_DISTANCE`/`TOP_K` floor.
- `tests/unit/retrieval/test_answer.py`, `test_fusion.py`, `test_pool.py` —
  fuse/assemble/answer flow, `Citation`/attribution splitting.
- `tests/unit/cli/test_query.py`, `test_query_save.py` — citation rendering
  and `--save` provenance filing.
- `tests/unit/resolution/test_edge_typing.py`,
  `tests/unit/cli/test_contradictions.py`,
  `tests/unit/resolution/test_contradiction.py` — downstream consumers; re-run
  (not necessarily re-write) after any proximity-source change.
- `tests/unit/resolution/test_insight_identity.py`,
  `tests/unit/state/test_question_vectors.py` — the separate question-vector
  space; in scope only for "do not confuse the two surfaces."

Strict TDD is active (`openspec/config.yaml` `apply.tdd: true`, runner
`uv run pytest`). RED tests should target `vectorstore.py`'s schema/CRUD
change, `reindex.py`'s per-document-N-chunks logic, and `answer.py`'s
dense-hit-to-`Citation` resolution.

### 8. Constraints and hazards (verbatim)

- One-row-per-`concept_id` invariant, `vectorstore.py:170-175`: "Replace
  `concept_id`'s stored vector and hash with `embedding`/`content_hash`,
  committing once for this call." Singular vector, singular hash, per id. This
  is the exact invariant the chosen fix must break.
- `vectorstore.py:52-58`: `vectors` has exactly one `embedding` column sized
  `float[EMBED_DIM]` — a single fixed-width slot per row.
- `vectorstore.py:73-79`: "`vector_meta` is the per-concept content_hash cache
  `meta_hashes()`/`reindex`'s incremental gate reads; `meta` is for
  whole-store, singleton settings... that must NEVER appear as a fake
  `concept_id` row in `vector_meta`." Any new chunk-tracking table must not
  collide with either table's semantics.
- vec0 tie-ordering, `vectorstore.py:112-118` (quoted in section 1): any new
  chunk-KNN-then-rollup logic must independently re-derive deterministic
  ordering.
- `neighbors()` residual limit, `vectorstore.py:465-472` (quoted in section 1):
  more rows per document raises the chance of exact ties among chunk vectors of
  templated content.
- `prune`/`prune_many` contract, `vectorstore.py:192-201`: both worded as ONE
  row removed per id; a chunked schema must remove ALL chunk rows atomically or
  silently orphan rows.
- Sensitivity fail-closed re-check, `answer.py:438-485`: a confidential doc
  must be excluded "even though it is normally already excluded upstream at the
  hit-seam filter — this is a redundant post-filter safety net." If chunking
  adds a resolve-chunk-to-document step between fusion and `_assemble_context`,
  this defence-in-depth re-check must still run per DOCUMENT before any chunk's
  content reaches the LLM, or it reopens the walk-bypass leak the docstring
  (446-461) describes fixing.
- `reindex.py:17-37`: per-doc embed isolation is written entirely in document
  terms. A chunked embed introduces a grain this model does not describe.
- `reindex.py`'s single-commit-per-run contract must still hold: whatever
  multi-row write shape is chosen, the run remains ONE commit at the end.
- `graph/proximity.py:104-110`: "Never raises. A k-NN failure mid-iteration
  yields zero candidates rather than aborting the caller." Any
  document-vector-derivation failure (e.g. zero chunks stored) must degrade the
  same way, not raise.
- `EMBED_COMPOSITION_TAG` (`reindex.py:74-84`): explicitly designed as the
  reuse point for "the next embed-text-shape change", which this is.

## Affected Areas

- `src/openkos/state/vectorstore.py` — schema, Protocol, `VectorStoreDB` CRUD,
  `neighbors()`.
- `src/openkos/state/reindex.py` — walk, cache-hit, embed, prune, model-tag
  gate.
- `src/openkos/retrieval/answer.py` — `_dense_search`, `_assemble_context`,
  `Citation` identity, attribution-index-to-citation mapping.
- `src/openkos/graph/proximity.py` — `VectorProximitySource.pairs()`.
- `src/openkos/extraction/concept.py` — `_chunk_lines` reuse candidate only.
- `src/openkos/resolution/insight_identity.py`,
  `src/openkos/state/question_vectors.py` — separate space; must not be
  conflated.
- `evals/edge_typing/`, `evals/contradictions/`, `evals/query_identity/` —
  no-regression baselines.
- `tests/unit/state/test_vectorstore.py`, `test_reindex.py`,
  `tests/unit/graph/test_proximity.py`, `tests/unit/retrieval/test_answer.py`
  — primary RED-test targets.

## Approaches

Not explored — the maintainer already made the scope decision. This phase
mapped current state only, per explicit instruction.

## Recommendation

Proceed to `sdd-design` with five decisions this mapping surfaces:

1. A chunk storage schema that keeps `meta_hashes()`/prune/model-tag-gate
   operating correctly per WHOLE DOCUMENT while storing N vector rows per
   document.
2. Reuse of `EMBED_COMPOSITION_TAG` as the forced-re-embed trigger.
3. An explicit `Citation` identity decision for chunk hits, since
   `query --save` provenance and the `USED:` attribution index both key off
   today's block-per-document assumption.
4. An explicit per-chunk vs. per-document embed-failure isolation grain.
5. Reuse of `_chunk_lines` as the line-packing primitive, decoupled from
   extraction's 12k/18k thresholds.

## Risks

- No eval measures `VectorProximitySource`'s candidate-PAIR recall/precision in
  isolation — only downstream classifiers against FIXED fixtures that do not
  depend on live nomination. A chunking-driven change to which pairs get
  nominated in a REAL bundle is not covered by any committed baseline.
- `neighbors()` is DB-only specifically to avoid forcing every test fake to
  implement blob-reading; any redesign must preserve or deliberately revisit
  this seam split, not silently promote chunk-aware lookup onto the Protocol.
- vec0's single-ORDER-BY limitation means chunk-level tie-breaking and any
  document-level rollup ordering must be implemented entirely in Python.
- `Citation`/attribution identity is positional, not `concept_id`-based
  (`answer.py:934-944`) — chunk-granularity fusion risks duplicate citations or
  citation-count inflation unless explicitly collapsed by `concept_id` before
  block numbering.

## Ready for Proposal

Yes — the current-state mapping is complete across all 8 requested areas with
`file:line` citations.
