# Delta for Graph Projection

## Note

The existing Non-Goals section defers "persistence to `.openkos/openkos.db`".
This slice fulfills that: persistence is now in scope via the ADDED
requirement below, written only by `reindex`. The in-memory,
rebuild-per-run `build_graph(bundle_dir)` contract itself is unchanged for
any caller that does not go through `reindex`.

## ADDED Requirements

### Requirement: On-Disk Persisted Graph Index Written By Reindex

The system MUST provide a persistence path that writes the node-edge
projection (nodes, edges, and `relation_type`) to on-disk SQLite storage
under `.openkos/`, invoked ONLY by `reindex`, using the SAME node/edge
extraction rules as in-memory `build_graph` (OKF concept ID node identity,
bundle-relative link edge extraction, `relations:` frontmatter typing). A
stored bundle-manifest hash MUST gate whether the persisted index is
rebuilt on a given `reindex` run.

#### Scenario: Reindex persists the graph index to disk

- GIVEN a bundle and an initialized workspace
- WHEN `openkos reindex` runs
- THEN an on-disk graph index exists under `.openkos/` containing the same
  nodes and edges `build_graph` would produce in memory over the same
  bundle

#### Scenario: Persisted index is read-only for non-reindex consumers

- GIVEN a persisted graph index already written by `reindex`
- WHEN `query`/`answer()` reads it
- THEN no write occurs to the on-disk graph index file

## MODIFIED Requirements

### Requirement: In-Memory SQLite Node-Edge Projection

The system MUST build an in-memory SQLite node-edge representation
(`sqlite3(":memory:")`, rebuild-per-run, context-managed) over every
non-reserved concept `.md` file in a bundle, enumerated via the existing
`okf._iter_docs` walk — mirroring `state/fts.py`'s build pattern. Calling
`build_graph(bundle_dir)` directly MUST NOT touch disk; disk persistence
exists ONLY via the dedicated on-disk writer path invoked by `reindex` (see
the new persisted-index requirement above).
(Previously: `build_graph` had no on-disk persistence concept at all; this
clarifies the in-memory call and the new `reindex`-only persistence path
remain distinct.)

#### Scenario: Projection builds one node per concept document

- GIVEN a bundle containing concept `.md` files
- WHEN the projection is built over that bundle
- THEN the resulting node set contains exactly one node per non-reserved
  document

#### Scenario: Projection never touches disk

- GIVEN any bundle
- WHEN the projection is built directly via `build_graph` (not via
  `reindex`'s persistence path)
- THEN no `.openkos/` directory or `openkos.db` file is created; the
  projection exists only in memory for the caller's session
