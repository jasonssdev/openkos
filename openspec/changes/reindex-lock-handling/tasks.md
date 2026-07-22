# Tasks: Reindex Lock-Contention Handling

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~180-260 (4 small prod edits ~35 lines + tests/helper ~150-220 lines) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | auto-forecast |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Predicate + fts.py catch + both CLI ladders + tests, single slice | PR 1 | `uv run pytest -q tests/ -k lock_contention or reindex or fts` | Real 2nd-connection `BEGIN IMMEDIATE` + `busy_timeout=0` competing-lock scenario against a temp `.openkos` workspace, plus `openkos reindex` CLI invocation | Revert the single commit restores prior ladder/catch behavior; no schema/migration touched |

## Phase 1: Lock Predicate + Test Helper (Foundation)

- [x] 1.1 RED: `tests/state/test_derived.py` — assert `sqlite_errorcode` is settable/readable on a manually constructed `sqlite3.OperationalError` on Python 3.13 (decides helper strategy).
- [x] 1.2 GREEN/decision: implement shared `make_locked_error()` test helper in `tests/conftest.py` — manual construction if 1.1 passes, else real 2nd-connection `BEGIN IMMEDIATE` + `busy_timeout=0` fallback.
- [x] 1.3 RED: `tests/state/test_derived.py` — `is_lock_contention(exc)` returns True for `SQLITE_BUSY`/`SQLITE_LOCKED`, False for `SQLITE_ERROR`.
- [x] 1.4 GREEN: add `is_lock_contention(exc: sqlite3.OperationalError) -> bool` to `src/openkos/state/derived.py`.
- [x] 1.5 REFACTOR: clean imports/naming in `derived.py`.

## Phase 2: fts.py CREATE Catch (Spec: fts-state, lock not mislabeled)

- [x] 2.1 RED: `tests/state/test_fts.py` — locked `OperationalError` at `_populate_docs_table` CREATE propagates unchanged as `sqlite3.OperationalError`, NOT `FtsUnavailable`.
- [x] 2.2 GREEN: `src/openkos/state/fts.py` (~195-200) — `except sqlite3.OperationalError as exc: raise` when `is_lock_contention(exc)`, else `raise FtsUnavailable(...) from exc`.
- [x] 2.3 REFACTOR: import `is_lock_contention` from `state.derived` in `fts.py`.
- [x] 2.4 Verify: genuine fts5-unavailable (`SQLITE_ERROR`) still raises `FtsUnavailable` — add/confirm regression test.

## Phase 3: Ladder 1 — vectors/fts (Spec: reindex-command)

- [x] 3.1 RED: `tests/cli/test_main.py` — locked error at `open_vector_store` → `reindex` exits 1, uniform message, no traceback.
- [x] 3.2 RED: same for locked error at `upsert_many`/commit.
- [x] 3.3 RED: same for locked error at fts `BEGIN IMMEDIATE`.
- [x] 3.4 RED: non-lock `OperationalError` (e.g. `SQLITE_ERROR`) is RE-RAISED by ladder 1, not swallowed (existing generic handling unchanged).
- [x] 3.5 GREEN: `src/openkos/cli/main.py` (~2504-2545) — add module-level `_LOCK_CONTENTION_MSG`; add `except sqlite3.OperationalError as exc` sibling clause before `(VecUnavailable, FtsUnavailable, OllamaError)`; `is_lock_contention(exc)` → print msg + exit 1, else `raise`.
- [x] 3.6 REFACTOR: import `is_lock_contention` in `main.py`; dedupe exit-1 helper if reused.

## Phase 4: Ladder 2 — graph (Spec: reindex-command, uniform message)

- [x] 4.1 RED: `tests/cli/test_main.py` — locked `graph.db` → ladder 2 emits the SAME `_LOCK_CONTENTION_MSG` as ladder 1, exits 1, no traceback.
- [x] 4.2 Verify: non-lock `sqlite3.Error` on graph still gets existing "failed while writing the graph index" message (regression).
- [x] 4.3 GREEN: `src/openkos/cli/main.py` (~2610-2617) — inside `except sqlite3.Error as exc`, branch: `isinstance(exc, sqlite3.OperationalError) and is_lock_contention(exc)` → `_LOCK_CONTENTION_MSG`, else current text.
- [x] 4.4 REFACTOR: update the stale gap comment at ~2606-2609 (no longer "deferred"); update comment text only.

## Phase 5: Integration / Regression

- [x] 5.1 Verify: `query` command lock/degrade behavior unchanged (no touched path) — run existing `_open_*_or_degrade` tests unmodified.
- [x] 5.2 Run `uv run pytest -q` full suite green.
- [x] 5.3 Run `uv run mypy .` repo-wide, zero new errors.
