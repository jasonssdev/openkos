# Tasks: Embedding Vector Store — Slice 2b (First Vec0 Consumer + Reindex)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~900-950 (prod ~355, tests ~545, docs ~15) |
| 800-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (store data flow) → PR 2 (reindex orchestrator) → PR 3 (CLI wiring + follow-ups + gitignore/docs) |
| Delivery strategy | auto-forecast (non-standard label; treated as "forecast and recommend, decision still owed") |
| Chain strategy | feature-branch-chain (recommended; PR1 gates PR2/PR3 per design's spike-first ordering) |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
800-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Vec0 spike + `upsert`/`query`/`meta_hashes`/`prune` on `VectorStoreDB`; Protocol extension; follow-ups (a)(b)(c)(d) | PR 1 | `uv run pytest tests/unit/state/test_vectorstore.py -q` | Real `sqlite-vec` ext via spike test (`skipif(not probe_vec_loadable())`) | Revert `vectorstore.py` additions + test file; 2a open-path untouched |
| 2 | `state/reindex.py` orchestrator (walk, hash-cache, embed, upsert, prune, `--force`) | PR 2 | `uv run pytest tests/unit/state/test_reindex.py -q` | N/A — no CLI entrypoint until PR 3; covered by fakes | Delete `state/reindex.py` + `test_reindex.py`; PR 1 unaffected |
| 3 | `reindex` CLI verb + error ladder; `.openkos/` gitignore; docs | PR 3 | `uv run pytest tests/unit/cli/test_reindex_cmd.py -q` | `openkos reindex` against a real initialized workspace with Ollama reachable | Remove CLI command, gitignore line, doc note; PR 1/2 unaffected |

Every checkpoint gate: `uv run pytest -q` + `uv run mypy .` (repo-wide, incl. `tests/`) + ruff/format clean. Fakes MUST declare `Sequence[float]` verbatim (Engram #1363).

## Phase 1: Vec0 Semantics Spike (gates all data-flow work)

- [x] 1.1 RED: `tests/unit/state/test_vectorstore.py` — integration spike, `skipif(not probe_vec_loadable())`: real ext, DELETE-by-`concept_id` + re-INSERT survives (one row), metadata-filtered KNN returns expected `concept_id`/ascending distance.
- [x] 1.2 GREEN: run against real sqlite-vec 0.1.9; if DELETE-by-metadata fails, fall back to DELETE-by-`rowid` (query rowid first) per design's Open Question; record the confirmed syntax.
- [x] 1.3 REFACTOR: extract confirmed DELETE/INSERT/QUERY SQL as module constants for reuse by Phase 2/3.

## Phase 2: Store Data Flow — `upsert`/`query`

- [x] 2.1 RED: `test_vectorstore.py` — first upsert of new `concept_id` inserts exactly one `vectors` + one `vector_meta` row (spec: First Upsert).
- [x] 2.2 RED: `test_vectorstore.py` — re-upsert replaces prior vector, one row remains, `vector_meta.content_hash` updated (spec: Re-Upsert Replaces).
- [x] 2.3 GREEN: add `upsert(concept_id, embedding, content_hash)` to `state/vectorstore.py` — `serialize_float32` → DELETE existing → INSERT → `INSERT OR REPLACE INTO vector_meta` → one `commit`.
- [x] 2.4 RED: `test_vectorstore.py` — `query(embedding, k)` returns ≤k `(concept_id, distance)` ascending (spec: k-NN Ordering); empty store returns `[]`, not an error (spec: Empty Store).
- [x] 2.5 GREEN: add `query(embedding, k)` — `embedding MATCH ? AND k = ? ORDER BY distance`; return `list[VecHit]`.
- [x] 2.6 GREEN: add `VecHit` frozen dataclass (`concept_id`, `distance`) mirroring `FtsHit`.
- [x] 2.7 RED+GREEN: extend `VectorStore` Protocol additively (`upsert`, `query` stubs); assert a fake declaring only `close()` still typechecks (spec: Protocol Extended Additively).
- [x] 2.8 REFACTOR: confirm mypy accepts an extended fake with `close`+`upsert`+`query` against `VectorStore`-typed call sites.

## Phase 3: Cache Accessors — `meta_hashes`/`prune`

- [x] 3.1 RED: `test_vectorstore.py` — `meta_hashes()` returns `{concept_id: content_hash}` for all rows.
- [x] 3.2 GREEN: implement `meta_hashes()`.
- [x] 3.3 RED: `test_vectorstore.py` — `prune(concept_id)` removes matching rows from both `vectors` and `vector_meta`.
- [x] 3.4 GREEN: implement `prune(concept_id)`.

## Phase 4: Reindex Orchestrator (`state/reindex.py`, new)

- [x] 4.1 RED: `tests/unit/state/test_reindex.py` — discovered doc's `concept_id` matches `forget`'s identity (spec: Discovered Doc's Identity); reserved files (`index.md`, `log.md`) never embedded/upserted (spec: Reserved Files Excluded).
- [x] 4.2 GREEN: `reindex.py` walks via `okf._iter_docs`, derives `concept_id` = relative path minus `.md`.
- [x] 4.3 RED: cache-hit (unchanged `content_hash`) makes zero fake-`Embedder` calls, stored vector unchanged (spec: Cache-Hit Zero Calls).
- [x] 4.4 RED: changed hash re-embeds + upserts (spec: Changed Content); new doc (no `vector_meta` row) embeds + inserts (spec: New Doc).
- [x] 4.5 GREEN: implement content-hash gate against `db.meta_hashes()`; embed+upsert on changed/absent, skip on match.
- [x] 4.6 RED: doc removed from disk but present in `vector_meta` is pruned from both tables (spec: Deleted Doc Pruned).
- [x] 4.7 GREEN: after the walk, `prune()` every `vector_meta` `concept_id` not seen on disk.
- [x] 4.8 RED: `--force` re-embeds every doc even when hashes match (spec: Force Bypasses Cache).
- [x] 4.9 GREEN: add `force: bool` param bypassing the hash-gate check.
- [x] 4.10 GREEN: add `ReindexReport(embedded, cache_hits, pruned, skipped)` dataclass; `reindex()` returns it.
- [x] 4.11 REFACTOR: dedupe embed-batch call (single `embedder.embed([...])` per changed/new batch vs. per-doc) if straightforward; else document per-doc rationale.

## Phase 5: CLI Reindex Wiring

- [x] 5.1 RED: `tests/unit/cli/test_reindex_cmd.py` — successful run prints embedded/cache-hit/pruned summary, exits 0 (spec: Successful Run).
- [x] 5.2 RED: run outside a workspace exits non-zero, clear stderr, no traceback (spec: Run Outside Workspace).
- [x] 5.3 GREEN: `cli/main.py` `reindex` command — `require_workspace` → `read_config` → `open_vector_store(vectors_db_path)` → call orchestrator → print summary.
- [x] 5.4 RED: `OllamaUnavailable` → `OllamaModelNotFound` → generic `OllamaError`/`VecUnavailable` each exit 1 with a clear message, mirroring `query`'s ladder (spec: Error Ladder Mirrors Query).
- [x] 5.5 GREEN: wire the same ordered `except` ladder `query` uses, substituting `VecUnavailable` for `FtsUnavailable`.
- [x] 5.6 RED+GREEN: add `--force` flag wiring through to the orchestrator.
- [x] 5.7 REFACTOR: confirm `query` command behavior is byte-identical (spec: No Retrieval Consumer Introduced) — no shared-code regression.

## Phase 6: Deferred 2a Follow-Ups

- [x] 6.1 (a) RED+GREEN: `test_vectorstore.py` — new/extended test: workspace root + its other files survive a failed `open_vector_store` after `.openkos/` was created (spec: Workspace Root Survives); doc the single-level cleanup invariant in `open_vector_store`'s docstring.
- [x] 6.2 (b) Rename the stale `# --- Review Correction 2 ...` label (`test_vectorstore.py:~476`) to a durable section name; no runtime scenario.
- [x] 6.3 (c) RED+GREEN: test that a second `close()` call does not raise (spec: Idempotent Double-Close); document this in `VectorStoreDB.close`'s docstring.
- [x] 6.4 (d) RED+GREEN: pre-create a real non-empty `vectors.db`, force a failing reopen (ext load or DDL), assert file + bytes survive unchanged (spec: Pre-Existing DB Survives Failed Reopen).

## Phase 7: Gitignore + Docs

- [x] 7.1 Add `.openkos/` to the repository-root `.gitignore` (design Decision #4).
- [x] 7.2 Update any user-facing docs/README mentioning CLI verbs to list `reindex`.

## Apply Notes (deviations from literal task wording)

- **Task 2.7 wording vs. verified mypy behavior**: the literal task text says
  "assert a fake declaring only `close()` still typechecks." Verified directly
  against mypy (a standalone repro `Protocol` with `close`+`upsert`, assigning
  a close-only class to the Protocol-typed variable) that this is NOT how
  Python Protocol structural typing works: a Protocol's abstract methods are
  ALL required, with no partial/optional subset, so a close-only fake cannot
  satisfy an extended Protocol that also declares `upsert`/`query`/
  `meta_hashes`/`prune`. The delta spec's own acceptance scenario ("Extended
  fake satisfies the Protocol") already matches this reality: it describes a
  fake implementing `close`, `upsert`, AND `query`, not a close-only fake.
  Implemented per the spec scenario: the Slice 2a close-only Protocol test
  was replaced with `test_extended_fake_satisfies_vector_store_protocol_structurally`,
  which fully implements the 2b-extended `VectorStore` Protocol and documents
  this in its docstring. No deviation from the delta spec itself — only from
  one imprecise sentence in the task list.
- **Spike outcome (Phase 1)**: both real-extension spike tests passed on the
  first run against sqlite-vec 0.1.9 — DELETE-by-`concept_id` (metadata
  column) and `embedding MATCH ? AND k = ? ORDER BY distance` both work as
  designed. The DELETE-by-rowid fallback documented in design's Open
  Questions was never needed.
- **Task 4.11 (batch dedupe)**: implemented directly in the initial GREEN
  pass rather than as a separate refactor step — `reindex()` collects every
  changed/new doc first, then issues exactly ONE `embedder.embed([...])`
  call per `reindex()` invocation (not one call per doc).
- **Tasks 6.1/6.4 (follow-ups a, d)**: both new tests passed immediately
  against the existing Slice 2a `open_vector_store` implementation with zero
  production-code changes needed -- the single-level cleanup invariant and
  the pre-existing-db survival guarantee were already correctly implemented
  in 2a. These tasks added regression-locking test coverage plus the
  requested docstring notes, not new behavior.
- **Line count vs. forecast**: the forecast estimated ~900-950 changed lines;
  the actual diff is ~1315 insertions / 25 deletions (~1340 total), driven by
  more thorough spec-derived test coverage than the estimate assumed
  (`test_vectorstore.py` +382, `test_reindex.py` +300, `test_reindex_cmd.py`
  +267). Delivered as the single PR with `size:exception` per the
  orchestrator's resolved delivery decision; flagged here for the reviewer's
  awareness since it exceeds the original estimate, though still within the
  same approved exception.
