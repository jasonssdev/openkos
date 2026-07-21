# Proposal: Persist Derived FTS + Graph Indexes (Slice 5 — performance-caching)

## Intent

Every `openkos query` runs a fresh one-shot process and `answer()` rebuilds all
derived indexes from scratch: `fts.build_index` (`retrieval/answer.py:259`) and
`sqlite_graph.build_graph` (`answer.py:214`) each open `:memory:`, walk ALL docs,
re-parse frontmatter, and are dropped on block exit. Dense retrieval already
avoids this — `vectors.db` is written once by `reindex`, read by `query`. Slice 5
extends that same persist-once / read-many contract to FTS and graph so per-query
recompute disappears.

## Scope

### In Scope
- Persist FTS + graph projections to on-disk SQLite under `.openkos/`, mirroring the `vectors.db` writer/reader split.
- `reindex` = SOLE writer of all derived indexes; `query`/`answer()` read handles read-only, degrade to empty when absent/stale.
- Cache key = bundle-manifest hash (digest over sorted `(concept_id, content_hash)` set) in a meta table; whole-index rebuild on manifest change.
- Rewire `answer()` to accept injected index handles (DI, mirroring `vector_store`).
- Follow-ups folded in: (1) strengthen `test_empty_question_*` with spies proving builders are never called on the empty-query short-circuit; (2) DRY the `max(limit,10)` pool cap into a named floor; (3) prune-skip observability (report field + CLI line); (4) WAL/busy_timeout PRAGMAs + one-commit-per-run chunking in the reindex write-path.

### Out of Scope
- PPR caching — not cacheable (personalization is per-query seed-dependent; only the query-independent graph BUILD is cached).
- Per-doc incremental FTS/graph updates (cross-doc graph edges forbid safe incremental maintenance).
- Follow-up #5 model-tag in `vector_meta` — deferred to its own slice (orthogonal schema migration).

## Capabilities

### New Capabilities
- `derived-index-cache`: on-disk SQLite persistence of FTS + graph projections under `.openkos/`, with a manifest-hash meta table gating whole-index rebuild.

### Modified Capabilities
- `reindex-command`: becomes sole writer of FTS + graph indexes (not just vectors); adds prune-skip observability and WAL/busy_timeout + chunked commit.
- `query-answer`: `answer()` reads injected FTS/graph handles via DI and degrades to empty when absent/stale; no longer builds indexes per-query.
- `fts-state`: `build_index` contract shifts from "rebuild-per-run `:memory:`" to persisted on-disk store written by reindex.
- `graph-projection`: `build_graph` contract shifts from rebuild-per-run to persisted.
- `query-command`: "no persisted state / no CLI-level graph command" docstring/contract updated.

## Approach

Copy the shipped `vectors.db` lifecycle (`open_vector_store`, `vectorstore.py:205`)
for FTS and graph: `reindex` opens on-disk handles (`fts.db`, `graph.db` or one
`derived.db`), rebuilds when the bundle-manifest hash differs, and commits once
per run under WAL. `query`/`answer()` open the same handles read-only via injected
DI and degrade to empty results (printing the existing reindex hint) when absent
or stale. Reuse the Slice 2b `content_hash` primitive; the manifest hash is an
order-stable digest over the sorted `(concept_id, content_hash)` set.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `retrieval/answer.py:214,257,259,271` | Modified | Replace internal `build_index`/`build_graph` with injected handles; DRY pool floor |
| `state/fts.py:136-194` | Modified | Add on-disk persist path alongside build logic |
| `graph/sqlite_graph.py:203-279` | Modified | Add on-disk persist path for graph build |
| `state/reindex.py:120-129` | Modified | Write FTS+graph indexes; prune-skip field; chunk commits |
| `state/vectorstore.py:242-248` | Modified | WAL/busy_timeout PRAGMAs at open |
| `cli/main.py:2226,2440` | Modified | Query/graph docstring; prune-skip CLI line |
| New persistence module(s) | New | Manifest-hash meta table + on-disk FTS/graph handles |
| `tests/unit/retrieval/test_answer.py:618,758,1103` | Modified | Spy that builders are never called on empty-query short-circuit |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Freshness regression — FTS/graph stale until `reindex` | High | Document; keep existing reindex hint; dense already behaves this way |
| Contract shift across 4 archived seams' docstrings + query spec | Med | Update spec deltas + docstrings in-scope; enumerated above |
| SQLite write contention (3 on-disk writers) | Med | Follow-up #4: WAL + busy_timeout + one commit per run |
| Manifest hash instability | Low | Sort concept_ids before digesting (order-stable) |
| >400 changed lines exceeds review budget | High | Forecast chained PRs at sdd-tasks: (a) FTS persist, (b) graph persist, (c) answer/query rewire + follow-ups |

## Rollback Plan

Feature is additive: `reindex` writes new `.openkos/` derived DBs and `answer()`
gains DI handles. Revert the change set; `answer()` falls back to per-query
`:memory:` build. Delete the new `.openkos/*.db` derived files; `vectors.db` and
bundle sources are untouched.

## Dependencies

- Shipped Slice 2b `content_hash` / `vector_meta` primitives and the `vectors.db` writer/reader lifecycle.
- Strict gate: `uv run pytest -q` + `uv run mypy .` repo-wide; new modules mirror `vectorstore.py` typing/guard discipline.

## Success Criteria

- [ ] `openkos query` performs zero FTS/graph builds when up-to-date derived indexes exist.
- [ ] `reindex` is the only writer of FTS + graph indexes; `query` opens them read-only.
- [ ] Editing/adding/removing a doc changes the manifest hash and triggers a whole-index rebuild on next `reindex`.
- [ ] `query` degrades to empty (with reindex hint) when derived indexes are absent or stale.
- [ ] Follow-ups #1–#4 landed; gate green.
