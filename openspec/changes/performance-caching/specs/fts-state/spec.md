# Delta for FTS State

## Note

The existing Non-Goals section defers "persistence of the index
(`.openkos/openkos.db`, `.gitignore` entries, locks)" to MVP-2. This slice IS
that MVP-2 work: persistence is now in scope via the ADDED requirement below,
written only by `reindex`. The in-memory `build_index(bundle_dir)` contract
itself is unchanged for any caller that does not go through `reindex`.

## ADDED Requirements

### Requirement: On-Disk Persisted FTS Index Written By Reindex

The system MUST provide a persistence path that writes the FTS5 projection
to on-disk SQLite storage under `.openkos/`, invoked ONLY by `reindex`,
using the SAME document set and row/identity rules as `build_index` (one row
per non-reserved document, keyed by OKF concept ID, reserved filenames
excluded, graceful degradation on bad files). A stored bundle-manifest hash
MUST gate whether the persisted index is rebuilt on a given `reindex` run.

#### Scenario: Reindex persists the FTS index to disk

- GIVEN a bundle and an initialized workspace
- WHEN `openkos reindex` runs
- THEN an on-disk FTS index exists under `.openkos/` containing the same
  rows `build_index` would produce in memory over the same bundle

#### Scenario: Persisted index is read-only for non-reindex consumers

- GIVEN a persisted FTS index already written by `reindex`
- WHEN `query`/`answer()` reads it
- THEN no write occurs to the on-disk FTS index file

## MODIFIED Requirements

### Requirement: Index Never Touches Disk

Calling `build_index(bundle_dir)` directly (the in-memory library entry
point) MUST NOT touch disk — no `.openkos/` directory, `openkos.db` file, or
`.gitignore` entry is created by that call alone, and the index exists only
in memory for the caller's session. Disk persistence exists ONLY via the
dedicated on-disk writer path invoked by `reindex` (see the new persisted-index
requirement above); ad-hoc, non-`reindex` callers of `build_index` observe no
change from this slice.
(Previously: `build_index` had no on-disk persistence concept at all; this
clarifies the in-memory call and the new `reindex`-only persistence path
remain distinct.)

#### Scenario: Index never touches disk

- GIVEN any bundle, of any size
- WHEN `build_index(bundle_dir)` runs directly (not via `reindex`'s
  persistence path)
- THEN no `.openkos/` directory, `openkos.db` file, or `.gitignore` entry is
  created, and the index exists only in memory for the caller's session
