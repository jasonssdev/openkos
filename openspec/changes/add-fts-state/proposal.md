# Proposal: `add-fts-state` — in-memory FTS5 lexical index (canonical state layer)

## Intent

`openkos query` (MVP-1) must locate candidate concepts by keyword before an LLM
answers, but no state/retrieval code exists — `grep -ri "sqlite|fts5" src/`
matches only docstrings. This change lands the **first piece of the query
capability**: a pure library module that builds an in-memory SQLite FTS5 index
over the compiled bundle and exposes a lexical search function returning concept
IDs. It is the canonical-layer foundation `add-ollama-client` and
`add-query-command` build on. Coherent with `docs/tech_stack.md:27` ("the
derived layer is a rebuildable cache").

## Scope

### In Scope

- New `src/openkos/state/fts.py` — canonical-layer module, **no CLI command**,
  no user-visible workspace change. Its only consumer is future `query`.
- **Rebuild-on-query, stateless.** Build the index from the bundle at the start
  of each query run via `sqlite3(":memory:")`; the index never touches disk.
- **One FTS5 row per document** (per concept/Source `.md`), indexing frontmatter
  `title`, `description`, `tags` + the markdown `body`.
- **Row identity = OKF concept ID** — the bundle-relative path minus `.md`
  (`concepts/stoicism`), the same identity `forget` uses, so hits map straight
  back to concept IDs for downstream citations.
- **Reuse the existing walk**: enumerate via `okf._iter_docs`/`DocScan` (the
  one-pass `rglob` that already skips `index.md`/`log.md` and degrades bad
  files), not a new walker.
- **Graceful degradation**: unreadable / unparseable-frontmatter files are
  skipped + noted, never crashing indexing (mirrors `lint`/`survey_bundle`).
- Search API sketch (shapes, not code): `build_index(bundle_dir) -> FtsIndex`;
  `FtsIndex.search(query, limit) -> list[FtsHit]` where each `FtsHit` carries the
  concept ID (and FTS rank). Full body extraction path is a design detail
  (`DocScan` today carries metadata only, not body).

### Non-goals (explicitly deferred to MVP-2)

| Deferred | Note |
|---|---|
| Persistence (`.openkos/openkos.db`, `.gitignore`, locks) | In-memory only; `architecture.md:136` DB deferred |
| Incremental indexing / change-detection / `ingest`/`forget` wiring | Full rebuild each run; lifecycle untouched |
| Chunking into passages | Document-granularity only |
| Vector/semantic retrieval, graph, hybrid ranking | Lexical FTS5 only |
| Any CLI command or workspace-visible artifact | Pure library module |

## Capabilities

### New Capabilities
- `fts-state`: in-memory FTS5 lexical index over the bundle (title/description/
  tags/body per document), keyed by OKF concept ID, with a rebuild-per-run
  builder and a `search()` query surface; graceful degradation on bad files.

### Modified Capabilities
- None. `okf._iter_docs` is reused read-only with no behavior change;
  `ingest`/`forget` are untouched.

## Approach

- **Rebuild-on-query, in-memory.** Open `sqlite3(":memory:")`, create the FTS5
  virtual table, populate from one `_iter_docs` pass, return a handle the caller
  queries then discards. Small MVP-1 bundles make full rebuild the simplest
  correct default and sidesteps the incremental-update question entirely.
- **Reuse, don't reinvent.** `_iter_docs` already enumerates + degrades; body
  text is obtained during that same pass (design decides: extend `DocScan` vs.
  re-parse). Concept ID = `path.relative_to(bundle_dir).with_suffix("")`.
- **Canonical-layer discipline.** `state/fts.py` imports nothing from
  `retrieval`/`graph` (correct direction; `retrieval/lexical.py` will import
  `state` later). Docstring-per-function house style, strict TDD, 90% branch.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/state/fts.py` | New | FTS5 DDL, rebuild-from-bundle builder, `search()`, `FtsHit`/`FtsIndex` |
| `src/openkos/state/__init__.py` | New | Package marker |
| `src/openkos/model/okf.py` | Reused | `_iter_docs`/`DocScan` reused read-only (possible additive body field — design) |
| `tests/unit/state/test_fts.py` | New | Build/search/degradation coverage |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Import-linter layering guard is **documented but not implemented** (`architecture.md:112` claims CI enforcement; `pyproject.toml` has none) | High (already true) | Record the gap on the proposal; this change stays layer-correct by hand. **Decision for maintainer**: wire import-linter here (first `state/` change) or defer + soften the doc claim. Recommend record-and-defer, not silent reliance |
| `DocScan` carries metadata, not body — reuse needs a body path | Med | Design chooses extend-`DocScan` vs. re-parse; keep enumeration reuse regardless |
| FTS5 table shape (contentless vs. content-backed) | Med | Design decision; rebuild-per-run makes contentless viable |
| Review size | Low | In-memory + no CLI + no persistence shrinks this well below the lint precedent; likely a single PR. Forecast at `sdd-tasks` |

## Rollback Plan

Purely additive: `git revert` removes `src/openkos/state/` and its tests. No
persisted state, no migration, no CLI surface, no workspace artifact, nothing to
unwind — the module is dormant until `add-query-command` calls it.

## Dependencies / Sequencing

- **Upstream: none.** Genuinely first in the locked chain; zero dependency on
  the other two changes.
- **Unblocks**: `add-ollama-client` and `add-query-command` (the latter hard-
  depends on this module's `search()` surface).

## Success Criteria

- [ ] `build_index(bundle_dir)` builds an in-memory FTS5 index over every
      non-reserved concept/Source `.md`, one row each, indexing title +
      description + tags + body.
- [ ] `search(query, limit)` returns hits keyed by OKF concept ID
      (`concepts/stoicism`), resolvable back to the file.
- [ ] Unreadable / unparseable files are skipped + noted, never crashing.
- [ ] No disk write, no `.openkos/`, no `openkos.db`, no `.gitignore` entry.
- [ ] No CLI command; `ingest`/`forget` unchanged.
- [ ] `uv run pytest` green at 90%+ branch; ruff/mypy clean.
