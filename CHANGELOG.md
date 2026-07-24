# Changelog

All notable changes to OpenKOS are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html),
and commit history follows [Conventional Commits](https://www.conventionalcommits.org/).

> OpenKOS is **alpha** — it runs, and the API may still change. The package is
> published on [PyPI](https://pypi.org/project/openkos/); the MVP 1 (Compiler)
> and MVP 2 (Graph and Memory) arcs are complete. The project's vision,
> architecture, and design live in the documents under
> [`docs/`](https://github.com/jasonssdev/openkos/tree/main/docs).

## [Unreleased]

## [0.1.2] - 2026-07-24

### Fixed

- **Extraction no longer returns an empty result for instructional sources.**
  `ingest` derived zero objects from how-to, tutorial, reference, and FAQ
  documents because the extraction prompt stacked three suppression cues (and a
  rubric that assumed every source is about a *named* subject) that made the
  model decline. The prompt now states a positive default (a substantive source
  yields at least one object), routes instructional documents to `Procedure` or
  `Concept`, and keeps the empty-array outcome as a genuine last resort, while a
  sub-topic restraint clause prevents the fix from over-extracting shallow
  stubs. (#129)

### Changed

- README documentation links are now absolute GitHub URLs so they resolve on the
  PyPI project page, not only on GitHub.

## [0.1.1] - 2026-07-23

### Changed

- Packaging and PyPI release preparation: lowered the Python floor to 3.12,
  finalized PyPI metadata, added the Trusted Publishing release workflow, and
  synchronized `uv.lock` to the release version.

## [0.1.0] - 2026-07-23

Initial public release — the complete MVP 1 (The Compiler) and MVP 2 (The Graph
and Memory) work.

### Added

- **18-verb command-line interface**: `init`, `ingest`, `forget`, `purge`,
  `relate`, `merge`, `unmerge`, `reconcile`, `status`, `lint`, `duplicates`,
  `adjudicate`, `suggest-relations`, `suggest-volatility`, `contradictions`,
  `query`, `reindex`, and `doctor`.
- **Compiler (MVP 1)**: text/markdown ingestion into an OKF-conformant bundle
  with immutable `raw/` sources, single-source extraction of up to five typed
  derived concepts, provenance chains, and automatic `index.md`/`log.md`.
- **Cited query**: natural-language `query` with citations back to concepts and
  sources, read-only by default.
- **Freshness lint v1**: mechanical stale-stamp and orphan-page checks, plus
  volatility classification with volatility-aware windows.
- **Entity resolution (MVP 2)**: `duplicates`, LLM `adjudicate`, and reversible
  `merge`/`unmerge` with a `merged_from` ledger.
- **Typed knowledge graph**: an OpenKOS layer over OKF's untyped links, written
  by `relate`, with `suggest-relations`, `suggest-volatility`, `contradictions`,
  and `reconcile`.
- **Hybrid retrieval (MVP 2)**: lexical FTS5 + local `sqlite-vec` vectors +
  graph traversal, fused via reciprocal rank fusion (RRF) with NetworkX
  PageRank, all served from persisted `.openkos/` indexes maintained by
  `reindex`.
- **Fail-closed sensitivity filter**: confidential concepts are excluded from
  retrieval and never sent to the LLM, with an explicit `--include-confidential`
  escape.
- **Forget/purge lifecycle**: reference-aware `forget` with tombstones and
  `--scope self|source` cascade, and an irreversible `purge` (right-to-be-
  forgotten) that expunges files and scrubs history via `git-filter-repo`.
- **Two-output rule**: `query --save` files a good answer back into the bundle
  as a new concept.
- **Status-aware retrieval**: deprecated and superseded concepts are excluded
  from retrieval by default.

### Changed

- Default embedding model is `bge-m3` (ADR-0006), superseding the earlier
  `qwen3-embedding:0.6b` default.

[Unreleased]: https://github.com/jasonssdev/openkos/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/jasonssdev/openkos/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/jasonssdev/openkos/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/jasonssdev/openkos/releases/tag/v0.1.0
