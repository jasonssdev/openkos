# Exploration: MVP-2 local embeddings + vector store + hybrid retrieval (openkos)

> Mirror of Engram `sdd/hybrid-retrieval/explore` (id 1340) and discovery id 1342.
> Full analysis for the `hybrid-retrieval` change. Slice 1 is proposed separately
> in `proposal.md`; slices 2–5 are deferred follow-ups captured here for context.

## Confirmed/corrected fact table (claim vs actual, file:line)

1. Ollama client exists under `src/openkos/llm/` — TRUE. `OllamaClient` at
   `llm/ollama.py:46` (stdlib `urllib` only, leaf module, config-free);
   `LLMBackend` Protocol at `llm/base.py:21`. Methods: `chat()` (ollama.py:64),
   `list_models()` (ollama.py:120), `model_tag_matches()` (ollama.py:168). NO
   embeddings method — needs additive `embed()` hitting `/api/embed`.
2. "Existing SQLite state DB + FTS5" — PARTIALLY WRONG (biggest correction). There
   is NO persistent SQLite DB. FTS5 is in-memory `sqlite3(":memory:")`, rebuilt per
   run from bundle `.md` files and dropped on exit (`state/fts.py:155`). The graph
   projection is identical: in-memory, rebuild-per-run (`graph/sqlite_graph.py:219`).
   Bundle `.md` files are the single source of truth; `concept_id` = bundle-relative
   path minus `.md`.
3. Current DB-open path never calls `enable_load_extension` (fts.py:155,
   sqlite_graph.py:219) — no extension loading exists today.
4. Retrieval is FTS-only. `answer()` at `retrieval/answer.py:129` does
   `build_index` → `search` → `_assemble_context` (guarded re-read) →
   `Citation(concept_id, title)` → `llm.chat`. CLI query wires it at
   `cli/main.py:2233`; Ollama-error handling at `main.py:2234-2254`.
5. `qwen3-embedding:0.6b` — TRUE, in the official Ollama library. Native dim 1024
   (MRL-configurable 32–1024).
6. sqlite-vec in deps — FALSE. `pyproject.toml` deps: networkx, python-frontmatter,
   pyyaml, typer only. No sqlite-vec/numpy/httpx. Dev floor py3.13, mypy strict,
   ruff, cov `fail_under=90` branch.
7. Config/model resolution: `config.read_config` (config.py:282),
   `DEFAULT_MODEL="qwen3:8b"` (chat model, config.py:20). Embedding model must be a
   NEW config field defaulting to `qwen3-embedding:0.6b`.
8. Onboarding pattern exists: init non-fatal Ollama preflight (main.py:154-173),
   doctor 5-check scan (main.py:2310+) with `[PASS]`/`[FAIL]`/`[SKIP]` + remediation,
   `OllamaUnavailable`/`OllamaModelNotFound` typed errors.

## Answers to the 5 key questions

- **Q1 Embedder seam**: add `Embedder` Protocol to `llm/base.py` beside `LLMBackend`.
  Contract: `embed(texts: Sequence[str]) -> list[list[float]]`, fixed dim 1024,
  batching via Ollama `/api/embed` input array. Concrete impl: additive `embed()` on
  `OllamaClient` reusing the urllib + typed `OllamaError`-family discipline.
- **Q2 sqlite-vec**: loads via `enable_load_extension(True)` → `sqlite_vec.load(db)` →
  `enable_load_extension(False)`; then `CREATE VIRTUAL TABLE ... USING vec0(embedding
  float[1024], +concept_id TEXT, +content_hash TEXT)`. FEASIBILITY RISK: stock macOS
  Python `sqlite3` frequently ships with extension loading DISABLED — must add a
  doctor-style preflight + typed `VecUnavailable` degradation. Because everything is
  rebuild-per-run in-memory, a pure `:memory:` vec table would re-embed every concept
  every query (Ollama call storm) — embeddings MUST persist (e.g. `.openkos/vectors.db`
  keyed by `concept_id` + `content_hash`).
- **Q3 Hybrid fusion**: RRF (Reciprocal Rank Fusion) — needs only rank order per
  retriever, sidesteps the bm25(lower-better) vs cosine(higher-better) scale mismatch.
  Plug into `answer.py`: FTS search + vector search → fuse to ranked `concept_id` list
  → SAME `_assemble_context`/`Citation` path. Vectors unavailable/empty → pure FTS
  fallback identical to today.
- **Q4 Lifecycle**: lazy compute with persistent content-hash cache (embed only
  concepts whose hash changed) + an explicit backfill/reindex verb. Unit = concept
  (`.md` file), matching FTS row and Citation. Merge edits → `content_hash` changes →
  auto re-embed on next access.
- **Q5 Offline/TDD**: the `Embedder` Protocol makes tests hermetic — inject a fake
  `Embedder` returning deterministic vectors (mirrors the fake `LLMBackend` in
  `answer()` today). Ollama-unavailable reuses `OllamaUnavailable` + doctor/preflight.
  sqlite-vec-unavailable → `VecUnavailable`, degrade to FTS-only, surfaced in doctor.

## Unit of embedding

OKF concept (one `.md` = one `concept_id`), matching the FTS5 row unit and
`Citation.concept_id`. Chunk-level deferred (breaks the concept_id=citation identity).

## Recommended slicing (dependency-ordered)

- **Slice 1 "Embedder seam + Ollama embeddings"** (this proposal): `Embedder`
  Protocol in `llm/base.py`; `embed()` on `OllamaClient` via `/api/embed`;
  `embedding_model` config field; doctor embedding-model check. Hermetic, touches
  NEITHER retrieval NOR extension loading. RECOMMENDED first PR.
- **Slice 2 "Vector store + persistence + feasibility preflight"**: add sqlite-vec dep;
  persistent vec0 cache DB keyed by `concept_id`+`content_hash`;
  `enable_load_extension` preflight + `VecUnavailable` degradation + doctor check;
  `build_vectors`/reindex + backfill verb. Likely exceeds 400 authored lines → split
  into (2a) dep+loader+preflight+doctor and (2b) vec0 store + backfill/reindex.
- **Slice 3 "Hybrid retrieval (RRF fusion) in `answer()`"**: dense search + RRF fuse
  with FTS; keep `_assemble_context`/citations; auto-degrade to FTS-only.
- **Slice 4 "Graph-traversal expansion"**: expand fused candidates via
  `GraphStore.neighbors()` before assembly. Optional/last.
- **Slice 5 (parallel) "two-output rule"**: file a good answer back as a new OKF
  concept. Independent concern.

## Review budget forecast

Slice 1: Low-Medium. Slice 2: Medium-High (split recommended). Slice 3: Medium.
Slice 4: Low-Medium.

## Risks

- macOS stock Python `sqlite3` may have extension loading disabled → sqlite-vec
  unusable; needs preflight + graceful FTS-only degradation (top feasibility risk,
  isolate in Slice 2).
- Embedding persistence is mandatory (rebuild-per-run architecture) — a naive
  `:memory:` vec table would trigger an Ollama call storm.
- New network dependency (embedding model) — keep hermetic via `Embedder` fake; reuse
  `OllamaUnavailable`/doctor.
- Cache invalidation must key on `content_hash` so merges/edits re-embed correctly.
- Adding numpy/sqlite-vec expands the currently-minimal dependency surface; confirm
  licenses and offline-install story.
