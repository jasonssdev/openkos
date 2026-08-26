# Tasks: Chunk-Backed Retrieval Vectors (#888)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1,300–1,750 (WU1 ~300–350 eval-only; WU2 ~550–740; WU3 ~320–460; WU4 ~100–170) |
| 2500-line budget risk (session override; default guard label kept below) | Medium — combined total fits comfortably under 2,500, and would even fit as one PR by line count alone |
| Chained PRs recommended | Yes — driven by dependency ordering and independent rollback, not budget overflow |
| Suggested split | PR 1 (D9 baseline, pre-change) → PR 2 (vectorstore schema/migration/collapse) → PR 3 (reindex chunking + failure grain) → PR 4 (disclosure + purge realignment) |
| Delivery strategy | auto-chain |
| Chain strategy | feature-branch-chain |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: Medium

**Why chain even though the raised 2,500-line budget is not exceeded.** The
D9 baseline (WU1) has a hard correctness precondition: it must be captured
on pre-change code, so it must land and be committed before WU2 touches the
schema — that is a real merge boundary regardless of size. WU2/WU3/WU4 are
also each independently revertible per the design's own rollback plan
(schema is additive/droppable; the chunking bump is gated behind
`EMBED_COMPOSITION_TAG`; disclosure/purge wording is cosmetic) and strict
TDD's RED-first discipline naturally produces per-seam boundaries that match
this dependency order (schema before chunking before disclosure, since
disclosure's `embed_calls` field depends on WU3's chunk loop). Bundling them
into one PR would trade away that granularity for headroom the budget does
not actually require.

**Known repo hazard with this chain strategy** (from prior chained-PR
incidents): tracker-targeted branches get no CI by default unless rebased
and force-pushed after each base-branch push, and a stacked PR on a squashed
base goes DIRTY — verify via check-runs on the exact head SHA after every
merge in the chain, not just on open.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | D9 pair-nomination gate + pre-change baseline | PR 1 (base = tracker branch) | `uv run pytest evals/pair_nomination -q` (if tests exist) plus `--self-test` | `python evals/pair_nomination/run_pair_nomination_probe.py --bundle <0.2.10 bundle> --baseline pre.json` against real Ollama | Delete `evals/pair_nomination/`; nothing else depends on it existing |
| 2 | Vectorstore schema, legacy migration, doc vector, query collapse, neighbors, Protocol widen | PR 2 (base = PR 1 branch) | `uv run pytest tests/unit/state/test_vectorstore.py tests/unit/retrieval/test_answer.py tests/unit/graph/test_proximity.py -q` | `probe_vec_loadable()`-gated real-extension tests already in `test_vectorstore.py` | Revert `vectorstore.py` + the three test files; `doc_vectors` table is additive/droppable |
| 3 | Reindex chunking, all-or-nothing failure grain, embed_calls/effective_model_tag, tag bump | PR 3 (base = PR 2 branch) | `uv run pytest tests/unit/state/test_reindex.py -q` | `openkos reindex` against a real workspace with Ollama reachable, on the 0.2.10 32-doc bundle | Revert `reindex.py`; restore `EMBED_COMPOSITION_TAG = "compose-v1"`, run `openkos reindex --force` |
| 4 | Reindex disclosure (3-branch) + purge wording realignment | PR 4 (base = PR 3 branch, merges tracker → main) | `uv run pytest tests/unit/cli/test_reindex_cmd.py tests/unit/cli/test_purge.py -q` | `openkos reindex` output inspected manually once against a fresh/dropped store | Revert the `cli/main.py` disclosure branches and the `test_purge.py:801` assertion together |

Every unit's checkpoint gate: `uv run pytest -q` + `uv run mypy .` + `uv run ruff check . && uv run ruff format --check .`.

## Phase 1: D9 Pair-Nomination Gate (PR 1 — must run on pre-change code)

- [x] 1.1 Create `evals/pair_nomination/pair_labels.json` — hand-labelled `related`/`unrelated` `concept_id` pairs (ids only) from the 0.2.10 E2E 32-document bundle.
- [x] 1.2 Create `evals/pair_nomination/run_pair_nomination_probe.py`: `--bundle`, `--baseline out.json`, `--compare baseline.json`, `--self-test` (zero model calls), `--rescore`; compute set delta (`|pre ∩ post|`, lost, gained, Jaccard — descriptive only), margin (`best_unrelated_distance − worst_related_distance`, PASS = post ≥ pre), truncation witness (`cos(doc_vector, first_chunk_vector)` per multi-chunk doc); print `n of TOTAL` for every filtered count and report `UNFALSIFIABLE` if the labelled `unrelated` set is empty.
- [x] 1.3 Run `--self-test` to validate the harness before spending real Ollama calls.
- [x] 1.4 On current (pre-change) HEAD, run the probe with real Ollama against the 0.2.10 bundle, save `pre.json`. Commit `pair_labels.json`, the probe script, and `pre.json` as PR 1 before any schema work begins.

