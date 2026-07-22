# Proposal: Reindex Lock-Contention Handling

## Intent

`openkos reindex` writes three on-disk SQLite stores (`vectors.db`, `fts.db`,
`graph.db`), all opened with WAL + `busy_timeout=5000ms`. When a write lock
cannot be acquired within that window (e.g. a concurrent `reindex`), SQLite
raises `sqlite3.OperationalError('database is locked')`. The reindex command's
vectors/FTS exception ladder (ladder 1) does NOT catch it, so a locked
`vectors.db`/`fts.db` crashes with a raw traceback and the wrong exit code on a
legitimate operational condition. The graph ladder (ladder 2) already catches
`sqlite3.Error`, so graph is covered — the defect is the asymmetry. Close the
gap: a locked store must exit 1 with a clear "another process holds the lock,
retry" message, uniformly across all three stores.

## Scope

### In Scope
- Ladder 1 (vectors + FTS, `cli/main.py` ~2504-2545): catch the lock case and
  exit 1 with a retry-hint message, no traceback.
- Ladder 2 (graph, ~2610-2617): keep the `sqlite3.Error` catch, emit the SAME
  uniform locked-case message so all three stores read consistently.
- `fts.py` ~195-200 (`_populate_docs_table`): stop mapping ANY
  `OperationalError` on `CREATE VIRTUAL TABLE` to `FtsUnavailable`; discriminate
  by errorcode so genuine fts5-unavailable stays `FtsUnavailable` and a lock
  error surfaces as lock-contention.
- Tests injecting `OperationalError('database is locked')` at each reindex write
  surface (open, upsert_many/commit, `BEGIN IMMEDIATE`) asserting clean exit 1,
  the right message, and no raw traceback.

### Out of Scope (Non-Goals)
- Retry with backoff — `busy_timeout` is already a bounded in-SQLite wait.
- Changing the 5000ms `busy_timeout`.
- The query/reader path — already degrades via `_open_*_or_degrade` + WAL.
- Cross-process reindex mutex / giving `vectors.db` an explicit `BEGIN IMMEDIATE`.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `reindex-command`: extend the "Error Ladder Mirrors `query`" requirement so a
  lock-contention `OperationalError` on any of the three stores exits 1 with a
  uniform retry-hint message instead of a raw traceback.
- `fts-state`: the `CREATE VIRTUAL TABLE` path must classify by errorcode —
  fts5-unavailable stays `FtsUnavailable`; a lock error is NOT mislabeled.

## Approach

Discriminate the locked case by `sqlite3.OperationalError.sqlite_errorcode in
(sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)` (Python 3.13), NOT by message
substring. Ladder 1 gains an errorcode-guarded `OperationalError` catch wrapping
the whole `with open_vector_store(...)` block; a non-lock operational error keeps
generic handling. Ladder 2 reuses the same locked-case message. In `fts.py`, gate
the `FtsUnavailable` mapping on errorcode so a busy/locked error propagates as
lock-contention rather than "fts5 unavailable".

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `cli/main.py` (~2504-2545, ladder 1) | Modified | Errorcode-guarded lock catch → clean exit 1 + retry message |
| `cli/main.py` (~2610-2617, ladder 2) | Modified | Same uniform locked-case message for graph |
| `state/fts.py` (~195-200) | Modified | Errorcode discrimination: lock ≠ FtsUnavailable |
| reindex tests | New | Inject `OperationalError` at each write surface |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Message-substring matching is fragile across versions | Med | Match `sqlite_errorcode` (SQLITE_BUSY=5, SQLITE_LOCKED=6), version-safe on 3.13 |
| Deterministic lock-contention test is awkward | Med | Inject a fake connection / raising `connect`, or hold a competing lock with `busy_timeout=0` |
| A lock error slips through mislabeled as `FtsUnavailable` | Med | Assert the errorcode-guarded `fts.py` path; test a lock error at CREATE stays lock-contention |
| Open-time locked error not wrapped by the catch | Low | Ensure the ladder-1 try covers the entire `with open_vector_store(...)` block |

## Rollback Plan

Single-PR slice, additive error handling only. Revert the PR commit to restore
the prior (raw-traceback) behavior; no data/schema migration involved.

## Dependencies

None. Python `>=3.13` already required (provides `sqlite_errorcode`).

## Success Criteria

- [ ] Two racing `reindex` runs → the blocked one exits 1 with a retry-hint
      message and no raw traceback, for any of the three stores.
- [ ] Genuine fts5-unavailable still surfaces as `FtsUnavailable`; a lock error
      at the CREATE path surfaces as lock-contention.
- [ ] All three stores emit a consistent locked-case message.
- [ ] `uv run pytest -q` and `uv run mypy .` pass; diff well under 400 lines.
