# Design: Chunk-Backed Retrieval Vectors (#888)

## Technical Approach

Chunks become the stored unit; the document stays the unit every consumer
sees. `vectors` holds N chunk rows per `concept_id`; a second vec0 table
`doc_vectors` holds one derived mean per `concept_id`. `VectorStoreDB.query()`
collapses chunk hits to documents **before returning**, so `fusion.fuse`,
`lifecycle.filter_hits`, `_assemble_context`, `Citation`, and `query --save`
are byte-identical in shape. The collapse boundary is the whole design: it is
placed at the lowest possible layer so nothing above `state/vectorstore.py`
learns that chunks exist.

## Architecture Decisions

### D1 — Chunk storage schema

**Choice.** `vectors` gains one metadata column, `chunk_index INTEGER`;
`concept_id` keeps holding the **document** id, never a composite key.
`vector_meta` stays one row per document and gains `chunk_count INTEGER`.
`meta` is untouched — no fake `concept_id` row anywhere.

| Property | Mechanism |
|---|---|
| `meta_hashes()` unchanged | `_SELECT_META_HASHES_SQL` names its two columns; `vector_meta` is still 1:1 on `concept_id` |
| Prune diff unchanged | `reindex.py:372-374` still diffs document ids |
| `prune_many` atomicity | `DELETE FROM vectors WHERE concept_id = ?` removes **all N chunk rows in one statement** (plus one `doc_vectors` and one `vector_meta` delete); no commit inside, so the whole prune batch lands in reindex's single end-of-run commit |
| No orphans on a changed chunk count | `upsert_many` keeps its DELETE-then-INSERT order per item: the DELETE is chunk-count-blind (it matches on `concept_id`), so 12 old rows are removed before 5 new ones are inserted. Orphaning is structurally impossible, not tested-away |

**Rejected**: composite `concept_id` of the form `doc#c3` — breaks
`DELETE ... WHERE concept_id = ?`, breaks `lifecycle.filter_hits`, and makes
`vector_meta` ambiguous. **Rejected**: a third `concept_id -> [chunk_ids]`
mapping table — a second source of truth for a fact the `vectors` rows
already carry.

**Migration (existing workspaces).** A vec0 virtual table cannot be
`ALTER`ed and `CREATE VIRTUAL TABLE IF NOT EXISTS` silently keeps the legacy
3-column shape. `open_vector_store` therefore probes
`SELECT chunk_index FROM vectors LIMIT 0`; on `OperationalError` it drops
`vectors`, recreates it, and **clears `vector_meta`**. Clearing the hash
cache is mandatory, not tidiness: dropped vectors with a surviving hash cache
would read as cache hits forever and leave the store permanently empty. This
runs inside `open_vector_store`'s own existing DDL commit, so reindex's
one-commit-per-run contract is untouched.

**Spike required first** (matching the 0.1.9 precedent at
`vectorstore.py:20-26`): confirm against the real extension that an INTEGER
metadata column is declarable, that DELETE-by-`concept_id` removes *all*
matching rows, and that KNN returns multiple rows sharing one `concept_id`.
Contingency if it fails: keep `vectors` 3-column and hold `chunk_index` in an
ordinary sidecar keyed by `rowid`.

### D2 — Document-level vector derivation

**Choice.** `doc = normalize(mean(normalize(chunk_i)))`, unweighted, over all
chunks of the document; **materialized** as one row in a separate vec0 table
`doc_vectors(embedding float[EMBED_DIM], concept_id TEXT)`, written in the
same `upsert_many` item.

| Alternative | Why rejected |
|---|---|
| Length-weighted mean | A long boilerplate chunk would dominate — the defect being fixed is boilerplate dominating |
| Mean without re-normalizing | `MAX_NEIGHBOR_DISTANCE` (`proximity.py:63-65`) is documented "Valid ONLY because embeddings are L2-normalized"; an unnormalized mean silently moves the nomination floor |
| Compute on read | `pairs()` calls `neighbors()` once per document; deriving on read is O(corpus) per anchor → O(n²) blob reads, and vec0 cannot KNN over values it does not store |
| Sentinel row (`chunk_index = -1`) inside `vectors` | The document mean would compete with chunks in the retrieval KNN — the truncation-era behaviour re-entering through the back door |

