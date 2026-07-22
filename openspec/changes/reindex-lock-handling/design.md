# Design: Reindex Lock-Contention Handling

## Technical Approach

Close the ladder-1 asymmetry (proposal `sdd/reindex-lock-handling/proposal`, explore
`#1495`, main @ 34c083b). A single errorcode-based predicate classifies a locked
`sqlite3.OperationalError`; both reindex ladders and the fts CREATE path reuse it so a
locked `vectors.db`/`fts.db`/`graph.db` exits 1 with ONE uniform retry message and no
traceback. Additive error handling only — no retry/backoff, no busy_timeout change, no
reader-path edits.

## Architecture Decisions

### Decision: Lock predicate home
**Choice**: `is_lock_contention(exc: sqlite3.OperationalError) -> bool` returning
`exc.sqlite_errorcode in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)`, added to
`state/derived.py`.
**Alternatives**: new `state/errors.py` module; message-substring match.
**Rationale**: `derived.py` is already the shared derived-store infra module and is
already imported by `fts.py` (`from openkos.state import derived`, fts.py:49) and
reachable from `cli/main.py` — no new module. Errorcode (SQLITE_BUSY=5, LOCKED=6) is
version-safe on Python >=3.13; substring matching is fragile.

### Decision: Ladder-1 catch placement (cli/main.py:2504-2545)
**Choice**: add `except sqlite3.OperationalError as exc:` as a sibling clause placed
immediately BEFORE the existing `(VecUnavailable, FtsUnavailable, OllamaError)` handler
(2543). The `try` at 2504 already wraps the entire `with open_vector_store(...)` block
(2505) plus the `reindex_module.reindex(...)` call, so open-time, upsert/commit, and
`BEGIN IMMEDIATE` locks all fall inside it. In the handler: `if is_lock_contention(exc):`
emit `_LOCK_CONTENTION_MSG` + exit 1; else `raise` (re-raise unchanged).
**Alternatives**: emit a generic clean message for non-lock OperationalError too.
**Rationale**: OperationalError is unrelated to the Ollama/Vec/Fts RuntimeError subclasses,
so ordering vs. them is behaviour-neutral; placing it adjacent keeps the lock branch
readable. Re-raising non-lock OperationalError preserves current behaviour and keeps the
change strictly additive to the documented lock gap — a generic swallow would hide
genuine schema/programming faults that should surface loudly.

### Decision: Ladder-2 message (cli/main.py:2610-2617)
**Choice**: keep the intentionally-broad `except sqlite3.Error as exc` and branch the
message inside it: `if isinstance(exc, sqlite3.OperationalError) and is_lock_contention(exc):`
emit `_LOCK_CONTENTION_MSG`; else keep the current `failed while writing the graph index`
text. Exit 1 either way.
**Alternatives**: narrow to a separate `except sqlite3.OperationalError` before the broad
catch.
**Rationale**: graph's broad catch already covers permission/IO/corrupt cases; only the
message needs a lock sub-branch. Reusing the predicate avoids duplicating exit logic and
keeps a single catch clause.

### Decision: fts.py CREATE misclassification (fts.py:195-200)
**Choice**: in `_populate_docs_table`, change the except to
`except sqlite3.OperationalError as exc: if is_lock_contention(exc): raise` (propagate the
lock error) `else: raise FtsUnavailable(...) from exc`.
**Rationale**: a genuine "no such module: fts5" carries `SQLITE_ERROR` (1), not BUSY/LOCKED,
so it stays `FtsUnavailable`; a lock surfacing at CREATE propagates as the raw
OperationalError and is caught by the refined ladder-1 handler instead of being mislabeled.

### Decision: Single message source of truth
**Choice**: module-level `_LOCK_CONTENTION_MSG` in `cli/main.py`, referenced by both
ladders. fts.py never emits user text (it re-raises), so the constant lives only where the
CLI speaks.
**Text**: `openkos reindex: failed -- another process is holding the workspace lock (a
concurrent reindex?); wait for it to finish, then try again.`

## Data Flow

    open_vector_store / upsert_many / commit / BEGIN IMMEDIATE ─┐
    write_fts_index → _populate_docs_table (CREATE)  ───────────┼→ OperationalError
                                                                │   is_lock_contention?
    ladder1 try(2504) ── except OperationalError ───────────────┘   ├ yes → _LOCK_CONTENTION_MSG, exit 1
    ladder2 except sqlite3.Error(2612) ── lock sub-branch ──────────┘ └ no  → re-raise / graph generic msg

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/state/derived.py` | Modify | Add `is_lock_contention` predicate |
| `src/openkos/state/fts.py` (~195-200) | Modify | Errorcode-discriminated CREATE catch |
| `src/openkos/cli/main.py` (~2504-2545) | Modify | Ladder-1 lock catch + `_LOCK_CONTENTION_MSG` const |
| `src/openkos/cli/main.py` (~2610-2617) | Modify | Ladder-2 lock message sub-branch |
| `tests/...reindex...` | Create | Lock injection at each write surface |

## Interfaces / Contracts

```python
def is_lock_contention(exc: sqlite3.OperationalError) -> bool:
    return exc.sqlite_errorcode in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
```

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | `is_lock_contention` true for BUSY/LOCKED, false for SQLITE_ERROR | direct call |
| Unit | fts `_populate_docs_table`: lock propagates raw, fts5-missing stays `FtsUnavailable` | fake `conn.execute` raising locked vs. SQLITE_ERROR OperationalError |
| Integration | ladder-1 lock at open / upsert-commit / `BEGIN IMMEDIATE` → exit 1 + msg, no traceback | monkeypatch `open_vector_store`/`reindex_module.reindex` in main.py to raise a shared `make_locked_error()` |
| Integration | ladder-2 lock on `graph.db` → SAME msg; non-lock sqlite3.Error → graph generic msg | monkeypatch `sqlite_graph.reindex_graph` |

**Lock-injection primitive (pin for apply)**: shared test helper `make_locked_error()`.
Primary: construct `e = sqlite3.OperationalError("database is locked"); e.sqlite_errorcode =
sqlite3.SQLITE_BUSY`. A RED test MUST first assert `sqlite_errorcode` is assignable on 3.13;
if it is not, the helper falls back to capturing a REAL locked instance (second connection
holding `BEGIN IMMEDIATE`, store opened with `busy_timeout=0`) — guaranteed-correct errorcode,
version-safe. The `connect=` seam on `open_vector_store`/`open_derived_connection` supports a
fake connection for the store-level unit tests.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or
process-integration boundary. In-process SQLite error classification only.

## Migration / Rollout

No migration required. Single-PR slice, additive; revert the commit to restore prior behaviour.

## Single-PR Forecast

- Decision needed before apply: No
- Chained PRs recommended: No
- 400-line budget risk: Low (three narrow edits + one predicate + focused tests; well under 400)

## Open Questions

- [ ] None blocking. `sqlite_errorcode` assignability on manually-built exceptions is the one
  runtime detail; the RED test above resolves it deterministically with a real-lock fallback.
