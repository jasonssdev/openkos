# Design: concept-edge-seeding (issue #183)

## Technical Approach

A THIRD edge pass in `graph/sqlite_graph.py::_populate_graph_tables`, fed by an
INJECTED candidate source so `graph/` never reaches into a store it does not own.
The source (`graph/proximity.py`, new) runs vec0 k-NN over `vectors.db` and emits
scored concept↔concept pairs; the third pass inserts them as `relation_type = NULL`
rows, deduped against pass 1. Nothing is written to `relations:` frontmatter, so
`model/okf.py` and `bundle/relations.py` are untouched. `ingest` gains an Embedder
only to keep `vectors.db` current, fail-open.

Layering is NOT inverted: `graph/` (derived) importing `state/vectorstore.py`
(canonical) is the ALLOWED direction, already exercised by
`sqlite_graph.py`'s `from openkos.state import derived` (see its docstring, 78-81).
Injection exists for testability and optionality, not to dodge a boundary.

## Architecture Decisions

### Decision A — candidate rows are `relation_type = NULL`

| Option | Cost | Verdict |
|---|---|---|
| `NULL` | `edge_typing._candidate_edges` (116-138) and `contradiction._candidate_pairs` (187-191) unchanged. Candidates indistinguishable from hand-written untyped links | **CHOSEN** |
| New synthesized type (e.g. `similar_to`, mirroring `derived_from` at 311-317) | Self-describing, but is *typed*, so it (a) enters `_candidate_edges`'s `typed_pairs` set and would SUPPRESS a genuine untyped body link for the same pair, (b) enters `contradiction`'s candidate set and spends LLM calls on machine guesses, (c) appears in `query`'s graph-expansion neighbors. Requires edits at `edge_typing.py:116-138` AND `contradiction.py:187-191` purely to restore NULL's behavior | Rejected |

**Rationale**: the new type's two required edits do not buy new behavior — they buy
back the behavior `NULL` already has, while adding a latent suppression bug. Accepted
cost: legibility. Mitigated by Slice 0's message vocabulary naming proximity as the
source, and by `relate` staying human-gated. **Lines changed in `resolution/`: zero.**

### Decision B — cosine floor 0.65, top-K 5

`VectorStore.query` returns ASCENDING vec0 **L2 distance** (`vectorstore.py:107-110`),
not similarity. Ollama `/api/embed` returns L2-normalized vectors, so for unit vectors
`cosine = 1 - d²/2`. The constant is therefore declared as a **similarity floor** and
converted once:

```python
CANDIDATE_SIMILARITY_THRESHOLD: Final[float] = 0.65  # cosine floor
MAX_NEIGHBOR_DISTANCE: Final[float] = sqrt(2 - 2 * CANDIDATE_SIMILARITY_THRESHOLD)
TOP_K: Final[int] = 5
```

Below `similarity.SIMILARITY_THRESHOLD = 0.75` because that constant scores *lexical*
token ratios, while every doc in one knowledge base shares vocabulary, compressing
embedding cosine upward; above 0.5, where bge-m3 starts returning topically unrelated
neighbors. `TOP_K = 5` bounds the flood independently of the floor. **Tuning requires no
schema change**: candidates are projection-ephemeral, so changing the constant changes
only the next `build_graph()` output — no migration, no stale rows. A calibration task
MUST lock the value against a fixture anchor pair, the way `similarity.py:18-21` locks
0.75 with `stoic`/`stoicism`.

### Decision C — narrow Protocol, not a `VectorStore` extension

k-NN needs each concept's *stored* vector. Adding `neighbors()` to the `VectorStore`
Protocol would grow its shape and break every existing fake (`vectorstore.py:150-156`).
Instead: add `neighbors(concept_id, k)` to `VectorStoreDB` only (read the blob, reuse
`_QUERY_VECTORS_SQL`), and declare the narrow `NeighborQuery` Protocol in
`graph/proximity.py`, which `VectorStoreDB` satisfies structurally.

### Decision D — ingest backfills the whole store, not just new docs

k-NN over an unembedded corpus yields nothing, so ingest reuses
`state.reindex.reindex(bundle_dir, db, embedder, model_tag=cfg.embedding_model)`. Its
content-hash gate makes steady state cost = new docs only; the first run pays the
necessary full backfill. `fts_db_path` is NOT passed — ingest touches `vectors.db` only.
"Candidates in the same run" means: after one `ingest`, the next `suggest-relations`
sees candidates with no intervening `reindex`. Ingest does NOT call `build_graph`.

