# Proposal: `embedding-vector-store` (Slice 2a) — vector-store seam + on-disk scaffolding

## Intent

MVP-2 needs dense retrieval, but openkos has NO persistent SQLite state — FTS5
and the graph are both `:memory:`, rebuilt per run (`state/fts.py:155`,
`state/sqlite_graph.py:219`). Without an on-disk vector cache, every run would
re-embed the whole bundle → an Ollama call-storm. This slice lays the FIRST
on-disk store and a hermetic seam, with NO data flow yet: infrastructure only,
so later slices plug in safely. Builds on shipped Slice 1 `hybrid-retrieval`
(Embedder Protocol, `EMBED_DIM=1024`).

## Scope

### In Scope

- Add `sqlite-vec` dependency (Apache-2.0/MIT; first binary/platform-wheel dep)
  to `pyproject.toml` + `uv.lock`; mypy `ignore_missing_imports` override for the
  untyped `sqlite_vec.*` module (CI runs `mypy .` repo-wide).
- New `src/openkos/state/vectorstore.py`: `VecUnavailable` typed error (mirrors
  `FtsUnavailable`); `VectorStore` Protocol seam (fake-injectable, mirroring the
  Slice-1 Embedder/LLMBackend pattern); a GUARDED extension loader
  (`enable_load_extension(True)` → `sqlite_vec.load(conn)` → `enable_load_extension(False)`,
  catching system-Python-no-extension as `VecUnavailable`); open/create
  `<root>/.openkos/vectors.db` with `CREATE VIRTUAL TABLE IF NOT EXISTS vectors
  USING vec0(embedding float[1024], concept_id TEXT, content_hash TEXT)`
  (idempotent CREATE = migration) plus a companion plain table for hash-keyed lookups.
- `WorkspaceLayout.openkos_dir` + `vectors_db_path` (`config.py:74-98`).
- A `sha256` `content_hash` helper over concept `.md` bytes.
- Doctor check #7 "Vector extension loadable" — INFORMATIONAL / non-fatal (never
  flips exit code), reusing the accumulate-never-raise `CheckResult` pattern.

### Out of Scope (non-goals — deferred slices)

- vec0 upsert/query DATA FLOW, `reindex`/backfill verb, `content_hash`
  invalidation flow, RRF hybrid fusion, ANY change to `retrieval/answer.py`.
- `numpy` (NOT needed — `sqlite_vec.serialize_float32` is struct-based; RRF needs
  only ranks). Deferred to slices 2b, 3, 4, 5.
- `init` behavior: `.openkos/` is created lazily by `reindex` (2b), not here.

## Capabilities

### New Capabilities

- `vector-store`: the `VectorStore` Protocol seam, `VecUnavailable` typed error,
  guarded sqlite-vec extension loader, on-disk `.openkos/vectors.db` open/create +
  vec0 schema, and the `content_hash` helper. (Scaffolding only — no store/query.)

### Modified Capabilities

- `doctor-command`: add informational check #7 "Vector extension loadable"
  (non-fatal, never flips exit code; remediation names non-extension Python).
- `workspace-init`: None. Layout gains `openkos_dir`/`vectors_db_path` properties,
  but `init` behavior/requirements do not change in 2a (no `.openkos/` creation).

## Approach

`vectorstore.py` mirrors `state/fts.py`: a module-level guarded `open_vector_store`
opens `vectors.db`, attempts the extension load inside try/except, and raises
`VecUnavailable` when `enable_load_extension` is absent (system Python) or the load
fails. On success it runs the idempotent vec0 + companion `CREATE ... IF NOT EXISTS`
(migration posture matches `sqlite_graph`). A `VectorStore` Protocol keeps Slice-3
tests hermetic via a fake. Doctor reuses the `CheckResult` accumulate pattern; the
new check reports loadable / not-loadable without changing exit status.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `pyproject.toml`, `uv.lock` | Modified | Add `sqlite-vec`; mypy override |
| `src/openkos/state/vectorstore.py` | New | Seam, `VecUnavailable`, loader, schema, hash helper |
| `src/openkos/config.py` | Modified | `openkos_dir` + `vectors_db_path` |
| `src/openkos/cli/main.py` | Modified | Doctor check #7 (informational) |
| `tests/unit/state/`, `tests/unit/cli/`, `tests/integration/` | New/Modified | Fake-conn `VecUnavailable` unit + real vec0 CI integration |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Shipped wheel on system/Homebrew Python without extensions | Med | Mandatory `VecUnavailable` → FTS-only degradation; doctor #7 warns |
| `sqlite_vec` untyped → mypy strict repo-wide fails | Med | `ignore_missing_imports` override task |
| First binary/platform-wheel dep (offline install, future non-ubuntu CI) | Low | CI ubuntu-only today; note wheel caching for later matrices |
| `uv.lock` churn inflates diff | Low | Generated lock lines excluded from authored review budget |

## Rollback Plan

Additive and isolated: remove `state/vectorstore.py`, the two `WorkspaceLayout`
properties, doctor check #7, the `sqlite-vec` dep + lock entry + mypy override, and
new tests. No stored/queried vectors, no migration, no touch to retrieval.

## Dependencies

- `sqlite-vec` (Apache-2.0/MIT). Empirically confirmed this session: uv-managed
  python-build-standalone 3.13.13 (sqlite 3.50.4) supports `enable_load_extension`;
  CI matrix 3.13+3.14 same → the real vec0 path runs green in CI.

## Review Workload Forecast

Authored estimate: prod ~110–150, tests ~180–240, docs ~20–40 →
**~310–430 authored lines**. `uv.lock` adds large GENERATED churn, excluded from
the authored review-budget threshold. Under the 800-line budget →
**single PR, no chaining.** `Decision needed before apply: No`.
`Chained PRs recommended: No`. `400-line budget risk: Low-Medium`.

## Success Criteria

- [ ] `VectorStore` Protocol exists; a fake satisfies it structurally in tests.
- [ ] Guarded loader raises `VecUnavailable` when `enable_load_extension` is
      absent/fails (unit) AND loads vec0 + creates schema on CI (integration).
- [ ] `<root>/.openkos/vectors.db` opens/creates with the idempotent vec0 +
      companion tables; re-open is a no-op.
- [ ] `WorkspaceLayout.openkos_dir`/`vectors_db_path` resolve correctly.
- [ ] `content_hash(bytes)` returns a stable sha256 digest.
- [ ] Doctor check #7 reports loadable/not-loadable and NEVER flips exit code.
- [ ] `uv run pytest`, `ruff check .`, `mypy .` repo-wide, 90% branch cov all green.