**`neighbors()` and the degrade posture.** `neighbors()` reads
`doc_vectors` instead of `vectors`; everything else about it is unchanged,
including the `(distance, concept_id)` Python re-sort. A document with zero
stored chunks has no `doc_vectors` row, so the existing
`row is None → return []` branch (`vectorstore.py:473-475`) already covers it
— no new failure path, and `VectorProximitySource`'s never-raises degrade
(`proximity.py:104-110, 145-146`) is preserved by **not touching it**.

### D3 — Chunk size, overlap, and per-chunk header

**Choice.** `_EMBED_CHUNK_TARGET = 12_000` chars, **zero overlap**, body-only
chunking with the document header repeated on every chunk. `_chunk_lines`
(`extraction/concept.py:2051`) is reused as the line-packing primitive with
an explicit `target=`; extraction's 12k/18k thresholds and meeting-shape
branching are **not** imported.

Budget derivation: 12,000 ÷ 3.55 chars/token ≈ 3,380 tokens = 41% of
bge-m3's 8192. The 59% headroom pays for three things simultaneously — the
repeated header, `_chunk_lines`' documented single-oversized-line escape
(`concept.py:2067`), and a token-dense document (code, tables, CJK) whose
ratio is worse than the one Spanish measurement. At 2× worse ratio, 12k chars
is still 6,760 tokens, inside the window. **Rejected 24,000** (only 17%
headroom — one ratio excursion truncates silently, reintroducing the exact
defect). **Rejected 4,000** (extraction's value, tuned for LLM object
recovery per window, a different objective; it triples row count and embed
calls for no embedding benefit — embedding wants the *largest* window that
never truncates, because a wider window keeps more intra-document context in
one vector).

**Header.** `_compose_embed_text` splits: `header` = title + description +
tags; each chunk's embed text is `header + "\n\n" + body_chunk`, and the body
is chunked to `12_000 - len(header)`. Rejected: chunking the composed string
whole — the header would land only in chunk 1 and chunks 2..N would be
topically anonymous. Cost: the header's chars are paid N times.

**Zero overlap.** Lossless coverage is then expressible as byte equality —
`"\n".join(body_chunks) == body`, which is `_chunk_lines`' own documented
invariant — rather than a weaker union-coverage assertion. 20% overlap would
cost ~25% more rows and embed calls (and the same on migration) to buy
boundary-straddle recall that the document-level collapse already softens.
Recorded as revisitable: overlap is a tuning change behind the same
`EMBED_COMPOSITION_TAG` bump, not a schema change.

**Edge case (seam).** An empty body → zero body chunks → embed the header
alone as one chunk, so a title-only concept still gets a vector.

### D4 — Citation identity for a chunk hit

**Choice.** Chunks collapse to documents **inside `VectorStoreDB.query()`**.
It over-fetches `k × max(chunk_count)` rows (read from `vector_meta`, a small
ordinary table — this is what `chunk_count` is for), keeps each
`concept_id`'s **minimum** distance, sorts by `(distance, concept_id)` in
Python, and returns at most `k` `VecHit`s, **one per `concept_id`** —
the same contract `query()` has today.

Consumers touched: **none change behaviour.** `retrieval/fusion.fuse`,
`lifecycle.filter_hits` (`answer.py:860`), `_assemble_context`,
`_split_attribution`, the positional citation filter (`answer.py:940-944`),
`Citation`, `AnswerResult.dense_hit_count`, `_classify_no_match`,
`query --save` provenance (`cli/main.py:16218-16222`), the CLI citation
renderer, and the 17 `fuse` call sites (including
`evals/query_sufficiency/` and `evals/query_entailment/`). One context block
and one `Citation` per document, as today. **Rejected**: chunk-level blocks
with a later citation collapse — `Citation` would need chunk identity, and
every one of the above would need re-auditing to buy nothing, since
`_assemble_context` re-reads the whole document from disk regardless.

**What does change in meaning** (spec-visible, behaviour-invisible): a
`VecHit`'s distance is now the document's *best chunk* distance, and
`dense_hit_count` counts documents, not chunks. **Residual limit, not
papered over**: the `k` cut still happens inside vec0, so on a store whose
`max(chunk_count)` under-describes the true distribution the collapse can
yield fewer than `k` documents — the same class of residue as
`vectorstore.py:465-472`.

### D5 — Fusion granularity

**`fusion.fuse` receives `concept_id`s. `retrieval-fusion` gets NO spec
delta.** `fusion.py` is not edited. Both sides of RRF remain ranked
`concept_id` lists over the same unit — the document — so RRF's
position-only semantics stay exactly as valid as today, and FTS needs no
change to be comparable. (`_accumulate`'s existing best-rank dedup at
`fusion.py:73-79` would tolerate a leaked duplicate, but the design does not
lean on it: a leak would still distort `dense_hit_count`, the `k` budget, and
`filter_hits`.)