## Phase 2: Vectorstore Schema, Legacy Migration, Protocol Widen (PR 2)

- [x] 2.1 RED `tests/unit/state/test_vectorstore.py`: opening a legacy 3-column store drops+recreates `vectors`/`doc_vectors`, `vector_meta` ends at 0 rows (S1).
- [x] 2.2 GREEN `vectorstore.py`: `open_vector_store` probes `SELECT chunk_index FROM vectors LIMIT 0`; on `OperationalError`, drop+recreate and clear `vector_meta` inside the existing DDL commit.
- [x] 2.3 RED `test_vectorstore.py`: `vectors` gains `chunk_index INTEGER`; new `doc_vectors(embedding, concept_id)` vec0 table; `vector_meta` gains `chunk_count INTEGER`, reads back correctly.
- [x] 2.4 GREEN `vectorstore.py`: extend schema DDL for the three columns/tables.
- [x] 2.5 RED `test_vectorstore.py`: `upsert_many` re-embed at a different chunk count (12 → 5) leaves exactly 5 rows, zero orphans (S2); one DELETE by `concept_id` removes all N rows across `vectors`/`doc_vectors`/`vector_meta`.
- [x] 2.6 GREEN `vectorstore.py`: widen `upsert_many` to `Sequence[tuple[str, Sequence[Sequence[float]], str]]`; per item DELETE-then-INSERT N chunk rows, keep the no-commit-inside contract.
- [x] 2.7 Same commit as 2.6: adapt the two typed `VectorStore` fakes (`test_vectorstore.py:181`, `test_answer.py:178`) and the direct `db.upsert_many([...])` call sites (`test_vectorstore.py:970,985,1033`) to the new item shape — do not split this across work units or leave unrelated tests red.
- [x] 2.8 RED `test_vectorstore.py`: `prune_many` removes all N chunk rows + `doc_vectors` + `vector_meta` in one call, no commit inside (S3).
- [x] 2.9 GREEN `vectorstore.py`: `prune_many` deletes across all three tables per `concept_id`.
- [x] 2.10 RED `test_vectorstore.py`: `doc_vectors` row = `normalize(mean(normalize(vi)))`; N=1 chunk equals that chunk; result is unit-length (S5).
- [x] 2.11 GREEN `vectorstore.py`: derive and write the document vector inside `upsert_many`.
- [x] 2.12 RED `test_vectorstore.py`: multi-chunk document — `cos(doc_vector, chunk_0) < 1.0` (S13).
- [x] 2.13 RED `test_vectorstore.py`: `query()` collapse — over-fetch `k × max(chunk_count)`, keep min distance per `concept_id`, Python tie-break `(distance, concept_id)`, ≤ k documents (S6); reproduce the design's tie `[('a',0,0.0),('b',0,1.414),('a',1,1.414)]` and assert deterministic `concept_id` ordering, not vec0 insertion order.
- [x] 2.14 GREEN `vectorstore.py`: implement the collapse in `query()`.
- [x] 2.15 RED `tests/unit/retrieval/test_answer.py`: with FTS explicitly disabled (`fts_index=None`) and a real `VectorStoreDB` holding a document whose head chunk is far from the query embedding and tail chunk is close, `_dense_search`/`answer()` retrieves that document through the dense path alone.
- [x] 2.16 RED `test_answer.py`: `len(citations)` equals distinct document count even when the store's pre-collapse rows span multiple chunks of one document; `query --save` provenance stays `concept_id`-only (S12).
- [x] 2.17 RED `test_vectorstore.py`: `neighbors()` reads `doc_vectors`; a zero-chunk document (no `doc_vectors` row) returns `[]` via the existing `row is None` branch, no raise (S7).
- [x] 2.18 GREEN `vectorstore.py`: point `neighbors()` at `doc_vectors`.
- [x] 2.19 RED `tests/unit/graph/test_proximity.py`: `VectorProximitySource.pairs()` over a chunk-derived document vector still never raises; zero-chunk document yields no pairs.
- [x] 2.20 REFACTOR: `uv run mypy .` accepts the widened `upsert_many` Protocol against both adapted fakes; re-run the full `test_proximity.py` suite green.

## Phase 3: Reindex Chunking + Failure Grain (PR 3)

