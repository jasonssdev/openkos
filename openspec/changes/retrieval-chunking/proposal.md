# Proposal: Chunk-Backed Retrieval Vectors (#888)

## Intent

A document's stored embedding is its first ~8192 tokens, not the document.
Measured in the 0.2.10 E2E: for `bundle/sources/transcription3.md` (56,037
chars, ~15,785 tokens), `cos(full, FIRST half) = 1.0000` while
`cos(full, SECOND half) = 0.6582`. The full document's vector IS its first
half's vector, exactly. On the two long documents measured in #888, 47% and
49% of the token content is outside the embedder's window and therefore
outside the vector entirely.

What a user loses: ask `openkos query` about something discussed late in a
long transcript and the dense half of retrieval cannot see it at all. Only
FTS can, and FTS needs the user to have guessed the literal wording — it
indexes whole documents correctly (`state/fts.py:220-234`), so the two halves
of `10 FTS + 10 dense -> 5 fused` are looking at different documents. The
same truncated vector also decides which documents look like duplicates, which
pairs get proposed for edge typing, and which pairs get planned as
contradiction candidates. Two documents that share a boilerplate opening and
disagree completely in their second halves currently look near-identical.

## Scope

### In Scope

- `state/vectorstore.py` — chunk storage schema, the one-row-per-`concept_id`
  invariant, `VectorStore` Protocol surface, `neighbors()` and its document
  rollup.
- `state/reindex.py` — embed / cache-hit / prune orchestration at N chunks per
  document; the `EMBED_COMPOSITION_TAG` forced re-embed; embed-failure grain;
  the one-commit-per-run contract.
- `retrieval/answer.py` — `_dense_search`, chunk-hit-to-document resolution,
  `Citation` identity, and the per-document sensitivity re-check in
  `_assemble_context`.
- `graph/proximity.py` — `VectorProximitySource.pairs()` over a
  chunk-derived document vector, preserving its never-raises degrade.
- Re-measurement gates against the three named evals, plus one new gate for
  candidate-pair nomination (see Success Criteria).

### Out of Scope

- **The question-vector space.** `state/question_vectors.py` and
  `resolution/insight_identity.py::near_duplicate_insights` are a separate
  store (`question_vectors`, its own `QuestionVectorCache` Protocol) holding
  one short source question per filed Insight. Never truncated, needs no
  chunking, and must not be conflated with `vectors.db`.
