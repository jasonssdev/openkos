# Design: `add-fts-state` — in-memory FTS5 lexical index (canonical state layer)

## Technical Approach

A pure library module `src/openkos/state/fts.py` (+ `state/__init__.py` marker),
canonical layer, **no CLI**. `build_index(bundle_dir)` opens `sqlite3(":memory:")`,
creates ONE FTS5 virtual table, and populates it from a single `okf._iter_docs`
pass — one row per non-reserved concept/Source `.md`, indexing frontmatter
`title`/`description`/`tags` + the markdown `body`. It returns an `FtsIndex`
handle owning the connection; `FtsIndex.search(query, limit)` runs an FTS5
`MATCH` ranked by `bm25`, returning `FtsHit(concept_id, score)`. Rebuild-per-run,
stateless, never touches disk. Mirrors the established `lint.collect_docs`
precedent (reuse `_iter_docs`, re-read body via `okf.load_frontmatter`, keep
`okf.py` byte-unchanged) and the read-only-never-crash discipline of
`lint`/`survey_bundle`.

## Architecture Decisions

| # | Decision | Alternatives rejected | Rationale |
|---|---|---|---|
| **D1** | **Plain content-backed FTS5 table** (FTS5's own default row storage), `concept_id` as an `UNINDEXED` column. | Contentless (`content=''`) — its rows are write-only and cannot be `SELECT`ed, so `concept_id` couldn't be read back without a parallel shadow table; external-content — needs a synced base table + delete/rebuild triggers. | Rebuild-per-run + in-memory makes sync machinery pure cost. Default storage keeps `concept_id` directly `SELECT`able in one table; `UNINDEXED` keeps the identity retrievable but out of the match vocabulary. |
| **D2** | **Re-parse body in `state/fts.py` via `okf.load_frontmatter`** (option b) — NOT extend `DocScan` with a `body` field. | Extend `DocScan`+`_iter_docs` (option a): would byte-change `okf.py`, break the `check_conformance` round-trip regression note, and force re-verifying every caller. | **Callers verified**: `okf.survey_bundle`, `okf.check_conformance` (both read `metadata`/`read_error`/`parse_error` only), and `lint.collect_docs` (already re-reads the body itself, ignoring any `DocScan` body). Option (b) has **zero blast radius** and copies a pattern already in the tree. |
| **D3** | **Rank by `bm25`, ascending.** `... WHERE docs MATCH ? ORDER BY rank LIMIT ?` (`rank` = bm25 by default; more-relevant rows sort first). `limit` is the SQL `LIMIT` bound parameter, default `10`. | Manual score math; no limit (unbounded result). | `rank`/`bm25` is FTS5-native, deterministic, zero extra code; `LIMIT` bounds the downstream context budget change #3 assembles. |
| **D4** | **Tags flattened to a space-joined string** into the `tags` column: `" ".join(str(t) for t in tags)` when `tags` is a list, else `""`. | Storing the Python `repr`/JSON (brackets/quotes pollute the token stream); a separate tags table (over-scope). | Space-join makes each tag an independent searchable token under `unicode61`; non-list/absent tags degrade to empty, never crash. |
| **D5** | **`FtsIndex` owns the connection; it is a context manager.** `with build_index(bundle) as idx: idx.search(...)`. `close()` drops the in-memory DB. Caller-scoped lifecycle. | Module-level global connection (not reentrant, leaks); caller passes its own connection (leaks `sqlite3` into callers, breaks the canonical-leaf shape). | In-memory DB dies with the connection; a context manager makes the rebuild-then-discard lifecycle explicit and test-friendly. |
| **D6** | **Query safety by per-token quoting.** Split the raw query on whitespace; wrap each token as a quoted FTS5 string (embedded `"` doubled); join with ` OR `. Passed as a **bound parameter** to `MATCH ?`. Empty/whitespace query → `[]` without touching SQLite. Defensive `except sqlite3.OperationalError: return []`. | Passing the raw string (a stray `*`/`"`/`AND`/`NEAR` raises `OperationalError` and crashes change #3); one big quoted phrase (requires all terms adjacent in order — near-zero recall on a natural question). | Quoting neutralizes every FTS5 operator per token so a user question never crashes; `OR` maximizes recall while `bm25` still ranks docs matching more/rarer terms first. |
| **D7** | **Availability check at build time.** `CREATE VIRTUAL TABLE ... USING fts5(...)` in a `try`; catch `sqlite3.OperationalError` matching `no such module: fts5` → raise a typed `FtsUnavailable` with a clear message. | Assuming FTS5 silently; failing later at query time. | stdlib `sqlite3` is always present; FTS5 has shipped compiled-in by default since SQLite 3.9 (standard on macOS/Linux CPython) — a **runtime assumption**, detected once with an actionable error rather than a raw traceback. |

**ADR gate — zero created.** `openspec/config.yaml` requires a hard-to-reverse
trade-off. Every decision is additive and `git revert`-able (new dormant module,
no persisted state, no migration, no CLI). Matches the `lint`/`status`
precedent. **Zero ADRs.**

## Data Flow

```
build_index(bundle_dir)                 state/fts   okf            sqlite3(:memory:)
  ├─ conn = sqlite3.connect(":memory:")
  ├─ CREATE VIRTUAL TABLE docs USING fts5(...)  ── (fail → FtsUnavailable)
  ├─ for scan in okf._iter_docs(bundle_dir):   ──→ rglob *.md (single walk, reserved-skip)
  │     read_error/parse_error → skipped.append(note); continue   [never raises]
  │     text = scan.path.read_text(); meta, body = okf.load_frontmatter(text)
  │     concept_id = path.relative_to(bundle_dir).with_suffix("").as_posix()
  │     INSERT (concept_id, title, description, " ".join(tags), body)
  └─ return FtsIndex(conn, skipped)

idx.search(query, limit) → tokens→quoted OR → MATCH ? ORDER BY rank LIMIT ?
                         → [FtsHit(concept_id, bm25_score), ...]
```

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/state/fts.py` | New | FTS5 DDL, `build_index`, `FtsIndex`, `FtsHit`, `FtsUnavailable`, query-safety helper |
| `src/openkos/state/__init__.py` | New | Package marker |
| `src/openkos/model/okf.py` | Reused | `_iter_docs`/`load_frontmatter` read-only — **no change** |
| `docs/architecture.md` | Modify | Line 112 one-line correction: soften "A tool such as import-linter guards these boundaries in CI." to state the guard is **planned/not yet wired** (import-linter absent from `pyproject.toml`; this change stays layer-correct by hand). Import-linter is NOT wired here. |
| `tests/unit/state/test_fts.py` | New | Build/search/degradation/query-safety coverage |

## Interfaces

```python
# state/fts.py — canonical leaf: imports only okf + stdlib sqlite3/pathlib
class FtsUnavailable(RuntimeError): ...

@dataclass(frozen=True)
class FtsHit:
    concept_id: str          # OKF concept ID, e.g. "concepts/stoicism"
    score: float             # bm25 rank (lower = more relevant)

class FtsIndex:
    skipped: list[str]       # skip notices, lint.collect_docs shape
    def search(self, query: str, limit: int = 10) -> list[FtsHit]: ...
    def close(self) -> None: ...
    def __enter__(self) -> "FtsIndex": ...
    def __exit__(self, *exc) -> None: ...   # closes the in-memory connection

def build_index(bundle_dir: Path) -> FtsIndex: ...

# FTS5 DDL
CREATE VIRTUAL TABLE docs USING fts5(
    concept_id UNINDEXED,
    title, description, tags, body,
    tokenize = 'unicode61'
);
```

**Degradation (D6/D2)**: `FtsIndex.skipped` carries one note per unreadable /
unparseable file — format `"{concept_id}.md: skipped (unreadable)"` /
`"... (unparseable frontmatter)"`, mirroring `lint.collect_docs`. Indexing never
raises on a bad file. `search` never raises on an odd query.

## Testing Strategy (strict TDD, ≥90% branch, no network, no disk)

| Layer | What | How |
|---|---|---|
| Unit (fs) | `build_index`: one row per concept/Source; `concept_id` identity; title/desc/tags/body all searchable; empty bundle → zero rows | `tmp_path` fixture bundle |
| Unit (fs) | Degradation: unreadable + unparseable-frontmatter files skipped + noted, never crash; `.skipped` populated | `tmp_path` + bad files |
| Unit | `search`: bm25 ordering; `limit` caps results; tag-term hit; body-term hit; no match → `[]` | fixture index |
| Unit | Query safety: `*`, unbalanced `"`, `AND`/`NEAR`, empty/whitespace → never raises, sane hits/`[]` | fixture index |
| Unit | `FtsUnavailable` path (simulated) + context-manager close | mock/monkeypatch |
| Integration | Build over `examples/good-life-demo/bundle/` → "stoicism"/"apatheia"/"philosophy" resolve to concept IDs | demo fixture |

**Unit seams**: `build_index(bundle_dir)` takes a plain `Path`, so a `tmp_path`
fixture bundle drives every test; `FtsIndex` is a context manager, so tests scope
the connection deterministically; query-safety and bm25 logic are exercised
against a tiny in-memory index with zero I/O.

## Threat Matrix

**N/A** — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process integration. SQLite is stdlib **in-memory** (no file,
no `.openkos/`, no subprocess). The query string is always a **bound parameter**
to `MATCH ?` (no SQL string interpolation); the only FTS5-syntax risk is a
`MATCH`-grammar error, fully contained by D6's per-token quoting + defensive
`except`.

## Migration / Rollout

No migration. Purely additive, no persisted state, module dormant until
`add-query-command` calls `search()`. `git revert` removes `src/openkos/state/`
and its tests and reverts the one doc line.

## Open Questions

- [ ] None blocking. Body path (D2), table shape (D1), ranking (D3), tags (D4),
      API/lifecycle (D5), query safety (D6), FTS5 availability (D7) all resolved.
      Import-linter remains deliberately **out of scope** (record-and-defer per
      proposal RISK-1); the doc-line correction is the only acknowledgement.
