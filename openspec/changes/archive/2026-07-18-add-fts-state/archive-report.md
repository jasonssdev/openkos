# Archive Report: add-fts-state

**Change**: add-fts-state | **Archived**: 2026-07-18 | **Status**: Complete | **Repository**: openkos (main 0a920cb after merge of PR #23)

This archive report closes the SDD cycle for the `add-fts-state` change. The feature implements the first piece of MVP-1's `query` capability — an in-memory SQLite FTS5 lexical index over the compiled bundle, canonical-layer foundation for future `add-ollama-client` and `add-query-command`. The implementation adds a new `state/fts.py` library module with no CLI surface, undergoes bounded review (two lineages: initial HIGH full-4R found 3 WARNINGs, corrected via strict TDD; fresh review on corrected tree APPROVED with only 3 non-blocking SUGGESTIONs), and achieves 100% line+branch coverage on the module with 98.53% project coverage.

## Change Summary

**Purpose**: Ship the lexical-index foundation (`fts-state` capability) for MVP-1's `query` command, enabling keyword-based concept lookup before LLM enrichment.

**Scope**:
- New `state/fts.py` canonical-layer module (no CLI, no workspace effect) with `build_index(bundle_dir) -> FtsIndex`, `FtsIndex.search(query, limit) -> list[FtsHit]`, `FtsHit(concept_id, score)` frozen dataclass, and `FtsUnavailable` exception
- In-memory-only SQLite FTS5 index, rebuilt on each query run via `sqlite3(":memory:")`, one row per non-reserved concept/Source `.md`, searchable on title/description/tags/body
- Row identity = OKF concept ID (bundle-relative path minus `.md`, e.g. `concepts/stoicism`), matching `forget`'s identity scheme
- Reuses existing `okf._iter_docs` enumeration walk (no new walker), re-reads body via `okf.load_frontmatter` (design D2: zero blast radius on `okf.py`)
- Graceful degradation: unreadable/unparseable files skipped + noted, never crashing indexing (mirrors `lint.collect_docs`)
- FTS5 search safety: per-token quoting + `OR`-join (design D6), defensive exception handling, empty-query short-circuit
- Context-manager lifecycle: `with build_index(bundle) as idx: idx.search(...)` (design D5), connection released on exception or explicit `close()`, `FtsUnavailable` raised if FTS5 unavailable (design D7)
- Tests: 34 total in `test_fts.py` (30 original + 4 correction-batch), covering build/search/degradation/query-safety/lifecycle, with integration fixture over `examples/good-life-demo/bundle/`
- Documentation: one-line correction to `docs/architecture.md:112` clarifying that layering is a followed convention, not yet an automated CI guard

**Bounded Review Corrections** (discovery from two review lineages):

**Lineage 1** — `review-07134110857a3e82` (HIGH tier, full 4R sweep):
- Found 0 blockers but 3 real WARNINGs (all from src/openkos/state/fts.py):
  1. **WARNING (reliability)**: Second body read unguarded (TOCTOU race — file deleted/corrupted between `_iter_docs`'s first read and `build_index`'s re-read). FIXED: guarded both `read_text` and `load_frontmatter` with same broad `except (OSError, UnicodeDecodeError, Exception)` + skip+note strategy that `lint.collect_docs` uses.
  2. **WARNING (resilience)**: No try/finally in build loop → in-memory SQLite connection leak on any mid-build exception (e.g., `INSERT` failure). FIXED: wrapped entire build body in `try/except BaseException: conn.close(); raise`; only successful builds hand open connection to `FtsIndex`.
  3. **WARNING (reliability)**: `ORDER BY rank` with no secondary key → nondeterministic tie-break in truncation. FIXED: `ORDER BY rank, concept_id` guarantees same results regardless of insertion order.

**Lineage 2** — `review-381fdecb5995f800` (fresh review on corrected tree):
- APPROVED with 0 blocker/critical/warning.
- 3 non-blocking SUGGESTIONs identified (no corrections required, deferred to optional future work):
  1. Document `concept_id` tie-break guarantee in `search()` docstring for maintainability
  2. Simplify `_quote_query` — optional refactor for readability
  3. Add explicit close-on-`FtsUnavailable` connection test for defensive coverage

**Key Architecture Decisions**:
- D1: Plain content-backed FTS5 table (default storage, keeps `concept_id` directly retrievable)
- D2: Re-parse body via `okf.load_frontmatter` (not extend `DocScan`) — zero blast radius on existing code
- D3: Rank by `bm25` native FTS5 scoring, ascending (lower rank = more relevant)
- D4: Tags flattened to space-joined string for independent tokenization
- D5: Context-manager lifecycle (`__enter__`/`__exit__`) — caller-scoped connection, deterministic release
- D6: Query safety by per-token quoting + `OR`-join, bound parameter to `MATCH ?`, defensive exception handling
- D7: `FtsUnavailable` exception on missing FTS5 (detect availability once at build time, not later)
- Zero ADRs created (all decisions additive, fully revertible via `git revert`)

**Change-scope verification**:
- `git diff --stat -- src/openkos/model/okf.py` → empty (D2 confirmed zero blast radius)
- Only four changed paths: `docs/architecture.md` (1-line doc fix), `src/openkos/state/` (new module), `tests/unit/state/` (new tests), `openspec/changes/add-fts-state/` (planning artifacts)
- No `ingest`/`forget` changes; CLI unchanged; 98 pre-existing CLI regression tests pass unmodified

## Artifacts Archived

| Artifact | Location | Status |
|---|---|---|
| Proposal | `archive/2026-07-18-add-fts-state/proposal.md` | Moved from change folder; summarizes intent, scope, approach, risks, and MVP-1 context |
| Specification | `archive/2026-07-18-add-fts-state/specs/fts-state/spec.md` | Promoted to main spec tree at `openspec/specs/fts-state/spec.md` + moved to archive |
| Design | `archive/2026-07-18-add-fts-state/design.md` | Moved from change folder; documents D1-D7 decisions, data flow, interfaces, testing strategy, threat matrix |
| Tasks | `archive/2026-07-18-add-fts-state/tasks.md` | 19/19 checked (10 original phases 1-10 + 9 correction-batch phase 11); all sub-tasks complete |
| Verification Report | `archive/2026-07-18-add-fts-state/verify-report.md` | PASS (all 12/12 spec scenarios passing, all design decisions verified, zero CRITICAL/WARNING/SUGGESTION findings). Verify ran on the pre-correction tree (346 tests, 30 in `test_fts.py`); the 3 non-blocking SUGGESTIONs came later from review lineage `review-381fdecb5995f800`, not from this report |

## Spec Merge Summary

| Action | Domain | Details |
|---|---|---|
| **NEW** | `fts-state` | Created new capability spec at `openspec/specs/fts-state/spec.md` |
| Requirements at archive time | 9 | Index Build From Bundle (2 scenarios), Row Identity Is The OKF Concept ID (1 scenario), Reserved Files Are Excluded (1 scenario), Graceful Degradation On Bad Files (2 scenarios), Empty Bundle Produces An Empty Index (1 scenario), Search Returns Ranked Concept Hits (2 scenarios), Tags Are Searchable As Flattened Text (1 scenario), No CLI Surface, No Lifecycle Change (1 scenario), Architecture Doc States Layering As Convention (1 scenario) |
| Total scenarios at archive time | 12 | Full coverage of build/identity/reserved-skip/degradation/empty-bundle/search/ranking/tag-search/no-cli/doc-correction |
| Source | Delta spec from change folder | `/openspec/changes/add-fts-state/specs/fts-state/spec.md` promoted to `/openspec/specs/fts-state/spec.md` |
| Merge mode | NEW capability | The `fts-state` capability did not exist before; this change establishes it. No existing spec to merge into. |
| Divergence note | Archived historical copy | The archived delta copy at `openspec/changes/archive/2026-07-18-add-fts-state/specs/fts-state/spec.md` is left unchanged as the historical record; the canonical `openspec/specs/fts-state/spec.md` is the source of truth for this capability going forward. |

## Verification Status

**Final Verdict**: PASS (after bounded-review corrections: all WARNINGs fixed and approved)

**Evidence Summary**:
- All 12/12 spec scenarios covered by passing tests (test_build_index_creates_one_row_per_eligible_document, test_build_index_and_search_never_write_to_disk, test_build_index_identity_is_bundle_relative_path_minus_md, test_build_index_reserved_filenames_never_indexed, test_build_index_skips_unreadable_file, test_build_index_skips_unparseable_frontmatter, test_build_index_empty_bundle_produces_empty_index, test_build_index_body_term_is_searchable, test_search_orders_hits_by_bm25_rank_ascending, test_search_no_match_returns_empty_list, test_build_index_tag_term_is_searchable, test_cli_module_does_not_import_state_fts, plus git-diff source inspection for doc-correction scenario)
- Design decision verification: D1 (content-backed table), D2 (re-parse, zero okf.py blast radius), D3 (bm25 ranking), D4 (tags flattened), D5 (context manager), D6 (per-token query safety), D7 (FtsUnavailable on missing fts5)
- Test execution (final, independent verify run after corrections): **350 passed, 0 failed, 0 skipped**
- Coverage: `src/openkos/state/fts.py` **100% line + 100% branch** (79 stmts, 10 branches); Project total **98.53%** (floor 90%, enforced; two pre-existing files below 100% unrelated to this change)
- Quality gates:
  - `uv run ruff check .` pass (exit 0, all checks pass)
  - `uv run ruff format --check .` pass (both modified test and source files clean)
  - `uv run mypy .` pass (strict mode, 34 source files, no issues)
- Byte-unchanged: `git diff --stat -- src/openkos/model/okf.py` → empty (design D2 verified)
- Regression suite: `tests/unit/cli` (98 tests incl. ingest/forget/lint/status) all pass unmodified, confirming no lifecycle changes

## Delivery History

This change was delivered as a single PR after orchestrator approval of `size:exception` and underwent bounded review with corrections:
- **PR #23** (merged to main, 2026-07-18, after bounded review corrections and approval): Complete FTS5 index implementation — `state/fts.py` module + context-manager lifecycle + FTS5 DDL/build/search + query safety + graceful degradation + architecture doc fix + 34 tests. Underwent bounded review process after apply: lineage `review-07134110857a3e82` (HIGH, full 4R) found THREE real WARNINGs (second-read TOCTOU, connection leak, nondeterministic tie-break). All THREE WARNINGs CORRECTED via strict TDD correction batch; lineage `review-381fdecb5995f800` (fresh review on corrected tree) came back clean with only 3 non-blocking SUGGESTIONs; final approval obtained via terminal receipt.

**Repository State**: main @ 0a920cb (commit: "feat: add openkos state.fts — in-memory FTS5 canonical index (MVP-1 query foundation)" after bounded review corrections and approval)

## Review Gate & Closure

**Delivery review history**:
- Lineage `review-07134110857a3e82` (HIGH, full 4R post-apply): initial review found 3 real WARNINGs on src/openkos/state/fts.py (TOCTOU unguarded second read, connection leak on exception, nondeterministic ranking tie-break). Corrections applied via strict TDD (guarded second read, wrapped build in try/except for connection release, added secondary key to ORDER BY). Stale review invalidated by tree drift.
- Lineage `review-381fdecb5995f800` (fresh review on corrected tree): APPROVED with terminal receipt valid. 0 blocker/critical/warning; 3 non-blocking SUGGESTIONs (optional docstring enhancement, optional refactor, optional test).

**Current status**:
- PR #23 merged to main with bounded review corrections applied
- All 350 tests passing, 100% fts.py line+branch, 98.53% project coverage
- All 12 spec scenarios passing runtime tests
- All 7 architecture decisions verified in code
- No blockers remain; all warning findings closed and corrected
- 3 non-blocking optional SUGGESTIONs recorded for future enhancement (not blockers)

## Implementation Details

**Modules added/modified**:
- `src/openkos/state/fts.py`: `build_index(bundle_dir) -> FtsIndex`, `FtsIndex(conn, skipped)` context manager with `search(query, limit) -> list[FtsHit]`, `__enter__`/`__exit__`/`close()`, `FtsHit(concept_id, score)` frozen dataclass, `FtsUnavailable` exception, `_quote_query` safety helper
- `src/openkos/state/__init__.py`: Package marker (empty, no re-exports)
- `src/openkos/model/okf.py`: Untouched (byte-unchanged, design D2 confirmed)
- `docs/architecture.md`: Line 112 one-line correction — state layering as a followed convention, not yet an automated CI guard (import-linter deferred to derived-layer MVP-2)
- `tests/unit/state/__init__.py`: Package marker (empty)
- `tests/unit/state/test_fts.py`: 34 tests (30 original + 4 correction-batch) covering build/search/degradation/query-safety/lifecycle/integration

**Correction-batch fixes** (applied to state/fts.py, tests/unit/state/test_fts.py, tasks.md phase 11):
- **Fix 1a** (second `read_text` guard): `except (OSError, UnicodeDecodeError)` wrapping the second `path.read_text()` call in `build_index`
- **Fix 1b** (second parse guard): `except Exception` wrapping the second `okf.load_frontmatter()` call, mirroring `lint.collect_docs` TOCTOU handling
- **Fix 2** (connection release on exception): `try/except BaseException: conn.close(); raise` around the entire build body; `FtsUnavailable` handler no longer closes (outer handler closes once)
- **Fix 3** (deterministic tie-break): `_SEARCH_SQL` → `ORDER BY rank, concept_id LIMIT ?` (secondary key guarantees stable truncation)
- **Fix 4** (polish, no behavior change): `chr(34)` / `chr(34)*2` → literal `'"'` / `'""'` in `_quote_query` (valid under Python 3.13+ PEP 701)
- 4 new tests: `test_build_index_skips_doc_whose_second_read_fails`, `test_build_index_skips_doc_whose_second_parse_fails`, `test_build_index_closes_connection_on_mid_build_exception`, `test_search_breaks_bm25_ties_by_concept_id_regardless_of_insertion_order` (isolated reverse-order insertion to prove determinism)

**API surfaces**:
```python
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
    def __exit__(self, *exc) -> None: ...

def build_index(bundle_dir: Path) -> FtsIndex: ...
```

**FTS5 DDL**:
```sql
CREATE VIRTUAL TABLE docs USING fts5(
    concept_id UNINDEXED,
    title, description, tags, body,
    tokenize = 'unicode61'
);
```

## Archival Actions Completed

**Filesystem**:
- [x] Main spec tree created at `openspec/specs/fts-state/spec.md` (9 requirements, 12 scenarios)
- [x] Change folder moved to `openspec/changes/archive/2026-07-18-add-fts-state/` (all artifacts: proposal, design, tasks, verify-report, specs)
- [x] All change artifacts archived in the dated folder
- [x] Canonical spec promoted to `openspec/specs/fts-state/spec.md`

**Engram**:
- [x] Archive report saved with topic key `sdd/add-fts-state/archive-report` (this document)
- [x] Traceability: observations for apply-progress (#946), bounded-review (#948)

## Next Steps

**For the project**:
- Archive folder now permanently in `openspec/changes/archive/2026-07-18-add-fts-state/`
- Main spec tree updated: `openspec/specs/fts-state/spec.md` is the canonical, promoted spec for the `fts-state` capability
- No follow-up changes required for this change (MVP-1 lexical index is complete)

**Unblocked downstream changes**:
- `add-ollama-client` (MVP-1 change #2) — unblocked, can now depend on `state.fts.build_index` as a library
- `add-query-command` (MVP-1 change #3) — unblocked, can now depend on `state.fts.FtsIndex.search` as retrieval foundation

**Documented non-blocking follow-ups** (optional SUGGESTIONs from lineage `review-381fdecb5995f800`, not blockers):
- Enhance `search()` docstring to explicitly document the `concept_id` tie-break behavior (secondary-key ordering)
- Simplify `_quote_query` helper — optional refactor for readability
- Add explicit connection-closing test for the `FtsUnavailable` exception path (defensive coverage enhancement)

## Risks & Mitigations

| Risk | Likelihood | Mitigation | Status |
|---|---|---|---|
| Unreadable/unparseable file crashes build | Low (pre-fix) | Guarded second read/parse via broad `except` + skip+note (mirrors lint.collect_docs) | **FIXED via correction batch** (tests: `test_build_index_skips_doc_whose_second_read_fails`, `test_build_index_skips_doc_whose_second_parse_fails`) |
| SQLite connection leak on build exception | Med (pre-fix) | Wrapped build body in try/except BaseException with conn.close() | **FIXED via correction batch** (test: `test_build_index_closes_connection_on_mid_build_exception`) |
| Nondeterministic truncation on tied bm25 scores | Med (pre-fix) | Added secondary `concept_id` key to ORDER BY rank | **FIXED via correction batch** (test: `test_search_breaks_bm25_ties_by_concept_id_regardless_of_insertion_order`) |
| Query string crashes on FTS5 operators | Low | Per-token quoting + OR-join, bound parameter, defensive exception handling (design D6) | Verified by `test_search_never_raises_on_fts5_syntax` (7-case parametrize: `*`, unbalanced `"`, `AND`, `NEAR`, mixed) |
| FTS5 unavailable at runtime | Low | Detect at build time via `FtsUnavailable` exception (design D7) | Verified by `test_build_index_raises_fts_unavailable_when_fts5_not_compiled` (mocked) |
| Path traversal via concept_id | Low | OKF concept IDs are bundle-relative paths (same as `forget` uses); no unlink here (module is library-only) | Design D2 reuses existing `_iter_docs` + identity; no risk in this module |

## Deferred/Out-of-Scope Items

**Explicitly deferred to MVP-2**:
- Persistence (`.openkos/openkos.db`, `.gitignore`, locks, transaction machinery)
- Incremental indexing / change-detection tied to `ingest`/`forget`
- Chunking into passages (document-granularity only)
- Vector/semantic retrieval, graph, hybrid ranking
- Automated CI guard for canonical/derived layering (import-linter, deferred with doc-correction acknowledging it)

**Accepted residual limitations**:
- None beyond intentional MVP-2 deferrals. The module achieves its scope: pure canonical-layer FTS5 index library, no CLI, no persistence, rebuild-per-run, full spec coverage.

## Traceability

This archive report records the final state of the `add-fts-state` change from proposal through implementation, bounded review corrections, verification, and archival. The change has been:
- Fully specified (9 requirements, 12 scenarios, `fts-state` capability spec at `openspec/specs/fts-state/spec.md`)
- Fully designed (7 architecture decisions D1-D7, in-memory FTS5 design, query-safety strategy, lifecycle model)
- Fully implemented (single PR, 1 new module + test module + doc fix, originally 30 tests, 34 tests after correction batch, 100% fts.py coverage, 98.53% project coverage)
- Fully reviewed (two lineages: initial HIGH found 3 WARNINGs, all corrected via strict TDD; fresh review APPROVED with 3 non-blocking SUGGESTIONs only)
- Fully verified (all 12/12 spec scenarios passing tests, all 7 design decisions verified in code, 350 tests passing, 0 CRITICAL/WARNING post-correction, only optional SUGGESTION enhancements)
- Fully delivered (PR #23 merged to main with bounded review corrections applied and approval obtained)

The SDD cycle is CLOSED. The change is archived and ready for downstream changes `add-ollama-client` and `add-query-command` to build on the `fts-state` capability.

**Archive Date**: 2026-07-18 (ISO format)
**Repository Head**: 0a920cb (main, after bounded review corrections and approval, PR #23 merged)
**Specification**: `openspec/specs/fts-state/spec.md` (canonical, promoted from delta spec, 9 requirements, 12 scenarios)
**Verification Date**: 2026-07-18 (verify-report pass, post-corrections)
**Archival Status**: COMPLETE
**Artifact Observation IDs**: apply-progress #946 | bounded-review #948 (all in Engram archive)
