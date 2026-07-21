# Design: `embedding-vector-store` (Slice 2a) — vector-store seam + on-disk scaffolding

## Technical Approach

Additive infrastructure, no data flow. New `state/vectorstore.py` mirrors `state/fts.py`'s guarded-open + connection-ownership posture (fts.py:136-194), but persists to disk instead of `:memory:`. It adds a `VecUnavailable` typed error (mirrors `FtsUnavailable`, fts.py:49), a minimal `VectorStore` Protocol seam (mirrors `Embedder`/`LLMBackend`, llm/base.py:21-42), a guarded extension loader that opens/creates `<root>/.openkos/vectors.db` with an idempotent vec0 + companion schema, and a `content_hash` helper. `WorkspaceLayout` gains two read-only path props (config.py:74-98). Doctor gains an informational check #7 that probes loadability without touching disk. Slice-1 hybrid-retrieval contracts (`EMBED_DIM=1024`) are the single source of truth for vector width.

## Architecture Decisions

| Decision | Choice | Rejected alternative | Rationale |
|---|---|---|---|
| Seam kind | `VectorStore` **Protocol** (structural) | ABC | Matches `Embedder`/`LLMBackend` (llm/base.py). `FtsIndex` is a concrete class, but no consumer inherits it; the seam exists for Slice-3 fake injection. |
| Protocol surface (2a) | Lifecycle only: `close()` | Pre-declare `upsert`/`query` stubs now | YAGNI: no consumer exists in 2a. 2b extends the Protocol additively; with no 2a fakes wired into `answer.py`, additions break nothing. |
| Loader exception mapping | Catch `AttributeError` (attr absent, system Python) + `sqlite3.Error` (superclass of `OperationalError` when extensions disabled) → `VecUnavailable`; `close()` conn before raising | Catch only `OperationalError` | `AttributeError` is the dominant system-Python failure; `sqlite3.Error` is the widest safe SQLite catch. Closing before raise mirrors fts.py:190-192 (never leak the conn). |
| `.openkos/` mkdir placement | Lazy, inside `open_vector_store` (`path.parent.mkdir(parents=True, exist_ok=True)` at open) | mkdir in `init` | Honors proposal: `init` requirements unchanged; `WorkspaceLayout` props are pure path derivation. dir is created only when a caller actually opens the store (2a: integration tests only). |
| Doctor #7 probe | In-memory `:memory:` load probe (`probe_vec_loadable() -> bool`) | Open the on-disk db | Doctor is read-only (main.py:2339) and must create no files. `:memory:` exercises the same guarded load without a `.openkos/` mkdir. |
| Companion table | `vector_meta(concept_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL)` | Query vec0 aux columns for cache hits | vec0 aux columns aren't efficiently keyable by `concept_id`; an indexed plain table gives O(1) 2b cache-hit / prune lookups. PK = one row per concept. |
| `content_hash` location/input | `content_hash(data: bytes) -> str` in `vectorstore.py`, sha256 hexdigest over **raw .md bytes** | `fsio` util; hash over decoded text | Co-located with the cache-key consumer (`vector_meta`); 2b `reindex` imports it. Raw bytes avoids encoding-normalization ambiguity; byte edits/merges change the hash → correct re-embed. |
| vec0 width | Interpolate `EMBED_DIM` into DDL (`float[{EMBED_DIM}]`) | Hard-code `1024` | Single source of truth with the Slice-1 contract constant; trusted int, not user input (no injection surface). |

## Data Flow

