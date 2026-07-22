# Delta for Reindex Command

## MODIFIED Requirements

### Requirement: Error Ladder Mirrors `query`

`reindex` MUST catch `OllamaError`-family exceptions and `VecUnavailable`,
printing a clear message to stderr and exiting 1, never a raw traceback.
Additionally, `reindex` MUST catch lock-contention `sqlite3.OperationalError`
raised at ANY write surface of the three on-disk stores (vectors, FTS,
graph) — store open, `upsert_many`/prune commit, or `BEGIN IMMEDIATE` —
discriminated by `exc.sqlite_errorcode in (sqlite3.SQLITE_BUSY,
sqlite3.SQLITE_LOCKED)`, NOT by message substring, and exit 1 with the SAME
uniform "another process holds the workspace lock; wait and retry" message
for all three stores. A non-lock `OperationalError` MUST NOT be swallowed by
this catch; it keeps its existing (generic operational-error) handling.
(Previously: only the graph ladder caught `sqlite3.Error` for a locked
`graph.db`; the vectors/FTS ladder had no lock-contention catch and a locked
`vectors.db`/`fts.db` produced a raw traceback instead of a clean exit 1.)

#### Scenario: Ollama unreachable exits 1 with a clear message

- GIVEN Ollama is not reachable
- WHEN `openkos reindex` runs
- THEN it prints a clear stderr message and exits 1

#### Scenario: Vector extension unavailable exits 1 with a clear message

- GIVEN `sqlite-vec` cannot be loaded
- WHEN `openkos reindex` runs
- THEN it prints a clear stderr message and exits 1

#### Scenario: Locked vectors.db exits 1 with the retry message, no traceback

- GIVEN a concurrent process holds a write lock on `vectors.db` past
  `busy_timeout`
- WHEN `openkos reindex` runs and hits `sqlite3.OperationalError` with
  errorcode `SQLITE_BUSY`/`SQLITE_LOCKED` at store open, upsert, or commit
- THEN it prints the uniform lock-contention message to stderr and exits 1,
  with no raw traceback

#### Scenario: Locked fts.db, including at BEGIN IMMEDIATE, exits 1 with the retry message

- GIVEN a concurrent process holds a write lock on `fts.db` past
  `busy_timeout`, including at the `BEGIN IMMEDIATE` step of
  `write_fts_index`
- WHEN `openkos reindex` runs and hits the same lock-contention
  `OperationalError`
- THEN it prints the uniform lock-contention message to stderr and exits 1,
  with no raw traceback

#### Scenario: Locked graph.db exits 1 with the SAME uniform message

- GIVEN a concurrent process holds a write lock on `graph.db` past
  `busy_timeout`
- WHEN `openkos reindex` runs and hits the lock-contention
  `OperationalError`
- THEN it prints the SAME uniform lock-contention message used for
  vectors/FTS, and exits 1 with no raw traceback

#### Scenario: A non-lock operational error is not mislabeled as lock contention

- GIVEN a store write raises `sqlite3.OperationalError` whose errorcode is
  NOT `SQLITE_BUSY`/`SQLITE_LOCKED`
- WHEN `openkos reindex` runs
- THEN it exits 1 via the existing generic operational-error handling, not
  the lock-contention message

#### Scenario: query command behavior is unaffected

- GIVEN this change is applied
- WHEN `openkos query "<question>"` runs, including against a locked store
- THEN its observable behavior (degrade-and-continue via
  `_open_*_or_degrade`) is identical to before this change
