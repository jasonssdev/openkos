# Tasks: concept-edge-seeding (issue #183)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | PR1 ~150, PR2 ~350, PR3 ~250 (~750 total) |
| 400-line budget risk | Medium (each PR stays under 400; Slice 1 does not) |
| Chained PRs recommended | Yes |
| Suggested split | PR1 Slice 0 legibility -> PR2 proximity module + pass 3 (fakes only) -> PR3 CLI/ingest wiring + e2e |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: Medium

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Three-state message vocabulary (`status`, `suggest-relations`, `contradictions`) | PR1 | `uv run pytest tests/unit/cli/test_status.py tests/unit/cli/test_suggest_relations.py tests/unit/cli/test_contradictions.py` | `openkos status` / `openkos suggest-relations` / `openkos contradictions` on demo bundle | `git revert` PR1; message-selection only, no schema/behavior change |
| 2 | `graph/proximity.py` + `VectorStoreDB.neighbors()` + pass 3, fakes only | PR2 | `uv run pytest tests/unit/graph/test_sqlite_graph.py tests/unit/state/test_vectorstore.py tests/unit/resolution/test_edge_typing.py tests/unit/resolution/test_contradiction.py -k proximity or candidates` | N/A — no CLI wiring yet, unit-only by design | `git revert` PR2; `build_graph(candidates=None)` default keeps behavior unchanged if reverted |
| 3 | CLI/`ingest` wiring, fail-open degrade, end-to-end regression test | PR3 | `uv run pytest tests/unit/cli/ -k "ingest or suggest_relations or contradictions or status"` and the new e2e test | `openkos ingest` on a fixture bundle with reachable/unreachable Ollama | `git revert` PR3; candidate rows are projection-ephemeral, vanish on next `build_graph()` |

## PR1 -- Slice 0: Three-State Message Vocabulary (targets: main)

Satisfies: specs/status, specs/llm-edge-production, specs/contradiction-detection.

### Phase 1.1: Read-only edge summary helper

- [x] 1.1.1 RED: `tests/unit/graph/test_summary.py` (new) -- `graph_edge_summary(bundle_dir)` returns `(total, typed)` for zero edges, some typed, all typed.
- [x] 1.1.2 GREEN: create `src/openkos/graph/summary.py` with `graph_edge_summary(bundle_dir) -> tuple[int, int]`, read-only over the graph projection.

### Phase 1.2: `status` three-state reporting

- [x] 1.2.1 RED: `tests/unit/cli/test_status.py` -- add cases for state 1 (no edges, vectors.db present), state 2 (edges present, counts reported), state 3 (vectors.db absent/empty -> distinct message), per specs/status scenarios.
- [x] 1.2.2 GREEN: `src/openkos/cli/main.py` (near 4228-4306, next to the existing vectors.db line at 4297-4300) -- wire `graph_edge_summary` and the three-state message logic; existing exit-0 behavior unchanged.

### Phase 1.3: `suggest-relations` three-state messaging

- [x] 1.3.1 RED: `tests/unit/cli/test_suggest_relations.py` -- add the three scenarios from specs/llm-edge-production (empty graph, typed-only, embeddings missing).
- [x] 1.3.2 GREEN: `src/openkos/cli/main.py` (4804-4806) -- branch on `graph_edge_summary` + `vectors_db_path.exists()` to select the message.

### Phase 1.4: `contradictions` three-state messaging

- [x] 1.4.1 RED: `tests/unit/cli/test_contradictions.py` -- add the three scenarios from specs/contradiction-detection (no typed edges, typed-but-excluded, embeddings missing).
- [x] 1.4.2 GREEN: `src/openkos/cli/main.py` (5037-5038) -- same three-state branch, reusing the helper from 1.2.2/1.3.2.

### Phase 1.5: PR1 gate

- [x] 1.5.1 `uv run pytest` green; branch coverage >= 90%.
- [x] 1.5.2 Confirm zero changes outside `graph/summary.py` (new) and `cli/main.py`'s three message sites -- PR1 has no dependency on PR2.

## PR2 -- Candidate Scoring Module + Pass 3 (targets: PR1 branch)

Satisfies: specs/candidate-edge-seeding, specs/graph-projection. Fakes only, NO CLI wiring.

### Phase 2.1: Unit-norm assumption pin (MUST run first)

