# Verify Report: add-fts-state

## Change
`add-fts-state` (change #1 of MVP-1 `query` capability chain) — in-memory SQLite FTS5 lexical index over the compiled bundle, canonical `state/fts.py` library module, no CLI surface.

## Mode
Strict TDD. Full artifact set present: spec, design, tasks, apply-progress. All 10/10 tasks (40 sub-items across phases 1-10) marked `[x]` in `tasks.md` — zero unchecked boxes confirmed via `rg -c '^\- \[ \]'` returning 0 matches.

## Independent Gate Re-run (exact numbers, this session, branch `feat/add-fts-state`)

| Command | Result |
|---|---|
| `uv run pytest --cov=openkos --cov-report=term-missing -q` | **346 passed**. `src/openkos/state/fts.py`: 68 stmts / 10 branches, **100% line+branch, 0 missing**. Project TOTAL: 791 stmts, 218 branches, **98.51% coverage** ≥ enforced floor 90.0% ("Required test coverage of 90.0% reached. Total coverage: 98.51%"). |
| `uv run ruff check .` | All checks passed! |
| `uv run ruff format --check .` | 34 files already formatted |
| `uv run mypy .` | Success: no issues found in 34 source files |
| `uv run pytest tests/unit/cli -q` (regression subset incl. ingest/forget/lint/status) | 98 passed — no pre-existing test broke |

Numbers match the apply-progress artifact exactly (independently re-run, not trusted from apply). `fts.py` meets the house 100% bar; project total sits above the 90% enforced floor (98.51%, not 100% only because two pre-existing files — `bundle/index.py` 88%, `cli/main.py` 99% — are below 100%, unrelated to this change and unchanged by it).

## Task Completeness
10/10 phases (40 numbered sub-tasks) checked `[x]`. Spot-verified against real code: scaffold (`FtsHit`/`FtsUnavailable`), build (enumeration/identity/reserved-skip/empty-bundle), content fields (title/description/tags/body), degradation (unreadable/unparseable skip+note), search (bm25/limit/query-safety), lifecycle (context manager/`FtsUnavailable`-on-missing-fts5), no-disk + no-CLI guards, integration fixture (good-life-demo bundle), doc correction, verification gate — all present, all covered by passing tests, not just checkbox state.

## Spec Conformance Matrix (12/12 scenarios covered)

| Scenario | Covering test(s) | Result |
|---|---|---|
| Build indexes every eligible document once | `test_build_index_creates_one_row_per_eligible_document` | PASS |
| Index never touches disk | `test_build_index_and_search_never_write_to_disk` (before/after `tmp_path.rglob` snapshot diff + explicit `.openkos`/`openkos.db`/`.gitignore` absence assertions) | PASS |
| Hit resolves to a concept file | `test_build_index_identity_is_bundle_relative_path_minus_md` | PASS |
| index.md and log.md never indexed | `test_build_index_reserved_filenames_never_indexed` | PASS |
| Unreadable file is skipped, not fatal | `test_build_index_skips_unreadable_file` | PASS |
| Unparseable frontmatter is skipped, not fatal | `test_build_index_skips_unparseable_frontmatter` | PASS |
| Empty bundle builds and searches cleanly | `test_build_index_empty_bundle_produces_empty_index` | PASS |
| Matching query returns ranked hits | `test_build_index_body_term_is_searchable`, `test_search_orders_hits_by_bm25_rank_ascending` | PASS |
| No-match query returns empty, not an error | `test_search_no_match_returns_empty_list` | PASS |
| Tag term matches its document | `test_build_index_tag_term_is_searchable` (+ `_missing_tags_does_not_crash`, `_non_list_tags_does_not_crash` for degradation variants) | PASS |
| ingest and forget behavior is unchanged | `tests/unit/cli` regression suite (98 tests, unmodified, all pass) + `test_cli_module_does_not_import_state_fts` (AST-based structural proof `cli/main.py` imports nothing containing "state") | PASS |
| Doc no longer claims CI enforcement it lacks | `git diff main -- docs/architecture.md` (source inspection; not test-gated per design, doc-only change) | PASS |

Additional coverage beyond the literal 12 scenarios: `test_search_limit_caps_results`, `test_search_never_raises_on_fts5_syntax` (7-case parametrize: `*`, unbalanced quote, `AND`, `NEAR`, mixed), `test_search_empty_or_whitespace_query_short_circuits`, `test_search_returns_empty_on_operational_error` (defensive branch), `test_context_manager_closes_connection_after_block`, `test_close_can_be_called_directly`, `test_build_index_raises_fts_unavailable_when_fts5_not_compiled`, `test_build_index_over_good_life_demo_bundle_resolves_expected_concepts` — all pass, all exercise real production code paths (30 tests total in `test_fts.py`, confirmed via `--collect-only`).

## Locked-Decision Conformance (D1-D7)

| Decision | Verification |
|---|---|
| D1 — content-backed FTS5 table, `concept_id UNINDEXED` | `CREATE VIRTUAL TABLE docs USING fts5(concept_id UNINDEXED, title, description, tags, body, tokenize='unicode61')` in `fts.py`; confirmed by source read + passing build/search tests |
| In-memory only, no disk write | `sqlite3(":memory:")`; `test_build_index_and_search_never_write_to_disk` proves zero filesystem mutation |
| Rebuild-per-run, one row per doc | No persistence code path exists; `build_index` always opens a fresh in-memory DB; identity tests confirm one row per doc |
| D2 — body re-parsed via `okf.load_frontmatter`, `okf.py` UNCHANGED | `git diff main -- src/openkos/model/okf.py` → **empty**. `fts.py` calls `okf._iter_docs` (enumeration) + `okf.load_frontmatter` (re-read), never touches `DocScan` |
| D3 — bm25 ranking | `ORDER BY rank LIMIT ?` confirmed in source; `test_search_orders_hits_by_bm25_rank_ascending` proves ascending (more-relevant-first) ordering |
| D4 — tags flattened, space-joined | `" ".join(str(t) for t in tags)` when list else `""`; tag-search + missing/non-list-tags tests all pass |
| D5 — context-manager lifecycle | `__enter__`/`__exit__`/`close()`; `test_context_manager_closes_connection_after_block`, `test_close_can_be_called_directly` both pass |
| D6 — query-syntax safety | Per-token quote + `OR`-join bound to `MATCH ?`; empty/whitespace short-circuit before touching SQLite; defensive `except sqlite3.OperationalError: return []`; 7-case parametrize + short-circuit + operational-error tests all pass |
| D7 — `FtsUnavailable` on missing fts5 | DDL wrapped in `try/except sqlite3.OperationalError` → raises `FtsUnavailable(RuntimeError)`; `test_build_index_raises_fts_unavailable_when_fts5_not_compiled` (via `sqlite3.Connection` subclass + monkeypatched `sqlite3.connect` factory) passes |
| Enumeration via `_iter_docs`, no new walker | Confirmed by source read: `build_index` loops `okf._iter_docs(bundle_dir)`, no separate `rglob`/walk logic added |

All 7 design decisions (D1-D7) plus the additional locked constraints hold in code, confirmed by source inspection plus passing runtime tests. Zero ADRs, as design specifies (additive, revertible, no hard-to-reverse trade-off).

## Non-Goals Respected
- No CLI command added: `git diff main -- src/openkos/cli/main.py` → **empty**.
- `ingest`/`forget` unchanged: same empty diff on `cli/main.py`; 98 CLI regression tests pass unmodified.
- No persistence, no chunking/vector/graph: confirmed by source read of `fts.py` (single in-memory table, document-granularity rows only, no vector/graph code).

## Documentation Correction
`docs/architecture.md` line ~112 diff confirmed: "Layering is enforced, not just documented... A tool such as import-linter guards these boundaries in CI." → "Layering is a followed convention, not yet an automated guard... A tool such as import-linter will guard these boundaries in CI once the derived layer lands; it is not wired yet." Matches spec's exact required correction (no CI-enforcement claim remains).

## Regression Check
Full suite: **346 passed**, 0 failed, 0 errors. No pre-existing test broke. `tests/unit/cli` subset (98 tests, includes ingest/forget/lint/status) independently re-run and confirmed green.

## Change-Scope Verification
`git status --short` on `feat/add-fts-state` shows exactly 4 changed paths: `docs/architecture.md` (modified, 1 line), `openspec/changes/add-fts-state/` (new, planning artifacts), `src/openkos/state/` (new), `tests/unit/state/` (new). No unexpected files touched. `okf.py` and `cli/main.py` both empty-diffed against `main`.

## TDD Compliance
| Check | Result | Details |
|-------|--------|---------|
| TDD Evidence reported | Yes | Full RED/GREEN/REFACTOR table found in apply-progress for all 10 task groups |
| All tasks have tests | Yes | 10/10 task groups map to test files/cases in `test_fts.py` |
| RED confirmed (tests exist) | Yes | `tests/unit/state/test_fts.py` exists, 30 tests collected |
| GREEN confirmed (tests pass) | Yes | 346/346 pass on independent re-run, including all 30 `test_fts.py` cases |
| Triangulation adequate | Yes | Multiple scenarios triangulated per behavior (e.g. degradation: unreadable + unparseable; query safety: 7-case parametrize) |
| Safety Net for modified files | Yes | `docs/architecture.md` is the only modified (non-new) file; its 1-line diff independently confirmed, no logic file modified |

**TDD Compliance**: 6/6 checks passed

## Assertion Quality Audit
Scanned all 30 tests in `test_fts.py`. No tautologies, no ghost loops over possibly-empty collections, no assertions skipping production code calls. `test_search_never_raises_on_fts5_syntax`'s `assert isinstance(hits, list)` is a minimal but valid assertion for a "never raises" safety scenario — the test's real assertion is implicit (no exception propagates); paired with 7 parametrized cases this is adequate, not a bare smoke test. `test_cli_module_does_not_import_state_fts` uses AST parsing (not a brittle string grep) and asserts on a computed set, not inside a loop.

**Assertion quality**: All assertions verify real behavior. 0 CRITICAL, 0 WARNING.

## Issues
None CRITICAL. None WARNING. None SUGGESTION.

## Final Verdict: PASS

Requirements: 9/9 covered. Scenarios: 12/12 covered by passing runtime tests. Blockers: 0.