### D6 — Embed-failure isolation grain

**Choice.** **All-or-nothing per document.** If any chunk's embed raises a
generic `OllamaError`, the document is counted `embed_failed`, **nothing** is
upserted for it, and its previous rows (chunks, `doc_vectors`, `vector_meta`)
are left untouched.

A mean over a surviving subset is a silently wrong vector: it type-checks,
it counts as one document, and it would be stored beside a `content_hash`
that says "current" — so the next run reads it as a cache hit and the
wrongness becomes permanent. Precedent: `_fan_out_windows`' caller contract
is all-or-nothing (`concept.py:3243-3246`). `ReindexReport.embedded` /
`embed_failed` / `skipped` keep counting **documents**, so the CLI summary
and the tag-persist gate (`skipped == 0 AND embed_failed == 0`) keep their
exact meaning. The three FATAL subclasses re-raise from the chunk loop
**before** the generic handler, preserving the safety-critical ordering at
`reindex.py:331-350`.

`on_progress` keeps its `(index, total, concept_id)` contract and fires once
per queued **document**, after its chunk set resolves (issue #190 semantics
unchanged). Residue, named not hidden: a 40-chunk document now looks stalled
for 40 calls.

Cost honesty: `ReindexReport` gains `embed_calls: int = 0` (defaulted, so
every existing construction site stays valid — the `Citation.confidential`
precedent). Without it the summary's "N embedded" understates the run's real
work by the chunk multiplier.

### D7 — Sensitivity re-check placement

**No new step is inserted.** Because the collapse lives inside `query()`,
`answer()`'s pipeline is unchanged: `_dense_search` →
`lifecycle.filter_hits(vec_hits, excluded)` → `fuse` → `_assemble_context`'s
per-document fail-closed re-read (`answer.py:438-485`). The load-bearing
invariant, stated so a reviewer can check it in one line: **the vector store
never returns text.** It returns `(concept_id, distance)`; `_assemble_context`
still reads the document body from disk and still runs
`sensitivity.should_block` on that document's freshly re-read frontmatter. No
chunk's content can reach the LLM by any path other than the whole-document
body that already passes both gates.

### D8 — The migration disclosure

The bug: `previous_model_tag` is the stored **effective** tag
(`bge-m3#compose-v1`) while the right-hand side is `cfg.embedding_model`, the
bare model — so a composition-only bump prints a model change nobody made.
Fix: compare like with like, and name the real trigger. `ReindexReport` gains
`effective_model_tag: str | None = None`; the CLI splits both tags at `#` and
prints one of three true lines:

| Case | Line |
|---|---|
| model parts differ | `embedding model changed (<old-model> -> <new-model>)` |
| model parts equal, composition differs | `embed text composition changed (<old-comp> -> <new-comp>); your embedding model is unchanged (<model>)` |
| previous tag absent | `no embedding-model tag stored (fresh or dropped store)` |

Every branch also reports `embed_calls` over `embedded` documents, which is
where the chunk multiplier becomes visible.

**`tests/unit/cli/test_purge.py:801` changes — and so does
`cli/main.py:7630-7634`.** That test asserts on **purge's** output, which
pre-emptively quotes reindex's wording. A purge-dropped store leaves
`previous_model_tag is None`, so it now takes the third branch; purge's line
must quote *that* wording or it becomes a new lie, and the assertion's
substring moves with it. It is a wording-contract test whose subject is being
corrected, not a behaviour test being weakened.

### D9 — The new pair-nomination gate

`evals/pair_nomination/run_pair_nomination_probe.py`. Measures the pair set
`VectorProximitySource.pairs()` nominates over a **real** bundle, pre- and
post-change, same corpus and same embedder.

| Output | Role |
|---|---|
| Set delta: `\|pre ∩ post\|`, lost, gained, Jaccard | Descriptive. A changed set is the point of the fix, never a failure by itself |
| **Margin** = `best_unrelated_distance − worst_related_distance` over a committed hand-labelled fixture (`pair_labels.json`, ids only) | The verdict, following `query_identity`'s paraphrase-worst vs. different-best method. **PASS = post margin ≥ pre margin** |
| Truncation witness: for every multi-chunk document, `cos(doc_vector, first_chunk_vector)` | Ties the gate to the defect. 1.0000 pre-change by construction; must be `< 1.0000` post-change |