- [x] 2.1.1 RED+GREEN together (assertion test, not a behavior to implement): `tests/unit/state/test_vectorstore.py` or a new `tests/unit/llm/test_ollama_embed_norm.py` -- assert Ollama `/api/embed` output vectors are L2-normalized (`||v|| ~= 1`), gated like the existing `probe_vec_loadable` real-interpreter tests. If this fails, STOP -- `MAX_NEIGHBOR_DISTANCE` is meaningless and design Decision B must be reopened.
- [x] 2.1.2 Calibration task: fixture anchor pair (mirroring `resolution/similarity.py:18-21`'s `stoic`/`stoicism` lock) -- assert two topically-close fixture concept embeddings fall within `CANDIDATE_SIMILARITY_THRESHOLD = 0.70`, and one topically-unrelated pair falls outside it. Document the anchor pair in the module docstring of `graph/proximity.py`.

### Phase 2.2: `VectorStoreDB.neighbors()`

- [x] 2.2.1 RED: `tests/unit/state/test_vectorstore.py` -- `neighbors(concept_id, k)` round-trip test, gated on `probe_vec_loadable()`, real sqlite-vec.
- [x] 2.2.2 GREEN: `src/openkos/state/vectorstore.py` -- add `neighbors(self, concept_id: str, k: int) -> list[VecHit]` to `VectorStoreDB` only (reads the stored blob, reuses `_QUERY_VECTORS_SQL`). Do NOT add to the `VectorStore` Protocol (150-156) -- would break every existing fake.

### Phase 2.3: `graph/proximity.py` -- scoring module

- [x] 2.3.1 RED: `tests/unit/graph/test_proximity.py` (new) -- threshold boundary (at/above/below `MAX_NEIGHBOR_DISTANCE`), top-K cap (`TOP_K = 5`), self-exclusion, symmetry collapse (one canonical direction per unordered pair), empty store -> `[]`. Use a fake `NeighborQuery` returning fixed `VecHit` lists -- no Ollama, no sqlite-vec.
- [x] 2.3.2 GREEN: create `src/openkos/graph/proximity.py` -- `NeighborQuery` Protocol, `ProximityPair` dataclass, `VectorProximitySource`, `open_proximity_source(path) -> VectorProximitySource | None` (existence-gated), `CANDIDATE_SIMILARITY_THRESHOLD = 0.70`, `MAX_NEIGHBOR_DISTANCE = sqrt(2 - 2 * CANDIDATE_SIMILARITY_THRESHOLD)`, `TOP_K = 5`. Never raises: a k-NN failure inside the source returns `[]`.

### Phase 2.4: Pass 3 in `sqlite_graph.py`

- [x] 2.4.1 RED: `tests/unit/graph/test_sqlite_graph.py` -- pass 3 determinism (stable edge order/dedup) with a stub source object; pass 1/2 output byte-identical with and without a source; dedup vs an existing body link; `candidates=None` -> no-op (existing tests must still pass unchanged).
- [x] 2.4.2 GREEN: `src/openkos/graph/sqlite_graph.py` -- add `candidates` kwarg to `_populate_graph_tables`; implement pass 3 (~25 lines) per the design pseudocode: dedup against `edge_pairs` (both directions), one canonical `(min, max)` row per pair, `relation_type = NULL`, sorted insertion order. Update module docstring to reflect three passes.
- [x] 2.4.3 GREEN: `src/openkos/graph/sqlite_graph.py` -- add `candidates` kwarg to `build_graph`, `write_graph_store`, `reindex_graph` (plumbing only, per Decision E / `sqlite_graph.py:369-371` on-disk/in-memory parity).

### Phase 2.5: `resolution/` kwarg plumbing (no filter changes)

- [x] 2.5.1 RED: `tests/unit/resolution/test_edge_typing.py` -- existing already-typed-pair exclusion re-run unchanged; add a case confirming a `candidates` kwarg reaches `build_graph` at `edge_typing.py:307` and NULL-typed candidate rows surface via `_candidate_edges` (116-138) unmodified.
- [x] 2.5.2 GREEN: `src/openkos/resolution/edge_typing.py` -- plumb `candidates` kwarg to the `build_graph` call at line 307. `_candidate_edges` (116-138) stays untouched.
- [x] 2.5.3 RED: `tests/unit/resolution/test_contradiction.py` -- existing `derived_from` exclusion re-run unchanged; add a case confirming NULL-typed candidate rows are excluded from `_candidate_pairs` (187-191) the same way `derived_from` is.
- [x] 2.5.4 GREEN: `src/openkos/resolution/contradiction.py` -- plumb `candidates` kwarg to the `build_graph` call at line 442. `_candidate_pairs` (187-191) stays untouched.

### Phase 2.6: Invariant assertions (cheap, explicit)

- [x] 2.6.1 Test: `tests/unit/bundle/` or existing merge/unmerge suite -- assert a bundle containing pass-3 candidate rows leaves `model/okf.py` `Relation` encode/decode and `bundle/relations.py` merge/unmerge behavior byte-identical to a bundle without them (projection-ephemeral invariant from specs/candidate-edge-seeding).

### Phase 2.7: PR2 gate

- [x] 2.7.1 `uv run pytest` green; branch coverage >= 90%.
- [x] 2.7.2 Confirm no CLI wiring landed (`cli/main.py` untouched in this PR) and PR2's diff targets PR1's branch cleanly (no PR1 changes leaking in).

## PR3 -- CLI/Ingest Wiring + Fail-Open Degradation + End-to-End (targets: PR2 branch)

Satisfies: specs/ingestion, remaining wiring for specs/graph-projection, specs/candidate-edge-seeding.

### Phase 3.1: RED-first end-to-end reproduction of #183

- [x] 3.1.1 RED: new `tests/unit/cli/test_candidate_edges_e2e.py` -- fake Embedder + fake LLM: ingest N sources -> candidate edges appear -> `suggest-relations` types them -> `contradictions` finds pairs. This test does not exist today; must fail first, reproducing issue #183's symptom.

### Phase 3.2: `_open_proximity_or_degrade` seam

- [x] 3.2.1 RED: `tests/unit/cli/test_suggest_relations.py` / `test_contradictions.py` -- CLI holds `(source, was_unavailable)` before calling into `resolution/`, so the "embeddings missing" message (PR1's state 3) is driven by this seam, not a second pass.
- [x] 3.2.2 GREEN: `src/openkos/cli/main.py` -- add `_open_proximity_or_degrade(layout.vectors_db_path)` wrapping `graph.proximity.open_proximity_source`; wire into `suggest-relations`, `contradictions`, `status` (replacing the ad hoc `vectors_db_path.exists()` check from PR1 with the shared seam), and `reindex`.

### Phase 3.3: `ingest` embedder wiring + fail-open degrade

- [x] 3.3.1 RED: `tests/unit/cli/test_ingest.py` -- ingest with a reachable fake embedder produces candidate edges in the same run (Decision D: ingest reuses `state.reindex.reindex(bundle_dir, db, embedder, model_tag=cfg.embedding_model)`, no `fts_db_path`).
- [x] 3.3.2 RED: `tests/unit/cli/test_ingest.py` -- ingest with an embedder raising each of `OllamaUnavailable`, `OllamaModelNotFound`, `OllamaError`, and one unmapped exception type -> exit 0, Source + concepts still written, distinct stderr message (not the existing concept-extraction-skipped message): `"openkos ingest: embeddings not updated -- {exc}; candidate relations unavailable until \`openkos reindex\` succeeds."`
- [x] 3.3.3 GREEN: `src/openkos/cli/main.py` -- add `_embed_after_ingest`, called AFTER `_autocommit`, with the deliberately broad `try/except Exception` (mirroring `probe_vec_loadable`'s rationale, `vectorstore.py:246-258`); `KeyboardInterrupt`/`SystemExit` still propagate; never re-raises, never changes exit code.
- [x] 3.3.4 GREEN: `src/openkos/cli/main.py` -- construct an `Embedder` in the `ingest` command path (near 1424) and pass it to `_embed_after_ingest`.

### Phase 3.4: `build_graph(candidates=None)` zero-candidate success path

- [x] 3.4.1 RED: `tests/unit/graph/test_sqlite_graph.py` -- `build_graph()` with absent/empty `vectors.db` succeeds, yields zero candidates (regression guard now exercised through the real CLI seam, not just the stub from PR2).
- [x] 3.4.2 GREEN: confirm `_open_proximity_or_degrade` returns `None` cleanly for this case (should already hold from 3.2.2 -- this task is verification, not new code).

### Phase 3.5: PR3 gate

- [x] 3.5.1 `uv run pytest` green; branch coverage >= 90% (`fail_under = 90`, branch coverage on).
- [x] 3.5.2 Full regression re-run: `tests/unit/resolution/test_edge_typing.py`, `tests/unit/resolution/test_contradiction.py`, `tests/unit/graph/test_sqlite_graph.py`.
- [x] 3.5.3 Confirm PR3's diff targets PR2's branch cleanly.

## Known Limitations (recorded, not tasked)

- No reject ledger: a declined candidate re-appears on every run (its own future issue).
- Performance ceiling ~1500 nodes / ~1s for the brute-force O(n^2 * d) k-NN pass is an explicit escalation for the human if crossed, not a task in this change (a `graph.db`-resident cache would reopen the settled ephemeral-only decision).
- Out of scope: LLM-emitted extraction-time links, Pydantic/instructor adoption, durable candidate persistence, a k-NN result cache.