- **FTS behaviour.** It already indexes whole documents. Nothing changes.
- **Context budgeting (#882).** Chunking multiplies what dense retrieval can
  surface, which makes the assembled-context budget more pressing — but the
  budget policy is #882's scope, and this change neither resolves nor waits
  on it. This change must not silently grow the prompt past what today's
  assembly already produces.

## Capabilities

### New Capabilities

- `embedding-chunking`: the windowing contract for embed text (window shape,
  size budget, lossless coverage) and the derivation of a document-level
  vector from its chunk vectors.

### Modified Capabilities

- `vector-store`: the schema now stores N vector rows per `concept_id`;
  `upsert`/`prune`/`meta_hashes`/`neighbors` contracts, today all worded as
  one row per id, change accordingly.
- `reindex-command`: the cache-hit gate, prune diff, model-tag gate, and
  embed-failure isolation must keep operating per WHOLE document over an
  N-row-per-document store.
- `query-answer`: the dense path returns chunk hits; citation identity and
  the sensitivity re-check must be re-stated for that unit.
- `graph-projection`: the third, embedding-proximity pass now reads a derived
  document vector rather than a stored one.
- `retrieval-fusion`: **conditional** — only if design fuses at chunk
  granularity rather than collapsing chunk hits to `concept_id` before
  fusion. If it collapses first, this capability is unchanged.

## Decisions This Change Must Make

These belong to `sdd-design`. They are stated here as decisions, not answers.

| # | Decision | What makes it hard | What constrains it |
|---|---|---|---|
| 1 | Chunk storage schema | `meta_hashes()`, the prune diff (`[cid for cid in cached_hashes if cid not in seen]`) and the model-tag gate all key 1:1 on `concept_id` today; the store must hold N rows per document without any of the three losing whole-document semantics | `vector_meta` must never gain fake-`concept_id` rows (its own docstring forbids polluting the hash cache); re-embedding a document to a *different* chunk count must not orphan the old rows; `prune_many` must remove ALL of a document's rows atomically |
| 2 | `EMBED_COMPOSITION_TAG` as the re-embed trigger | It is the intended reuse point — `reindex.py:83` says "Bump this token, not the gate's mechanics, on the next embed-text-shape change" — but a chunking bump is also a *schema* change, so a tag bump alone may not be sufficient | The stored tag is `{model}#{composition}`; the gate is the only mechanism that must force the migration, and a second parallel version marker is explicitly ruled out |
| 3 | `Citation` identity for a chunk hit | `_split_attribution` (`answer.py:256`) parses the model's `USED:` line as 1-based BLOCK POSITIONS and maps them to the same-index `citations` entry — never by `concept_id` | `query --save` files provenance as a `concept_id` list, so chunk identity must collapse back to `concept_id` before that write regardless; multiple chunks of one document must not inflate the citation count or duplicate a block |
| 4 | Embed-failure isolation grain | Today one doc = one `embed()` call, so failure isolation is per-doc by construction. With N calls per doc, chunk 3-of-5 failing has no defined meaning | `reindex.py:17-37`'s resilience invariant is written entirely in document terms; extraction's `_fan_out_windows` precedent is all-or-nothing; the three FATAL `OllamaError` subclasses must still re-raise before the generic handler |
| 5 | Reuse of `_chunk_lines` | It is proven, dependency-free, and lossless by construction (`"\n".join(_chunk_lines(text)) == text`) — but it sits inside `extraction/concept.py` next to extraction-specific tuning | The 12k/18k char thresholds and meeting-shape branching are extraction's, not embedding's; embedding's budget is bge-m3's 8192 tokens at a measured 3.55 chars/token for Spanish. Reuse the primitive, not the thresholds — and do not create a second definition of "window" in this codebase |

## Success Criteria

- [ ] The truncation property is gone: for a document exceeding the embedder
      window, `cos(document_vector, first_half_vector)` is no longer 1.0000,
      and a query answerable only from the document's tail retrieves that
      document through the dense path with FTS disabled.
- [ ] Chunk coverage is lossless — every embeddable byte of a document is
      inside exactly one chunk.
- [ ] `evals/edge_typing/` accuracy stays within its measured noise band
      **0.41–0.45** (baseline 0.44, qwen3:8b).
- [ ] `evals/contradictions/` holds antonym FP **0.24–0.28**, TP retention
      **1.00**, accuracy **0.88–0.90**.
- [ ] `evals/query_identity/` question-embedding margin stays at
      **+0.0745** or better. (This eval scores the question-vector space,
      which this change does not touch — it is a *witness that nothing
      leaked across stores*, not a measure of the fix.)
- [ ] **New gate, new work:** no committed eval measures
      `VectorProximitySource`'s live candidate-pair nomination. The two
      evals above score downstream classifiers over fixed, hand-built
      fixtures and cannot see which pairs a real bundle nominates. A gate
      for pair nomination must be *created* — a pre/post comparison on a
      real bundle, with `evals/query_identity/`'s paraphrase-worst vs.
      different-best margin as the candidate methodology. This is new
      construction, not an existing safety net being re-run.
- [ ] Assembled context does not grow beyond what today's assembly produces
      (the #882 boundary).

## Migration and User Impact

Every existing workspace holds truncated vectors. They are not repairable in
place: the missing text was never embedded.

- **What users run:** `openkos reindex`, once, after upgrading. The
  composition-tag gate should make this automatic rather than requiring
  `--force`.
- **What it costs:** a full re-embed of every surviving document — one
  embedding call per chunk, so total calls go from *documents* to *chunks*.
  The 0.2.10 E2E bundle held 32 embeddable documents at 32 vectors; the
  post-change call count is that bundle's total chunk count. Design must
  state the expected multiplier and whether the existing per-run cost
  disclosure still tells the truth.
- **What the CLI must disclose:** today `reindex` prints `embedding model
  changed (<previous_model_tag> -> <cfg.embedding_model>)`. `previous_model_tag`
  is the stored *effective* tag (`bge-m3#compose-v1`) while the right-hand
  side is the bare configured model, so a composition-only bump prints
  `embedding model changed (bge-m3#compose-v1 -> bge-m3)`. That claims a
  model change the user did not make and reads like a downgrade. The
  disclosure must name the real trigger (embed-text composition) and the
  real cost, or users will reasonably suspect their config was altered.

## Risks

| Risk | Constraint that produces it | Mitigation |
|---|---|---|
| Candidate-pair nomination shifts unmeasured | No eval covers `VectorProximitySource` in isolation | Build the new gate before the schema lands; treat it as a deliverable, not a checkbox |
| Chunk-level KNN ties | vec0 accepts exactly one `ORDER BY distance` and rejects a secondary key; ties fall back to insertion order. Row count per document goes 1 → N, making near-ties at the k-th boundary more likely | Break ties in Python as `neighbors()` already does; the residue (`k` cut inside the extension) stays non-zero and must be documented, not claimed fixed |
| Citation inflation or duplicate blocks | Attribution is positional, not `concept_id`-keyed | Decision #3 must collapse explicitly; assert citation count against document count in tests |
| Sensitivity re-check bypassed | `_assemble_context`'s per-document re-read is a deliberate defence-in-depth net against the walk-bypass leak | Any chunk→document resolution step must sit BEFORE that re-read, never around it |
| Protocol/fake breakage | `neighbors()` is off the `VectorStore` Protocol specifically so dict-backed fakes need not read blobs | Revisit that seam deliberately; do not promote chunk-aware lookup onto the Protocol without auditing every fake |
| Migration cost surprises long-bundle users | Call count scales with chunks, and Ollama serializes by default | Disclose the multiplier up front; measure on the 32-doc bundle before claiming a figure |
| Partial migration state | Embed failures leave a workspace mid-migration | Decision #4 must define what a partially-embedded document means for retrieval, and the tag must stay withheld until the run is complete (as it already does) |

## Rollback Plan

Revert the code and run `openkos reindex --force` with the previous
`EMBED_COMPOSITION_TAG` restored: the tag gate detects the mismatch and
re-embeds every document back to the single-vector shape. The chunk tables
are additive to `vectors.db` and can be dropped. Nothing outside
`vectors.db` is mutated — bundle files, `graph.db`, and the question-vector
store are untouched — so rollback costs one re-embed pass and no data.

## Dependencies

- A running Ollama with `bge-m3` for any re-measurement or migration test.
- `evals/edge_typing/`, `evals/contradictions/`, `evals/query_identity/`
  baselines as recorded above.
- Related, not blocking: #882 (context budgeting).
