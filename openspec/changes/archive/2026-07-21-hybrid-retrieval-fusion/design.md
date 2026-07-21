# Design: Hybrid Retrieval Fusion (MVP-2 Slice 3)

## Technical Approach

Approach A + pure RRF helper (proposal-approved, spec-confirmed). Fusion runs
inside `answer()` between `FtsIndex.search()` and `_assemble_context()`. The
CLI builds and injects the dense seams (`Embedder` + open `VectorStore`),
mirroring the existing `llm` injection, so `answer.py` stays config-free. RRF
math is extracted to a pure, zero-I/O helper `retrieval/fusion.py`. FTS is the
mandatory backbone; only the dense side degrades.

## Architecture Decisions

### Decision: RRF helper contract
**Choice**: `fusion.fuse(fts_hits: list[FtsHit], vec_hits: list[VecHit]) -> list[str]`.
Score `Σ 1/(K_RRF+rank)`, `K_RRF=60`, 1-based rank per list *as given* (no
re-sort), equal weights, dedup by best rank within a list, order by score desc
then `concept_id` asc. **No truncation** — caller slices.
**Alternatives**: generic `Sequence[Sequence[str]]` N-list fuse (rejected —
spec pins the 2-arg `FtsHit`/`VecHit` signature; graph is an explicit non-goal);
truncation inside fuse (rejected — spec: caller truncates).
**Rationale**: matches spec exactly; pure and table-testable; imports only the
two frozen dataclasses (no config).

### Decision: Injection + store lifetime
**Choice**: `answer(question, *, bundle_dir, llm, embedder: Embedder | None = None,
vector_store: VectorStore | None = None, limit=5)`. The CLI owns open/close via
a context manager and passes an already-open store; `answer.py` never calls
`open_vector_store` or imports config.
**Alternatives**: `answer()` opens the store (rejected — pulls config/lifecycle
into a leaf); inject a query callable (rejected — spec names `embedder` +
`vector_store` params and fake-both testing).
**Rationale**: hermetic — fake `Embedder` + fake `VectorStore` fully exercise it.

### Decision: Degrade boundary (dense-only)
**Choice**: read-path degrade lives *inside* `answer()`, wrapping only the dense
sub-phase (`embedder.embed([q])[0]` → `vector_store.query`). Empty/whitespace
question or `vector_store is None` skips dense entirely. Store-open failure
(absent db, `VecUnavailable`) is handled by the CLI (passes `vector_store=None`).
**Rationale**: the read happens inside `answer()`, so its degrade must too;
open lives in the CLI where config/paths live.

### Decision: AnswerResult additive fields
**Choice**: add `dense_hit_count:int`, `fused_count:int`, `cited_count:int`,
`dense_degraded:bool`; `no_match_cause` success sentinel moves `"none"` → `None`
(type widens to `NoMatchCause | None`, dropping `"none"`), per spec. Existing
`fts_hit_count`/`llm_invoked`/`skip_notices`/`citations` unchanged. `dense_degraded`
(beyond the proposal's two) is the precise CLI hint signal (set True only when
`answer` catches `VecUnavailable`/`sqlite3.Error`).
**Rationale**: spec is authoritative; `dense_degraded` avoids firing the reindex
hint on a healthy store that simply found no dense match.

## Data Flow

    question ─┬─► FtsIndex.search(pool)      ─┐
              └─► embed→VectorStore.query(pool)─┤ (degrade→[] on Vec/sqlite err)
                                                ▼
                         fusion.fuse(fts, vec) ──► truncate(limit)
                                                ▼
                    _assemble_context(bundle_dir, concept_ids) ──► llm.chat

`pool = max(limit, 10)`. `_assemble_context` now takes `list[str]` concept_ids
(fused, truncated) instead of `list[FtsHit]` — it only needs `.concept_id`.
`zero_hits` = fused list empty (both retrievers empty).

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `retrieval/fusion.py` | Create | Pure RRF `fuse`; `K_RRF=60`; imports `FtsHit`/`VecHit` only |
| `retrieval/answer.py` | Modify | Inject `embedder`+`vector_store`; `_dense_search` + degrade; fuse+truncate; additive `AnswerResult`; reclassify no-match; `_assemble_context(list[str])` |
| `cli/main.py` (`query`) | Modify | Build `OllamaClient(cfg.embedding_model)`; existence-gated `open_vector_store` via `nullcontext`/`VecUnavailable`-degrade; inject; extend `retrieval:` line; reindex hint; `OllamaModelNotFound` msg uses `{exc}` (names real model) |
| `state/reindex.py` | Modify | Walk-error prune guard via `okf._walk_errors`; opportunistic decode-branch cleanup (guarded) |
| `tests/unit/retrieval/test_fusion.py` | Create | RRF table tests |
| `tests/unit/{retrieval/test_answer,cli/test_query,state/test_reindex}.py` | Modify | Hermetic + degrade + prune tests |

