# Delta for Reindex Command

## MODIFIED Requirements

### Requirement: Error Ladder Mirrors `query`

`reindex` MUST catch `BackendError`-family exceptions and `VecUnavailable`,
printing a clear message to stderr and exiting 1, never a raw traceback.
Additionally, `reindex` MUST catch lock-contention `sqlite3.OperationalError`
raised at ANY write surface of the three on-disk stores (vectors, FTS,
graph) — store open, `upsert_many`/prune commit, or `BEGIN IMMEDIATE` —
discriminated by `exc.sqlite_errorcode in (sqlite3.SQLITE_BUSY,
sqlite3.SQLITE_LOCKED)`, NOT by message substring, and exit `3` -- the
retry-safe refusal -- with the SAME uniform message for all three stores. The
message MUST say that another OpenKOS process is using the workspace's
derived stores and that a re-run is safe, and MUST NOT name a specific
concurrent verb. A non-lock `OperationalError` MUST NOT be swallowed by
this catch; it keeps its existing (generic operational-error) handling.

The same refusal is the contract for every other verb that holds the workspace
lock: a lock-contention `sqlite3.OperationalError` from any derived or findings
store that no verb-specific handler took MUST surface as the same uniform
message (naming the verb the user ran) and exit `3`, mapped once in the shared
workspace-lock guard rather than per verb (`workspace-lock`: Derived-Store
Contention Is The Same Retry-Safe Refusal).
(Previously: derived-store contention exited 1 while workspace-lock
contention exited 3, and the message guessed at "a concurrent reindex".)

#### Scenario: Ollama unreachable exits 1 with a clear message

- GIVEN Ollama is not reachable
- WHEN `openkos reindex` runs
- THEN it prints a clear stderr message and exits 1

#### Scenario: Vector extension unavailable exits 1 with a clear message

- GIVEN `sqlite-vec` cannot be loaded
- WHEN `openkos reindex` runs
- THEN it prints a clear stderr message and exits 1

#### Scenario: Locked vectors.db exits 3 with the retry message, no traceback

- GIVEN a concurrent process holds a write lock on `vectors.db` past
  `busy_timeout`
- WHEN `openkos reindex` runs and hits `sqlite3.OperationalError` with
  errorcode `SQLITE_BUSY`/`SQLITE_LOCKED` at store open, upsert, or commit
- THEN it prints the uniform lock-contention message to stderr and exits 3,
  with no raw traceback

#### Scenario: Locked fts.db, including at BEGIN IMMEDIATE, exits 3 with the retry message

- GIVEN a concurrent process holds a write lock on `fts.db` past
  `busy_timeout`, including at the `BEGIN IMMEDIATE` step of
  `write_fts_index`
- WHEN `openkos reindex` runs and hits the same lock-contention
  `OperationalError`
- THEN it prints the uniform lock-contention message to stderr and exits 3,
  with no raw traceback

#### Scenario: Any locked-workspace verb refuses instead of tracing back

- GIVEN a concurrent process holds a write lock on `findings.db` past
  `busy_timeout`
- WHEN a locked verb other than `reindex` writes to it
- THEN it prints the uniform lock-contention message under its own name to
  stderr and exits 3, with no raw traceback

#### Scenario: Locked graph.db exits 3 with the SAME uniform message

- GIVEN a concurrent process holds a write lock on `graph.db` past
  `busy_timeout`
- WHEN `openkos reindex` runs and hits the lock-contention
  `OperationalError`
- THEN it prints the SAME uniform lock-contention message used for
  vectors/FTS, and exits 3 with no raw traceback

#### Scenario: A non-lock operational error is not mislabeled as lock contention

- GIVEN a store write raises `sqlite3.OperationalError` whose errorcode is
  NOT `SQLITE_BUSY`/`SQLITE_LOCKED`
- WHEN `openkos reindex` runs
- THEN it exits 1 via the existing generic operational-error handling, not
  the lock-contention message

#### Scenario: query command behavior is unaffected

- GIVEN a locked store
- WHEN `openkos query "<question>"` runs
- THEN it degrades and continues via `_open_*_or_degrade`, independent of
  `reindex`'s lock-contention handling