Corpus: the 0.2.10 E2E bundle (32 embeddable documents; the two long ones
carry the case). **Commit ids and labels only — never source bodies.**
Interface follows the existing probes: `--bundle`, `--baseline out.json`,
`--compare baseline.json`, `--self-test` (zero model calls), `--rescore`.
Falsifiability guard: print `n of TOTAL` for every filtered count and print
how many labelled `unrelated` pairs the treatment *could* have failed; an
empty unrelated set reports **UNFALSIFIABLE**, never PASS. The baseline must
be captured on **pre-change** code, so this is work unit 1.

## Data Flow

    reindex: doc ──> header + _chunk_lines(body, 12k) ──> N embed calls
                        │ (any chunk fails ⇒ whole doc embed_failed)
                        └──> upsert_many([(cid, [v1..vN], hash)])
                                ├─ DELETE cid from vectors/doc_vectors/vector_meta
                                ├─ INSERT N chunk rows (chunk_index 0..N-1)
                                ├─ INSERT 1 doc_vectors row = norm(mean(norm(vi)))
                                └─ INSERT vector_meta(cid, hash, chunk_count=N)
                                        ── ONE commit per run ──

    query:  question ─> embed ─> vectors KNN (k × max_chunk_count rows)
                                    │
                        collapse: min distance per concept_id, top-k docs
                                    │
            VecHit[concept_id] ─> filter_hits ─> fuse ─> _assemble_context
                                                            (per-doc re-read
                                                             + should_block)

    graph:  neighbors(cid, k) ─> doc_vectors KNN ─> pairs()  [unchanged shape]

## File Changes

| File | Action | Description |
|---|---|---|
| `evals/pair_nomination/run_pair_nomination_probe.py` | Create | D9 gate; baseline captured before any schema change |
| `evals/pair_nomination/pair_labels.json` | Create | Hand-labelled pairs, ids only |
| `src/openkos/state/vectorstore.py` | Modify | `chunk_index`/`chunk_count` columns, `doc_vectors` table, legacy-shape migration, chunk-collapsing `query()`, `neighbors()` reads `doc_vectors`, `upsert_many` item widens |
| `src/openkos/state/reindex.py` | Modify | `_compose_embed_text` split into header + body, `_chunk_lines` reuse at 12k, per-document all-or-nothing chunk loop, `embed_calls`/`effective_model_tag` on the report, `EMBED_COMPOSITION_TAG` → `chunk-v1` |
| `src/openkos/cli/main.py` | Modify | Corrected reindex disclosure (3 branches, `embed_calls`); purge's pre-emptive line realigned |
| `src/openkos/extraction/concept.py` | None | `_chunk_lines` reused as-is via explicit `target=` |
| `src/openkos/retrieval/answer.py`, `retrieval/fusion.py`, `graph/proximity.py` | None | Unchanged by construction (D4, D5, D2) |

## Interfaces / Contracts

```python
# vectorstore.py — the ONE Protocol signature that changes shape.
def upsert_many(
    self, items: Sequence[tuple[str, Sequence[Sequence[float]], str]]
) -> None: ...
#                            ^ chunk vectors for ONE document

# upsert() / prune() / query() / meta_hashes() / prune_many() / commit() /
# read_model_tag() / write_model_tag() / close(): signatures UNCHANGED.
# upsert() is redefined as the 1-chunk case (zero production callers today).
# neighbors() STAYS OFF the Protocol — the dict-fake seam is preserved.
```

Every fake typed `VectorStore` that implements `upsert_many` must adapt. This
is a deliberate, audited break — the alternative (a second `upsert_chunked`
method) leaves the old write path able to store a truncated single vector,
which is the defect.

## Testing Strategy

Strict TDD. Seams for `sdd-tasks` to place RED tests, in dependency order:

| # | Seam | Assertion |
|---|---|---|
| S0 | vec0 spike (`probe_vec_loadable`-gated) | INTEGER metadata column declarable; DELETE-by-`concept_id` removes all N rows; KNN returns duplicate `concept_id`s |
| S1 | Legacy-shape migration | Legacy 3-column store → dropped, recreated, `vector_meta` **empty** |
| S2 | `upsert_many` re-embed at a different chunk count | 12 rows → 5 rows, exactly; zero orphans |
| S3 | `prune_many` | All N chunk rows + `doc_vectors` + `vector_meta` gone, in one commit |
| S4 | Body chunking | `"\n".join(body_chunks) == body`; empty body → 1 header-only chunk |
| S5 | Document vector | `norm(mean(norm(vi)))`; N=1 equals that chunk; result is unit-length |
| S6 | `query()` collapse | ≤ 1 `VecHit` per `concept_id`; distance == min over chunks; ≤ k documents |
| S7 | `neighbors()` / `pairs()` | Missing `doc_vectors` row → `[]`; `pairs()` returns `[]`, never raises |
| S8 | Chunk failure | Chunk 3 of 5 fails → `embed_failed += 1`, zero rows written, prior rows intact |
| S9 | FATAL ladder | The three subclasses re-raise from the chunk loop before the generic catch |
| S10 | Commit count | Still exactly one per run |
| S11 | Disclosure | Composition-only bump prints composition wording (**not** "embedding model changed"); model bump prints model wording; absent tag prints fresh-store wording; purge's line matches |
| S12 | Citation identity | `len(citations)` == distinct document count; `--save` provenance is `concept_id`s |
| S13 | Truncation gone | Multi-chunk document: `cos(doc_vector, chunk_0) < 1.0` |

Integration: `tests/unit/state/test_reindex.py` (gate/prune/tag over N rows),
`tests/unit/graph/test_proximity.py`, `tests/unit/retrieval/test_answer.py`.
E2E: the D9 probe pre/post, plus re-runs of `evals/edge_typing/`,
`evals/contradictions/`, `evals/query_identity/` against their recorded bands.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary. The change is a SQLite
schema, an embed-text windowing rule, and CLI output wording.

## Migration / Rollout

`EMBED_COMPOSITION_TAG` moves `compose-v1` → `chunk-v1`, which forces one
full re-embed through the existing gate — no `--force`, no second version
marker. The schema migration in D1 runs at `open_vector_store` and is
independent of the tag, so a store with `model_tag=None` still migrates
correctly. Cost = Σ over documents of `ceil(len(body) / (12_000 − len(header)))`
embed calls. **That multiplier must be MEASURED on the 32-document 0.2.10
bundle before any user-facing note states a figure** — no number copied from
an issue. Rollback: restore `compose-v1`, revert, `openkos reindex --force`;
`doc_vectors` is additive and droppable, and nothing outside `vectors.db` is
mutated.

Delivery: this exceeds the 400-line review budget. Recommend chained slices —
(1) D9 gate + baseline, (2) vectorstore schema/migration/collapse,
(3) reindex chunking + failure grain, (4) disclosure + purge realignment.

## Open Questions

- [ ] None blocking. Two measurements are deliverables, not unknowns: the
      chunk multiplier on the 32-document bundle, and the S0 vec0 spike
      (whose failure has a recorded contingency in D1).

---

## Orchestrator verification (post-authoring)

Recorded by the orchestrator after this design was authored and before
`sdd-spec` was launched. The design listed the vec0 metadata-column question
as **assumed, not verified**, with decision 1's schema gated behind spike S0.
That spike was run against the installed extension:

```
sqlite-vec version: v0.1.9
CREATE VIRTUAL TABLE vectors USING vec0(
    embedding float[4], concept_id TEXT, chunk_index INTEGER, content_hash TEXT
)                                                     -> accepted
INSERT (3 rows, 2 of them chunks of document 'a')     -> accepted
SELECT concept_id, chunk_index, distance ... MATCH/k  -> chunk_index returned
DELETE FROM vectors WHERE concept_id = 'a'            -> rowcount 2
SELECT count(*) FROM vectors                          -> 1
```

**S0 is retired.** vec0 0.1.9 accepts an `INTEGER` metadata column, returns it
from a KNN projection, and removes all of a document's chunk rows with the
single `DELETE ... WHERE concept_id = ?` that decision 1 depends on. The
rowid-keyed sidecar contingency is not needed.

### Concrete evidence for the tie-ordering risk

The same spike produced an exact distance tie unprompted, in a three-row table:

```
[('a', 0, 0.0), ('b', 0, 1.4142135381698608), ('a', 1, 1.4142135381698608)]
```

`b/0` and `a/1` are equidistant and vec0 returned `b/0` first — insertion
order, exactly as `vectorstore.py:112-118` documents. This is not a
hypothetical under chunking: because the collapse to one hit per document
happens AFTER vec0's `k` cut, a tie at the `k`-th boundary can drop a whole
document rather than merely reorder two chunks of the same one. The design
already records this residue as documented-not-fixed; this note supplies the
reproduction so `sdd-tasks` can place a deterministic-ordering RED test at
the collapse seam rather than treating the hazard as theoretical.