## Degrade Matrix

| Cause | Detected at | Handling | Exit |
|-------|-------------|----------|------|
| `vectors.db` absent (cold) | CLI `path.exists()` false | `vector_store=None`; dense skip; hint | 0 |
| `VecUnavailable` at open | CLI `except` | `vector_store=None`; dense skip; hint | 0 |
| `VecUnavailable`/`sqlite3.Error` at `query` | `answer()` `except` | dense=[]; `dense_degraded=True`; hint | 0 |
| Ollama down / embed model missing | propagates `OllamaError` | CLI ladder (`{exc}` names model) | 1 |
| `FtsUnavailable` | propagates | CLI ladder | 1 |

CLI reindex hint fires iff `store_was_unavailable or result.dense_degraded`.
Query never *creates* `vectors.db` (read-only invariant).

### reindex prune guard
Before the prune loop, call `okf._walk_errors(bundle_dir)`; if the returned
`list[OSError]` is non-empty, skip the entire prune pass (embed + cache-hit
passes unchanged). Prevents destroying a valid vector when an unreadable
subtree makes a live doc look absent.

## Interfaces / Contracts

```python
K_RRF = 60
def fuse(fts_hits: list[FtsHit], vec_hits: list[VecHit]) -> list[str]: ...

def answer(question: str, *, bundle_dir: Path, llm: LLMBackend,
           embedder: Embedder | None = None,
           vector_store: VectorStore | None = None,
           limit: int = 5) -> AnswerResult: ...
```

Stderr line: `retrieval: {fts} FTS + {dense} dense → {fused} fused → LLM
{invoked|skipped} → {cited} cited`. STDOUT unchanged.

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| Unit | `fuse` math/order/ties/dedup/empty | Pure table tests, `FtsHit`/`VecHit` fixtures, zero I/O |
| Unit | `answer` fusion, dense-only hit, degrade (`VecUnavailable`/`sqlite3.Error`), empty-query skip, counts, config-free import | Fake `Embedder` (`embed(self, texts: Sequence[str]) -> list[list[float]]`, `EMBED_DIM=1024`, signature EXACT for mypy) + fake `VectorStore` implementing all 5 Protocol methods exactly |
| Unit | reindex walk-error prune skip; control (no errors prunes) | Monkeypatch/simulate `_walk_errors` non-empty; fake store |
| CLI | absent/VecUnavailable/corrupt store → FTS-only, exit 0, hint; extended stderr line; STDOUT clean; model-not-found msg | Typer runner, monkeypatched seams |
| Real-ext | fuse over real sqlite-vec read path | Gated on `probe_vec_loadable()` |

Gate: `uv run pytest -q` + `uv run mypy .` (repo-wide incl `tests/`) + ruff.
Fakes match Protocols exactly; hermetic (no real Ollama/extension except the
probe-gated read test).

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary. Dense read is a local sqlite
query via an injected seam.

## Migration / Rollout

No migration. `vectors.db` is read-only here; dense injection is
optional/additive; reverting restores FTS-only `query`.

## Open Questions

- [ ] Spec vs task-hint conflict: `no_match_cause` success sentinel `"none"` →
  `None` and new `cited_count`/`dense_degraded` fields change/extend
  `AnswerResult`. Design conforms to the on-disk spec; confirm the spec author
  intends the sentinel change (tasks/apply must update CLI `!= "none"` → `is not None`).
- [ ] Proposal's opportunistic "dead decode-branch removal" (#4) has no spec
  requirement and no clearly-unreachable branch in current `reindex.py`; keep
  only if the exploration finding pins an exact unreachable branch, guarded by a
  regression test — otherwise drop from scope.