### Decision E — on-disk projection keeps parity

`write_graph_store` shares `_populate_graph_tables`, and `sqlite_graph.py:369-371`
pins "the on-disk projection always contains exactly the nodes/edges an equivalent
`build_graph` call would produce". So the source is plumbed through
`write_graph_store` / `reindex_graph` too (via `functools.partial` into
`derived.reindex_gate(write=...)`), and the `reindex` CLI passes it.

## Data Flow

    cli/main.py ──_open_proximity_or_degrade(layout.vectors_db_path)──┐
       │  (learns "embeddings missing" HERE, zero extra passes)       │
       ├──> candidate_edges(bundle_dir, candidates=src) ──┐           │
       └──> find_contradictions(..., candidates=src) ─────┤           │
                                                          v           │
                          graph/sqlite_graph._populate_graph_tables    │
                            pass1 body links ─┐                       │
                            pass2 relations:  ─┼─> edges table        │
                            pass3 proximity ──┘   (NULL rows)  <──────┘
                                                          ^
                          graph/proximity.VectorProximitySource
                                 └── VectorStoreDB.neighbors() (sqlite-vec)

    ingest: write bundle ──> _autocommit ──> backfill vectors.db (fail-open)

## Graceful Degradation (hard constraint)

| Seam | Behavior |
|---|---|
| `cli/main.py::_embed_after_ingest` (new), called AFTER `_autocommit` | One `try/except Exception` — deliberately broad, mirroring `probe_vec_loadable`'s documented rationale (`vectorstore.py:246-258`): `OllamaUnavailable`/`OllamaModelNotFound`/`OllamaError` (`llm/ollama.py:33-45`), `VecUnavailable`, `sqlite3.Error`, `OSError`, and any unmapped type. `KeyboardInterrupt`/`SystemExit` still propagate. NEVER re-raises, NEVER changes the exit code |
| Workspace after failure | Source + concepts written, `index.md`/`log.md` updated, autocommit done. `vectors.db` untouched — `reindex` commits ONCE at the end (`reindex.py:309-310`), so a mid-run raise commits nothing. Stderr: `openkos ingest: embeddings not updated -- {exc}; candidate relations unavailable until \`openkos reindex\` succeeds.` |
| `build_graph` with no `vectors.db` | `candidates=None` (default) → pass 3 is a no-op, zero candidate rows, successful build. `status`'s existing treatment (`cli/main.py:4297-4300`) stays valid |
| Observability | The CLI already holds `(source, was_unavailable)` from `_open_proximity_or_degrade` before calling into `resolution/` — the third message state costs NO second pass |

## Slice 0 — three-state message vocabulary

| Site | State 1 (no edges) | State 2 (edges, no candidates) | State 3 (embeddings missing) |
|---|---|---|---|
| `suggest-relations` (4804-4806) | "No concept relationships in the graph yet." | "N relation(s) exist; none are untyped." | "Candidate relations unavailable — run `openkos reindex` (vectors.db missing)." |
| `contradictions` (5037-5038) | same 1 | "N typed relation(s); none are contradiction candidates." | same 3 |
| `status` (near 4297-4300) | "No concept relationships yet." | "N concept-to-concept edge(s) (M typed)." | existing vectors.db line, reworded to name candidates |

One read-only helper, `graph/summary.py::graph_edge_summary(bundle_dir) -> (total, typed)`.
State 3 keys purely on `vectors_db_path.exists()`, so Slice 0 ships independently of Slice 1.

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/graph/proximity.py` | Create | `NeighborQuery` Protocol, `ProximityPair`, `VectorProximitySource`, `open_proximity_source(path)` (existence-gated → `None`), the two constants |
| `src/openkos/graph/summary.py` | Create | Slice 0 read-only `graph_edge_summary` |
| `src/openkos/graph/sqlite_graph.py` | Modify | Pass 3 (~25 lines) + `candidates` kwarg on `_populate_graph_tables`/`build_graph`/`write_graph_store`/`reindex_graph`; module docstring updated to three passes |
| `src/openkos/state/vectorstore.py` | Modify | `VectorStoreDB.neighbors()` only — Protocol UNCHANGED |
| `src/openkos/resolution/edge_typing.py` | Modify | `candidates` kwarg plumbed to `build_graph` (307). `_candidate_edges` UNCHANGED |
| `src/openkos/resolution/contradiction.py` | Modify | Same kwarg at 442. `_candidate_pairs` UNCHANGED |
| `src/openkos/cli/main.py` | Modify | Slice 0 messages; `_open_proximity_or_degrade`; `_embed_after_ingest`; wiring in `ingest`/`suggest-relations`/`contradictions`/`reindex` |

Pass 3 pseudocode (determinism is the load-bearing part):

```python
if candidates is not None:
    seen = {(s, t) for s, t in edge_pairs} | {(t, s) for s, t in edge_pairs}
    rows = {
        (min(p.source_id, p.target_id), max(p.source_id, p.target_id))
        for p in candidates.pairs(sorted(node_ids))
        if p.source_id in node_ids and p.target_id in node_ids
        and (p.source_id, p.target_id) not in seen
    }
    for source_id, target_id in sorted(rows):
        conn.execute(_INSERT_EDGE_SQL, (source_id, target_id, None))