Slice 2a wires NO retrieval data flow. Only lazy open + schema-ensure:

    open_vector_store(vectors_db_path)
      └─ mkdir .openkos/ ─→ sqlite3.connect(vectors.db)
           └─ enable_load_extension(True) ─→ sqlite_vec.load ─→ enable_load_extension(False)
                └─ CREATE ... IF NOT EXISTS (vectors vec0 + vector_meta) ─→ VectorStoreDB
           (any failure) ─→ conn.close() ─→ raise VecUnavailable

    doctor #7: probe_vec_loadable() on :memory: ─→ CheckResult(pass|fail, critical=False)

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/state/vectorstore.py` | Create | `VecUnavailable`, `VectorStore` Protocol, `open_vector_store`, `VectorStoreDB` (ctx mgr owning conn, mirrors `FtsIndex`), `probe_vec_loadable`, `content_hash`, schema DDL |
| `src/openkos/config.py` | Modify | Add `openkos_dir` + `vectors_db_path` props (config.py:74-98); note in dataclass docstring they are engine-cache paths, not init-written |
| `src/openkos/cli/main.py` | Modify | Append informational check #7 "Vector extension loadable" after check 6 (main.py:2489); update doctor docstring check list |
| `pyproject.toml` | Modify | Add `sqlite-vec` to `dependencies`; add `[[tool.mypy.overrides]] module="sqlite_vec.*" ignore_missing_imports=true` after line 112 |
| `uv.lock` | Modify | Regenerated (generated churn, excluded from authored review budget) |

## Interfaces / Contracts

```python
class VecUnavailable(RuntimeError): ...          # mirrors FtsUnavailable

class VectorStore(Protocol):                     # minimal 2a seam; 2b adds upsert/query
    def close(self) -> None: ...

def content_hash(data: bytes) -> str: ...        # sha256 hexdigest over raw .md bytes
def probe_vec_loadable() -> bool: ...            # :memory: load probe, never raises
def open_vector_store(                           # lazy mkdir + guarded load + schema
    path: Path, *, connect: Callable[[str], sqlite3.Connection] = sqlite3.connect
) -> "VectorStoreDB": ...                         # raises VecUnavailable on load failure
```

`connect` injection is the hermetic seam for the VecUnavailable unit test (a stub conn whose `enable_load_extension` raises), avoiding monkeypatching. Any test fake for `VectorStore` must match param types EXACTLY (Engram #1363: `mypy .` repo-wide rejected Sequence-vs-list); 2a's `close()`-only surface is trivially exact.

Security note: `enable_load_extension(False)` is re-asserted immediately after `sqlite_vec.load`, closing the SQL-level `load_extension()` surface; the loaded extension is the pinned vendored wheel, not a user path.

## Testing Strategy

| Layer | What to Test | Approach |
|---|---|---|
| Unit | Loader raises `VecUnavailable` on absent/failed extension; conn closed | Inject `connect` returning a stub whose `enable_load_extension` raises `AttributeError` and (separate case) `sqlite3.OperationalError` |
| Unit | `content_hash(bytes)` stable sha256; `WorkspaceLayout.openkos_dir`/`vectors_db_path` resolve | Direct assertions |
| Unit | Doctor #7 emits pass/fail, `critical=False`, never flips exit | Stub `probe_vec_loadable` both branches; assert exit code unchanged |
| Integration | Real vec0 loads on uv-managed interpreter; schema created; re-open idempotent no-op | tmp `.openkos/vectors.db`; assert `vectors` + `vector_meta` exist; one-line runtime assert vec0 loads (explore risk) |

Branch coverage (90%): loader success (integration) + both `VecUnavailable` exception paths (unit); doctor #7 pass + fail.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary. The only native surface is in-process SQLite extension loading, guarded and immediately re-disabled (see security note).

## Migration / Rollout

No migration. Idempotent `CREATE ... IF NOT EXISTS` IS the migration posture (mirrors `sqlite_graph`). Additive/isolated rollback: remove `vectorstore.py`, the two props, doctor #7, the dep + lock + mypy override, new tests. No stored vectors, no retrieval touch.

## Open Questions

- [ ] `.openkos/` gitignore convention (rebuildable cache) — noted by explore, defer to 2b `reindex` which populates it; not a 2a blocker.
