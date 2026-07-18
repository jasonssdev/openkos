# Tasks: `add-fts-state` — in-memory FTS5 lexical index

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~480 (`state/fts.py` ~150, `state/__init__.py` ~5; `test_fts.py` ~320, test `__init__.py` ~5; `docs/architecture.md` ~2) |
| 400-line budget risk | High |
| Chained PRs recommended | No |
| Suggested split | Single PR (`size:exception`) — future seam if reopened: (1) DDL+build_index+degradation, (2) search()+query-safety+FtsUnavailable+context-manager |
| Delivery strategy | ask-on-risk |
| Chain strategy | size-exception |

Decision needed before apply: Yes
Chained PRs recommended: No
Chain strategy: size-exception
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `state/fts.py` (build_index + search + degradation + FtsUnavailable) + `state/__init__.py` + doc fix | PR 1 (`size:exception`) | `uv run pytest tests/unit/state/test_fts.py` | `build_index(Path("examples/good-life-demo/bundle"))` then `search("stoicism")`/`search("philosophy")` resolve to concept IDs | `git revert`; `state/` + its tests are additive-only; `docs/architecture.md:112` reverts; nothing else changes |

## Phase 1: Scaffold
- [x] 1.1 RED — `tests/unit/state/test_fts.py`: `FtsHit` frozen dataclass (`concept_id`, `score`); `FtsUnavailable` subclasses `RuntimeError`.
- [x] 1.2 GREEN — `src/openkos/state/__init__.py` (package marker) + `src/openkos/state/fts.py` with `FtsUnavailable`, `FtsHit`.

## Phase 2: Build — enumeration, identity, reserved-skip, empty bundle
- [x] 2.1 RED — concept+Source docs → one row each; `concept_id` = relative path minus `.md` (build + identity scenarios).
- [x] 2.2 RED — nested `index.md`/`log.md` never indexed (reserved-exclusion scenario).
- [x] 2.3 RED — empty bundle → zero rows; `search()` returns `[]`, no error (empty-bundle scenario).
- [x] 2.4 GREEN — `fts.py`: `build_index` — `sqlite3(":memory:")`, `CREATE VIRTUAL TABLE docs USING fts5(...)` (D1); loop `okf._iter_docs`, re-read + `okf.load_frontmatter` per doc (D2); insert; return `FtsIndex`.

## Phase 3: Content fields
- [x] 3.1 RED — distinctive body term → `search(term)` hits its concept_id.
- [x] 3.2 RED — `tags: [philosophy]` → `search("philosophy")` hits it; missing/non-list tags index without crash (tag-search scenario).
- [x] 3.3 GREEN — `fts.py`: wire title/description/body columns; flatten tags `" ".join(str(t) for t in tags)` else `""` (D4).

## Phase 4: Degradation
- [x] 4.1 RED — one unreadable file among valid docs → build completes, file absent + noted in `.skipped` (unreadable-degradation scenario).
- [x] 4.2 RED — one malformed/missing-frontmatter file among valid docs → same degrade-and-note behavior (unparseable-degradation scenario).
- [x] 4.3 GREEN — `fts.py`: route `scan.read_error`/`parse_error` to `skipped.append(note)`, `lint.collect_docs`-shaped text.

## Phase 5: `search()` — ranking, limit, safety
- [x] 5.1 RED — bm25 ordering across varying term frequency; `limit` caps results.
- [x] 5.2 RED — absent query term → `[]`, no exception (no-match scenario).
- [x] 5.3 RED — `*`, unbalanced `"`, `AND`/`NEAR`, empty/whitespace query → never raises (query-syntax-safety scenario, D6).
- [x] 5.4 GREEN — `fts.py`: `FtsIndex.search` — per-token quote + `OR`-join bound to `MATCH ?`, `ORDER BY rank LIMIT ?`, `except sqlite3.OperationalError: return []`, empty query short-circuits.