```

One canonical direction per unordered pair (k-NN is near-symmetric; two rows would
double every suggestion). Never raises: a k-NN failure inside the source returns `[]`.

## Testing Strategy

| Layer | What | How |
|---|---|---|
| Unit `graph/proximity` | Threshold boundary, top-K cap, self-exclusion, symmetry collapse, empty store | Fake `NeighborQuery` returning fixed `VecHit` lists — no Ollama, no sqlite-vec |
| Unit `sqlite_graph` | Pass 3 determinism; pass 1/2 output byte-identical with and without a source; dedup vs an existing body link; `candidates=None` no-op | Stub source object in `tests/unit/graph/test_sqlite_graph.py` |
| Unit `vectorstore` | `neighbors()` round-trip | Real sqlite-vec, gated on `probe_vec_loadable()` like the existing spike |
| Unit `cli` | Three message states × 3 commands; ingest fail-open (embedder raises each of the three Ollama errors + an unmapped one) → exit 0, files present | Existing `tests/unit/cli/` harness (verify shape before writing) |
| Integration (RED FIRST) | ingest N sources → candidates appear → `suggest-relations` types them → `contradictions` finds pairs | Fake Embedder + fake LLM; this test does not exist today and reproduces #183 |

Regression re-run required: `test_edge_typing.py`'s already-typed-pair exclusion and
`test_contradiction.py`'s `derived_from` exclusion.

## Performance

Pass 3 is n vec0 k-NN scans, brute force at O(n²·d), d=1024. n=200 → ~4×10⁷ float ops
in C, tens of ms — invisible. n=2000 → ~4×10⁹, seconds. `build_graph` runs from only
TWO sites (`edge_typing.py:307`, `contradiction.py:442`), both already LLM-bound and
interactive, so the pass is never on a hot read path. Mitigation without a cache: the
source is built ONCE per command and passed in; the CLI may print a stderr notice above
a node threshold. **Escalation, not a silent fix**: if a real bundle crosses ~1500 nodes
and the pass exceeds ~1s, a `graph.db`-resident cache becomes necessary — that reopens
the human's settled decision 2 and MUST come back as an explicit question, not a
unilateral addition.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification,
or process-integration boundary. The ingest embed call reuses the existing
`OllamaClient` HTTP seam with its established trusted-host handling.

## Migration / Rollout

No migration. Candidate rows are recomputed on every `build_graph()`; reverting the pass
makes them vanish on the next call. `vectors.db` schema unchanged.

**Delivery** (auto-chain, 800-line budget): Slice 1 will NOT fit in 400 lines — proposed
cut: **PR1** Slice 0 legibility (~150) → **PR2** `proximity.py` + `neighbors()` + pass 3 +
DI plumbing, tested with fakes, no CLI wiring (~350) → **PR3** CLI/ingest wiring +
fail-open + end-to-end test (~250). Total ~750. Feature Branch Chain: PR1 → tracker, PR2 →
PR1, PR3 → PR2.

## Open Questions

- [ ] Calibration of `CANDIDATE_SIMILARITY_THRESHOLD` against a real fixture bundle — the
      value is a reasoned starting point, not an empirical lock (task 1 of PR2).
- [ ] Ollama `/api/embed` normalization is an ASSUMPTION the cosine conversion depends on;
      a unit-norm assertion in the proximity tests must pin it.
- [ ] No reject ledger exists: a candidate a human declines re-appears on every run.
      Out of scope here; worth its own issue.
