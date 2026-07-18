# FTS State Specification

## Purpose

`state/fts.py` is the canonical-layer foundation for lexical retrieval: a
pure library module that builds an in-memory SQLite FTS5 index over the
compiled bundle and exposes a `search()` surface returning OKF concept IDs.
It has no CLI command and no user-visible workspace effect; its only
consumer is the future `query` command.

## Non-Goals

This spec does not define: persistence of the index (`.openkos/openkos.db`,
`.gitignore` entries, locks — deferred to MVP-2); incremental indexing or
change-detection tied to `ingest`/`forget` (full rebuild each run only);
chunking into passages (document-granularity only); vector, graph, or
hybrid retrieval; ranking/ordering guarantees beyond native FTS5 relevance;
any CLI command or workspace-visible artifact.

## Requirements

### Requirement: Index Build From Bundle

The system MUST build an in-memory SQLite FTS5 index (`sqlite3(":memory:")`)
over every non-reserved concept/Source `.md` file in a bundle, enumerated
via the existing `okf._iter_docs`/`survey_bundle` walk (no new walker), with
exactly one FTS5 row per document indexing that document's frontmatter
`title`, `description`, `tags`, and markdown body.

#### Scenario: Build indexes every eligible document once

- GIVEN a bundle containing concept and Source `.md` files
- WHEN `build_index(bundle_dir)` runs
- THEN the resulting index contains exactly one row per non-reserved
  document, each row's searchable text drawn from that document's title,
  description, tags, and body

#### Scenario: Index never touches disk

- GIVEN any bundle, of any size
- WHEN `build_index(bundle_dir)` runs
- THEN no `.openkos/` directory, `openkos.db` file, or `.gitignore` entry is
  created, and the index exists only in memory for the caller's session

### Requirement: Row Identity Is The OKF Concept ID

Each indexed row MUST be keyed by the OKF concept ID — the document's
bundle-relative path with the `.md` suffix removed (e.g.
`concepts/stoicism`) — the same identity `forget` uses, so a hit resolves
back to a concept file.

#### Scenario: Hit resolves to a concept file

- GIVEN an indexed document at `bundle/concepts/stoicism.md`
- WHEN that document is returned by `search()`
- THEN the hit's identifier is `concepts/stoicism`

### Requirement: Reserved Files Are Excluded

The index build MUST exclude `index.md` and `log.md` at every directory
level, matching `_iter_docs`'s existing reserved-filename skip.

#### Scenario: index.md and log.md never indexed

- GIVEN a bundle containing `index.md` and `log.md`
- WHEN `build_index(bundle_dir)` runs
- THEN neither file appears as a row in the resulting index

### Requirement: Graceful Degradation On Bad Files

An unreadable file or one with unparseable or missing frontmatter MUST be
skipped and noted, and MUST NOT crash the index build, mirroring
`lint`/`survey_bundle`'s degradation behavior.

#### Scenario: Unreadable file is skipped, not fatal

- GIVEN a bundle containing one file that cannot be read (permissions,
  encoding) alongside valid documents
- WHEN `build_index(bundle_dir)` runs
- THEN the build completes, the unreadable file is noted and absent from
  the index, and all valid documents are indexed normally

#### Scenario: Unparseable frontmatter is skipped, not fatal

- GIVEN a bundle containing one `.md` file with malformed or missing
  frontmatter alongside valid documents
- WHEN `build_index(bundle_dir)` runs
- THEN the build completes, that file is noted and absent from the index,
  and all valid documents are indexed normally

### Requirement: Empty Bundle Produces An Empty Index

An empty bundle (no non-reserved `.md` files) MUST produce an empty index,
and `search()` against it MUST return no hits without raising.

#### Scenario: Empty bundle builds and searches cleanly

- GIVEN a bundle with no non-reserved `.md` files
- WHEN `build_index(bundle_dir)` runs and `search("anything", limit)` is
  called on the result
- THEN the build succeeds with zero rows and `search()` returns an empty
  result, not an error

### Requirement: Search Returns Ranked Concept Hits

`search(query, limit)` MUST return a list of hits keyed by OKF concept ID,
ordered by FTS5 relevance, each resolvable back to its source file. A query
matching nothing MUST return an empty result, never an error.

#### Scenario: Matching query returns ranked hits

- GIVEN an index built over a bundle containing a document whose title or
  body contains "stoicism"
- WHEN `search("stoicism", limit)` runs
- THEN the result includes that document's concept ID, ranked by relevance

#### Scenario: No-match query returns empty, not an error

- GIVEN a built index
- WHEN `search()` is called with a query term absent from every indexed
  document
- THEN the result is an empty list and no exception is raised

### Requirement: Tags Are Searchable As Flattened Text

The `tags` list of each document MUST be flattened into searchable text so
that a query matching a tag value returns that document as a hit.

#### Scenario: Tag term matches its document

- GIVEN a document whose frontmatter `tags` includes `philosophy`
- WHEN `search("philosophy", limit)` runs
- THEN that document's concept ID appears in the result

### Requirement: No CLI Surface, No Lifecycle Change

This module MUST NOT introduce a CLI command or any user-invocable entry
point, and MUST NOT alter `ingest` or `forget` behavior. It is a dormant
library dependency until a future command calls it.

#### Scenario: ingest and forget behavior is unchanged

- GIVEN this module exists in the codebase
- WHEN `openkos ingest` or `openkos forget` runs
- THEN their observable behavior is identical to before this change

### Requirement: Architecture Doc States Layering As Convention

`docs/architecture.md`'s layering-enforcement claim MUST be corrected to
state that canonical/derived separation is a followed convention, not an
implemented CI guard, until an automated guard (e.g. import-linter) is
actually wired for the derived layer.

#### Scenario: Doc no longer claims CI enforcement it lacks

- GIVEN `docs/architecture.md` at the layering-convention line (~112)
- WHEN a reader reviews that line after this change
- THEN it describes layering as a followed convention and states that an
  automated guard arrives with the derived layer, and no longer claims CI
  already enforces it
