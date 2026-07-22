# Delta for FTS State

## ADDED Requirements

### Requirement: `CREATE VIRTUAL TABLE` Failure Is Discriminated By Errorcode

When `_populate_docs_table` fails to create the FTS5 `docs` virtual table,
the system MUST discriminate the failure by
`exc.sqlite_errorcode`, NOT by catching every `sqlite3.OperationalError`
uniformly. A genuine fts5-module-absence error (errorcode NOT
`SQLITE_BUSY`/`SQLITE_LOCKED`) MUST still raise `FtsUnavailable("SQLite's
fts5 module is not available in this environment")`. A lock-contention error
(errorcode `SQLITE_BUSY` or `SQLITE_LOCKED`) MUST NOT be converted to
`FtsUnavailable`; it MUST propagate unchanged as `sqlite3.OperationalError`
so the caller's lock-contention handling (reindex-command: Error Ladder
Mirrors `query`) can catch it.

#### Scenario: Genuine fts5-unavailable still raises FtsUnavailable

- GIVEN `sqlite3`'s `fts5` module is not compiled into the running SQLite
- WHEN `_populate_docs_table` attempts `CREATE VIRTUAL TABLE`
- THEN `FtsUnavailable` is raised, unchanged from before this change

#### Scenario: A lock error at CREATE VIRTUAL TABLE is not mislabeled as FtsUnavailable

- GIVEN a concurrent process holds a write lock on the on-disk `fts.db` past
  `busy_timeout`
- WHEN `write_fts_index`'s `_populate_docs_table` call hits
  `sqlite3.OperationalError` with errorcode `SQLITE_BUSY`/`SQLITE_LOCKED` at
  `CREATE VIRTUAL TABLE`
- THEN the error propagates as `sqlite3.OperationalError`, NOT as
  `FtsUnavailable`