- [x] 3.1 RED `tests/unit/state/test_reindex.py`: body chunking via `_chunk_lines(body, target=12_000 - len(header))` — `"\n".join(body_chunks) == body` byte-for-byte; empty body → one header-only chunk (S4).
- [x] 3.2 GREEN `reindex.py`: split `_compose_embed_text` into `header` (title+description+tags) + `body`; chunk body; each chunk = `header + "\n\n" + body_chunk`.
- [x] 3.3 RED `test_reindex.py`: short doc → 1 chunk/1 embed call; long doc → N chunks/N calls, lossless.
- [x] 3.4 RED `test_reindex.py`: chunk 3-of-5 fails on a generic `OllamaError` → `embed_failed += 1`, zero rows written for that document, prior stored rows untouched, and no partial mean stored — assert the stored `vector_meta`/`content_hash` state directly, not just the counter (S8).
- [x] 3.5 GREEN `reindex.py`: per-document chunk loop, all-or-nothing on any chunk's generic `OllamaError`.
- [x] 3.6 RED `test_reindex.py`: the three FATAL subclasses (`OllamaUnavailable`, `OllamaModelNotFound`, `OllamaEmbeddingDimensionMismatch`) re-raise from the chunk loop before the generic handler (S9) — restore the Missing-model and Dimension-mismatch scenarios per the accepted spec's third-pass correction.
- [x] 3.7 GREEN `reindex.py`: preserve the existing ordered `except` ladder inside the chunk loop, order unchanged.
- [x] 3.8 RED `test_reindex.py`: the run still commits exactly once regardless of chunk count (S10).
- [x] 3.9 GREEN `reindex.py`: `ReindexReport` gains `embed_calls: int = 0` and `effective_model_tag: str | None = None` (defaulted); `embedded`/`embed_failed`/`skipped` keep counting documents.
- [x] 3.10 GREEN `reindex.py`: bump `EMBED_COMPOSITION_TAG` `"compose-v1"` → `"chunk-v1"`.
- [x] 3.11 RED `test_reindex.py`: `on_progress` still fires once per queued document, after its own chunk set resolves.
- [x] 3.12 MEASURED: `openkos reindex` against the real 0.2.10 32-document bundle (`/Users/jasonssdev/openkos-e2e-0210`) with real Ollama (`bge-m3`): 32 documents embedded, **40 total `embed_calls`** — a 1.25x overall multiplier. Only 2 of the 32 documents (`sources/transcription1`, `sources/transcription3`, the two long ones) produced more than one chunk, each exactly 5 chunks (5x for those two specifically); the other 30 stayed single-chunk.

## Phase 4: Disclosure + Purge Realignment (PR 4)

- [x] 4.1 RED (CLI-level disclosure test): three branches — model parts differ → `embedding model changed (...)`; model parts equal, composition differs → `embed text composition changed (...); your embedding model is unchanged (...)`; previous tag absent → `no embedding-model tag stored (fresh or dropped store)` (S11).
- [x] 4.2 GREEN `cli/main.py`: split `previous_model_tag`/`effective_model_tag` at `#`, implement the three-branch comparison, report `embed_calls` over embedded documents in every branch.
- [x] 4.3 RED `tests/unit/cli/test_purge.py:801`: update the pre-emptive quoted wording to the corrected "no embedding-model tag stored (fresh or dropped store)" line.
- [x] 4.4 GREEN `cli/main.py:7630-7634`: realign purge's disclosure line to quote the corrected wording.

## Phase 5: Cross-Cutting Verification (no new production code)

- [x] 5.1 Re-ran `run_pair_nomination_probe.py --compare pre.json` on post-change code (see `evals/pair_nomination/compare-pre-vs-post.txt`). Truncation witness confirms the fix: `max cos(doc, chunk_0)` goes `1.0000 -> 0.9183` for both multi-chunk documents. **Margin does NOT pass**: post `-0.0820` < pre `-0.0489` (FAIL), driven by one labelled-unrelated pair (`sources/transcription1` vs `decisions/necesidad-de-feedback-de-los-tutores`) moving CLOSER after chunking (0.9481 -> 0.9120) — the unweighted mean-of-chunks pooling can make a long, topically diverse document's vector more centroid-like, raising its similarity to unrelated content even as it removes the false 1.0 truncation match. This is a real measured tradeoff, not a harness defect (`n=10/10`, falsifiable) — flagged as a risk for the maintainer, not silently resolved by re-picking labels.
- [ ] 5.2 NOT RUN this batch: `evals/edge_typing/`, `evals/contradictions/`, `evals/query_identity/` against their recorded bands. These need substantial additional real chat-model time on top of an already long batch; none of the changed code paths (fusion.py, resolution/*) were touched by this change, so regression risk there is low, but the bands were not re-measured. Left for a follow-up verification pass.
- [x] 5.3 `answer.py`/`fusion.py`/`proximity.py` have ZERO production diff in this change (confirmed via `git diff` against every commit) — `_assemble_context` is byte-identical, so its output length is unaffected by construction; no separate run was needed to confirm this.
- [x] 5.4 Full gate run: `uv run pytest -q` (5682 passed, 1 skipped, pre-Phase-4 baseline; full final re-run in apply-progress), `uv run mypy .` (clean, 273 files), `uv run ruff check .` + `uv run ruff format --check .` (clean).
