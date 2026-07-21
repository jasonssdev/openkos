# Design: Persist Derived FTS + Graph Indexes (Slice 5)

## Technical Approach

Extend the shipped dense persist-once/read-many contract (`open_vector_store`,
`vectorstore.py:205`; reindex writer `reindex.py`; read-only query open
`_open_vector_store_or_degrade`, `main.py:2186`) to FTS and graph. `reindex`
becomes the SOLE writer of every derived index; `answer()` reads injected
read-only handles and degrades to empty when a handle is absent or corrupt —
identical to today's dense split. Whole-index rebuild is gated by a bundle
manifest hash. No per-query build survives.

## Architecture Decisions

### D1 — Persistence layout
| Option | Tradeoff | Verdict |
|--------|----------|---------|
| Separate `.openkos/fts.db` + `.openkos/graph.db` | 1 module = 1 file = 1 lifecycle; independent chained PRs; writes hit different files (least contention); per-file rollback/rebuild | **CHOSEN** |
| One shared `derived.db` | Fewer files, but couples FTS+graph lifecycle/commits and forces both PRs into one schema/module; contention concentrates | Rejected |
| Fold into `vectors.db` | Couples fts5 + vec0 virtual tables + migration to unrelated store; max blast radius | Rejected |

Each file mirrors `vectors.db` exactly (lazy `.openkos/` create on successful
open, single-level failure cleanup, context-manager handle).

### D2 — Manifest-hash cache key
- **Digest input**: reuse Slice 2b `content_hash(raw_bytes)` (`vectorstore.py:134`).
  Manifest = sha256 over the sorted-by-`concept_id` set of `(concept_id,
  content_hash)` pairs, canonically joined (`f"{cid}\x00{hash}\n"`). Sorting
  guarantees order-stability.
- **Storage**: a `meta(key TEXT PRIMARY KEY, value TEXT)` table in EACH derived
  db; row `('manifest_hash', <digest>)`. Key-value shape leaves room for later
  keys without migration.
- **Staleness detection**: gate lives in **reindex only**. reindex computes the
  current manifest, compares to stored `meta.manifest_hash`; equal (and not
  `force`) → skip whole rebuild; differ/absent → rebuild whole index + rewrite
  meta. **Query does NOT recompute the manifest** — a full-bundle walk at query
  time would reintroduce the cost this slice removes. Edit-freshness is handled
  exactly as dense already handles it: the `reindex` hint. Shared helper
  `bundle_manifest_hash(bundle_dir)` in new `state/derived.py`.
- **Degrade-to-empty**: query opens each derived db existence-gated (never
  creates); absent/corrupt → empty handle → `answer()` sees zero hits.

### D3 — reindex as sole writer
Extend `reindex()` (`reindex.py:58`) to also open `fts.db`/`graph.db` writers,
compute the manifest, and — when changed or `force` — DROP+rebuild the whole
FTS and graph projections against the on-disk connections (build logic reused
byte-for-byte from `fts.build_index`/`sqlite_graph.build_graph`, retargeted from
`:memory:` to disk), then write `meta.manifest_hash`. **Follow-up #4**: set
`PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout` at open (all three stores) and
batch to ONE commit per index per run (dense currently commits per-doc,
`vectorstore.py:287`). **Follow-up #3**: add `prune_skipped: bool` (walk-error
signal) to `ReindexReport` + a CLI line at `main.py:2440`.

### D4 — answer() DI rewiring
`answer()` (`answer.py:220`) gains `fts_index` and `graph_store` params
mirroring `vector_store` (each `| None = None`); it stops calling
`fts.build_index` (`answer.py:259`) and `sqlite_graph.build_graph`
(`answer.py:214`). `None` → degrade to `[]`. CLI adds
`_open_fts_or_degrade`/`_open_graph_or_degrade` (mirror
`_open_vector_store_or_degrade`, `main.py:2186`) and wires all three handles at
`main.py:2281`. Because no build happens at query time anymore, the empty-query
short-circuit (`answer.py:247`) trivially touches nothing — **follow-up #1**
tests spy the injected handles to prove `search`/graph-read are never called.

### D5 — DRY pool floor
New `retrieval/pool.py`: `POOL_FLOOR = 10` + `def pool_limit(limit): return
max(limit, POOL_FLOOR)`. Imported by `answer.py` (257, 271) and
`graph_retrieve.py:69` — a leaf module both depend on, no import cycle.

### D6 — Migration / back-compat
Additive: first run has no `fts.db`/`graph.db` → query degrades to empty (FTS +
graph absent → dense-only or no-match), never crashes. First `reindex` creates
them lazily (like `open_vector_store`). Rollback = delete the new `.openkos/*.db`
files; `vectors.db` and sources untouched. No schema migration for existing users.

## Data Flow

    reindex ─(sole writer)─→ vectors.db / fts.db / graph.db  [meta.manifest_hash]
                                        │ read-only
    query CLI ── open×3 (degrade→None) ─┘
       └─→ answer(vector_store, fts_index, graph_store) ─→ fuse ─→ answer

## File Changes
| File | Action | Description |
|------|--------|-------------|
| `state/derived.py` | Create | `bundle_manifest_hash`, WAL/busy_timeout open helper, `meta` DDL |
| `retrieval/pool.py` | Create | `POOL_FLOOR` + `pool_limit()` |
| `state/fts.py` | Modify | On-disk persisted writer + read-only open |
| `graph/sqlite_graph.py` | Modify | On-disk persisted writer + read-only open |
| `state/reindex.py` | Modify | Write FTS+graph; `prune_skipped`; chunked commit |
| `state/vectorstore.py` | Modify | WAL/busy_timeout PRAGMAs; one-commit-per-run |
| `retrieval/answer.py` | Modify | DI handles; drop inline builds; use `pool_limit` |
| `cli/main.py` | Modify | Read-only opens; wire handles; prune-skip line; docstrings |
| `tests/unit/retrieval/test_answer.py` | Modify | Spy builders never called on empty query |

## Testing Strategy
| Layer | What | Approach |
|-------|------|----------|
| Unit | manifest digest order-stability; meta gate skip/rebuild; degrade-to-empty on absent/corrupt | fake stores, temp dbs |
| Unit | empty-query builds nothing | spy injected handles (#1) |
| Integration | reindex sole-writer round-trip; query zero-build read | temp bundle + real sqlite |

## Threat Matrix
N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary. Pure SQLite persistence + DI.

## Migration / Rollout
No data migration. Additive files; delete-to-rollback. Freshness regression
(indexes stale until `reindex`) is documented and mirrors shipped dense behavior.

## Chained-PR Forecast (for sdd-tasks)
`400-line budget risk: High.` Natural seams: **(a)** FTS persistence + shared
`state/derived.py` manifest/WAL infra + `retrieval/pool.py`; **(b)** graph
persistence (reuses (a)'s infra); **(c)** `answer()` DI rewire + CLI wiring +
follow-ups #1–#4 + docstring/spec deltas. Recommend chained PRs.

## Open Questions
- None blocking. Exact home of `pool_limit` (`retrieval/pool.py` vs inside
  `graph_retrieve.py`) is a tasks-phase mechanical choice.