## Phase 6: Lifecycle
- [x] 6.1 RED — `with build_index(bundle) as idx:` works; connection closed after the block (D5).
- [x] 6.2 RED — monkeypatched DDL failure (`no such module: fts5`) → `build_index` raises `FtsUnavailable` (FtsUnavailable scenario, D7).
- [x] 6.3 GREEN — `fts.py`: `__enter__`/`__exit__`/`close()`; wrap DDL in `try/except sqlite3.OperationalError` → `FtsUnavailable`; docstring-per-function pass matching `okf.py`/`lint.py` house style.

## Phase 7: No-disk + no-CLI guards
- [x] 7.1 RED — after any build/search, no `.openkos/`, `openkos.db`, or `.gitignore` entry appears under the bundle root (no-disk scenario).
- [x] 7.2 RED — existing `ingest`/`forget` CLI tests pass unmodified; `state/fts.py` imported by no CLI module (No CLI Surface scenario).

## Phase 8: Integration fixture
- [x] 8.1 RED — `build_index(examples/good-life-demo/bundle)` then `search("stoicism")`/`"apatheia"`/`"philosophy"` each resolve to the expected concept ID.

## Phase 9: Documentation
- [x] 9.1 `docs/architecture.md:112` — state layering as a followed convention with an automated guard not yet wired, dropping the CI-enforcement claim (doc-correction scenario).

## Phase 10: Verification Gate
- [x] 10.1 `uv run pytest --cov` — full suite green, ≥90% branch (project floor; house practice targets 100%).
- [x] 10.2 `uv run ruff check .` && `uv run ruff format --check .` — clean.
- [x] 10.3 `uv run mypy .` — clean (strict).
- [x] 10.4 `git diff --stat -- src/openkos/model/okf.py` — empty, confirming D2's zero-blast-radius claim.

## Phase 11: Correction batch (post-review, review lineage review-07134110857a3e82)

A bounded 4R review found three real WARNINGs (0 blockers) after the change above was
implemented; the maintainer approved fixing them in this follow-up batch. All fixes
land in `src/openkos/state/fts.py` only, with tests written first (Strict TDD).

- [x] 11.1 RED — a doc whose SECOND body read (`Path.read_text`) fails between
  `_iter_docs`'s first read and `build_index`'s re-read is skipped and noted,
  never crashing the build.
- [x] 11.2 RED — a doc whose SECOND parse (`okf.load_frontmatter`) fails the
  same way is skipped and noted too.
- [x] 11.3 GREEN — `fts.py`: guard the second `read_text`/`load_frontmatter`
  call with the same `(OSError, UnicodeDecodeError)` / broad-`Exception`
  try/except shape `lint.collect_docs` uses, `continue`-ing past the failed doc.
- [x] 11.4 RED — an exception raised mid-build (a failing `INSERT`, after the
  table DDL succeeds) still closes the `sqlite3(":memory:")` connection
  instead of leaking it until GC.
- [x] 11.5 GREEN — `fts.py`: wrap the whole build body in `try/except
  BaseException: conn.close(); raise`; only a successful build hands the open
  connection to the returned `FtsIndex`. Verified `FtsUnavailable`'s existing
  close path still closes exactly once (no double-close).
- [x] 11.6 RED — a tied `bm25` rank across rows inserted in the OPPOSITE of
  `concept_id` order still returns in insertion order (proves the pre-fix
  nondeterminism directly against `_SEARCH_SQL`, bypassing `build_index`'s
  already-sorted insertion order).
- [x] 11.7 GREEN — `fts.py`: `_SEARCH_SQL` — `ORDER BY rank, concept_id LIMIT ?`
  (secondary key makes truncation deterministic).
- [x] 11.8 Polish (no test change) — `_quote_query`: replaced the `chr(34)`
  indirection with the literal `'"'`/`'""'` escape (valid under
  `requires-python = ">=3.13"`'s PEP 701 nested-quote f-strings).
- [x] 11.9 Verification gate — full suite green, `state/fts.py` 100%
  line+branch, ruff/ruff-format/mypy clean, `okf.py` untouched.
