# Tasks: `embedding-vector-store` (MVP-2, Slice 2a) — Vector-Store Seam + On-Disk Scaffolding

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~310-430 (prod ~130-160, tests ~220-280, docs ~15-25); `uv.lock` churn excluded |
| 400-line budget risk | Medium |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | auto-chain |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Medium

Additive-only: one new module mirroring `fts.py`'s shape, two config properties, one doctor check, one dep + mypy override. No data flow, threat matrix N/A. Close to 400 due to dual-branch loader tests + a real-extension test; keep as one cohesive PR.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `vectorstore.py` + `WorkspaceLayout` props + doctor #7 | PR 1 | `uv run pytest tests/unit/state/test_vectorstore.py tests/unit/test_config.py tests/unit/cli/test_doctor.py` | `uv run openkos doctor` on uv-managed interpreter (real vec0 load) | Revert PR 1; zero consumers this slice |

## Phase 1: Dependency + mypy Override (`pyproject.toml`)

- [x] 1.1 Add `sqlite-vec` to `dependencies`.
- [x] 1.2 Add `[[tool.mypy.overrides]] module="sqlite_vec.*" ignore_missing_imports=true`.
- [x] 1.3 Run `uv lock` (regenerated `uv.lock`, excluded from authored budget).

## Phase 2: `WorkspaceLayout` Paths (`src/openkos/config.py`)

- [x] 2.1 RED: `tests/unit/test_config.py` — `WorkspaceLayout(root).openkos_dir` == `root/".openkos"`.
- [x] 2.2 RED: `test_config.py` — `.vectors_db_path` == `root/".openkos"/"vectors.db"`.
- [x] 2.3 RED: `tests/unit/cli/test_init.py` — `init` at a fresh root still creates no `.openkos/`, no `vectors.db`.
- [x] 2.4 GREEN: `config.py` — add `openkos_dir`/`vectors_db_path` `@property`; docstring: engine-cache paths, not init-written.

## Phase 3: `content_hash` (`src/openkos/state/vectorstore.py`, new file)

- [x] 3.1 RED: `tests/unit/state/test_vectorstore.py` (new) — `content_hash(b"x")` stable across calls.
- [x] 3.2 RED: `test_vectorstore.py` — differing bytes yield differing hashes.
- [x] 3.3 GREEN: create `state/vectorstore.py` (docstring mirrors `fts.py`); `content_hash(data: bytes) -> str` via `hashlib.sha256(data).hexdigest()`.

## Phase 4: `VecUnavailable` + `VectorStore` Protocol + Loader + Schema

- [x] 4.1 RED: `test_vectorstore.py` — a fake exposing only `close()` satisfies `VectorStore` structurally (mirrors `Embedder`); `mypy` accepts.
- [x] 4.2 RED: `test_vectorstore.py` — injected `connect` stub whose `enable_load_extension` raises `AttributeError` → `open_vector_store` raises `VecUnavailable`; stub closed.
- [x] 4.3 RED: `test_vectorstore.py` — injected stub whose extension load raises `sqlite3.OperationalError` → same `VecUnavailable` + closed-conn assertion.
- [x] 4.4 RED: `test_vectorstore.py` — real interpreter: `open_vector_store(tmp_path/".openkos"/"vectors.db")` succeeds, lazily creates `.openkos/`, `vectors` + `vector_meta` exist.
- [x] 4.5 RED: `test_vectorstore.py` — re-opening the same path is a no-op (idempotent `CREATE IF NOT EXISTS`).
- [x] 4.6 GREEN: `state/vectorstore.py` — `VecUnavailable(RuntimeError)`, `VectorStore` Protocol (`close()` only), DDL interpolating `EMBED_DIM` (from `llm.base`), `open_vector_store(path, *, connect=sqlite3.connect)`: lazy `mkdir` → connect → `enable_load_extension(True)` → `sqlite_vec.load` → `enable_load_extension(False)` → `CREATE IF NOT EXISTS`; catch `AttributeError | sqlite3.Error` → close conn → raise `VecUnavailable`.
- [x] 4.7 GREEN: `state/vectorstore.py` — `VectorStoreDB` ctx-mgr owning the conn (`close()`/`__enter__`/`__exit__`, mirrors `FtsIndex`), returned on success.

## Phase 5: `probe_vec_loadable` + Doctor Check #7 (`src/openkos/cli/main.py`)

- [x] 5.1 RED: `test_vectorstore.py` — real interpreter: `probe_vec_loadable()` returns `bool`, never raises, against `:memory:`.
- [x] 5.2 GREEN: `state/vectorstore.py` — `probe_vec_loadable() -> bool` reusing the guarded-load sequence on `sqlite3.connect(":memory:")`; `False` on `AttributeError`/`sqlite3.Error`, never raises.
- [x] 5.3 RED: `tests/unit/cli/test_doctor.py` — `probe_vec_loadable` stubbed `True` → `[PASS] Vector extension loadable`, `critical=False`, exit unaffected.
- [x] 5.4 RED: `test_doctor.py` — stubbed `False` → `[FAIL]` with extension-capable-interpreter remediation (not system/Homebrew Python); exit stays `0` if critical checks pass.
- [x] 5.5 RED: `test_doctor.py` — check #7 runs (no `[SKIP]`) even when Ollama unreachable / outside a workspace.
- [x] 5.6 GREEN: `cli/main.py` — append `CheckResult` #7 after check 6, calling `probe_vec_loadable()` unconditionally; update docstring to "seven checks".

## Phase 6: Docs + Regression Sweep

- [x] 6.1 DOCS: `docs/cli.md` — "six checks" → "seven checks"; list the new check and its exit-code note.
- [x] 6.2 DOCS: `docs/roadmap.md` — mark `sqlite-vec` on-disk scaffolding delivered (no data flow yet), beside the Slice 1 line.
- [x] 6.3 VERIFY: `uv run pytest tests/unit/state/test_vectorstore.py tests/unit/test_config.py tests/unit/cli/test_doctor.py` green.
- [x] 6.4 VERIFY: `uv run ruff check . && uv run ruff format --check .` clean; `uv run mypy .` (repo-wide) clean.
- [x] 6.5 VERIFY: `uv run pytest --cov` ≥90% branch — both `VecUnavailable` paths, doctor #7 pass/fail, idempotent re-open covered.
