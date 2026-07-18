# Verify Report: add-lint-command

## Change
`add-lint-command` — `openkos lint` read-only stale-stamp + orphan-page health check.

## Mode
Strict TDD. Full artifact set present: proposal, spec, design, tasks. All 23 tasks marked complete; full verification run.

## Independent Gate Re-run (exact numbers, this session)

| Command | Result |
|---|---|
| `uv run pytest --cov -q` | **266 passed**, 100.00% total coverage. `lint.py`: 107 stmts/40 branches, 100%. `cli/main.py`: 183 stmts/42 branches, 100%. `config.py`: 116 stmts/28 branches, 100%. |
| `uv run ruff check .` | All checks passed! |
| `uv run ruff format --check .` | 29 files already formatted |
| `uv run mypy .` | Success: no issues found in 29 source files |

Numbers match the apply-progress artifact exactly (independently re-run, not trusted from apply).

## Task Completeness
23/23 tasks checked `[x]` in `openspec/changes/add-lint-command/tasks.md`. Spot-verified each phase against real code/tests (not just checkbox state): config field (Phase 1), `LintDoc`/`LintFinding`/`LintReport` + `collect_docs` (Phase 2), `parse_window`/`resolve_window` (Phase 3), `check_stale_stamps` (Phase 4), `normalize_link`/`check_orphans` (Phase 5), CLI wiring + docstring (Phase 6), docs (Phase 7), gate (Phase 8) — all present and covered by passing tests. Genuine, not cosmetic.

## Spec Conformance Matrix

| Scenario | Covering test(s) | Result |
|---|---|---|
| Run outside a workspace (non-zero exit, no traceback) | `test_lint_refuses_when_not_a_workspace` | PASS |
| Stale stamp is flagged | `test_check_stale_stamps_flags_a_stamp_beyond_the_window`, `test_lint_flags_a_stale_stamp` | PASS |
| Fresh stamp is not flagged | `test_check_stale_stamps_does_not_flag_a_stamp_within_the_window`, `test_check_stale_stamps_exact_boundary_is_not_stale` | PASS |
| Pure-ingest bundle produces zero stale findings | `test_check_stale_stamps_pure_ingest_bundle_has_zero_findings`, `test_lint_pure_ingest_bundle_shows_both_empty_states` (real `ingest --auto` round-trip) | PASS |
| Unreferenced concept flagged as orphan | `test_check_orphans_wholly_unreferenced_concept_is_orphan`, `test_lint_flags_an_orphan_page` | PASS |
| Concept linked from index.md is not orphan | `test_check_orphans_cataloged_concept_is_not_orphan` | PASS |
| Concept linked from another concept's body is not orphan | `test_check_orphans_referenced_only_from_another_concepts_body_is_not_orphan` | PASS |
| Empty/fresh bundle: no findings, exit 0 | `test_lint_fresh_bundle_empty_state` | PASS |
| Bundle with findings still exits 0 | `test_lint_flags_a_stale_stamp`, `test_lint_flags_an_orphan_page` (both assert `exit_code == 0`) | PASS |
| No mutation on any run | `test_lint_never_writes_to_the_workspace` (full tree snapshot before/after) | PASS |
| No `--json` accepted | `test_lint_rejects_json_flag` | PASS |

All 11 spec scenarios have a passing covering test at runtime (not just source inspection).

## Design-Decision Conformance

| Decision | Verification |
|---|---|
| Q1 — link normalization (`/`-rooted, plain-relative, `./`/`../`, extension-less) | `test_normalize_link_rooted_slash`, `_plain_relative`, `_dot_slash_relative`, `_dot_dot_relative`, `_extension_less_matches_md_counterpart` — all pass |
| Q2 — **log.md excluded from referenced-set** (critical invariant) | Structurally proven two ways: (1) `okf.RESERVED_FILENAMES = {"index.md", "log.md"}` — `_iter_docs`/`collect_docs` never yields log.md as a doc, so its body can never enter the "doc bodies" loop in `check_orphans`; (2) `check_orphans(docs, *, index_text)` has no `log_text` parameter at all — there is no code path through which log.md content could reach the referenced-set. `test_check_orphans_log_md_link_does_not_count_as_a_reference` locks this in by asserting a doc referenced nowhere except (hypothetically) log.md is still flagged orphan. Confirmed correct by source inspection + passing test. |
| Q3 — uniform Source treatment | `test_check_orphans_uncataloged_source_is_orphan`, `test_check_orphans_cataloged_source_is_not_orphan` — no `type` exemption in `check_orphans` source |
| Q4 — duration parser `Nd`/`Nw`, fallback to 7d on unparseable/zero/negative | `test_parse_window_parses_days_and_weeks`, `test_parse_window_rejects_zero_negative_and_garbage`, `test_resolve_window_falls_back_on_invalid_raw`, `test_resolve_window_never_raises`, CLI `test_lint_falls_back_and_prints_notice_on_bad_freshness_window` |
| Q5 — malformed `(as of ...)` dates silently skipped, never crash | `test_check_stale_stamps_skips_malformed_calendar_dates` — `STAMP_RE` shape-match then `date(y,m,d)` in `try`/`except ValueError` |

All 5 resolved decisions hold in code, confirmed by source inspection plus passing tests.

## Byte-Unchanged Check
`git diff --stat -- src/openkos/model/okf.py` → **empty**. `okf.py` is untouched; `collect_docs` calls `okf._iter_docs`/`okf.load_frontmatter` read-only, never edits the module. `check_conformance`/`survey_bundle` behavior unchanged (their tests still pass at 100% coverage with no modification to `okf.py`).

## Clock Injection
`rg "datetime.now()" src/openkos/lint.py` → no match. `lint.py` never calls `datetime.now()`; `today` is computed once in `cli/main.py::lint()` via `datetime.now(UTC).date()` and injected into `check_stale_stamps(docs, today=today, window=window)`. Confirmed by source inspection.

## Deviation Review: test `__init__.py` files
Apply added 5 empty `__init__.py` files (`tests/`, `tests/unit/`, `tests/unit/bundle/`, `tests/unit/cli/`, `tests/unit/model/`) to fix a mypy "Duplicate module named test_lint" collision between `tests/unit/test_lint.py` and `tests/unit/cli/test_lint.py`.

Verified:
- All 5 files are 0 bytes (no logic, no side effects).
- `pytest --collect-only` on both `test_lint.py` files: **61 tests collected** (52 pure + 9 CLI) — matches apply's claim.
- Full suite: 266 tests, 100% coverage — unaffected.
- `pyproject.toml`: no diff — no import-mode/packaging config change.
- Every `tests/**` directory now consistently has an `__init__.py` (checked via `fd` — no directory left without one), so this is a complete, non-partial fix, not an ad hoc patch on two directories only.

**Verdict: acceptable, benign fix.** Minimal, mechanical, fully consistent, zero behavioral or coverage impact, no packaging regression.

## Mutation Check
`test_lint_never_writes_to_the_workspace` snapshots the full tree (paths + file bytes) before and after a `lint` run against a bundle with a stale-stamp/orphan-triggering concept, and asserts equality. Confirms `lint` writes nothing.

## Issues

None CRITICAL. None WARNING. None SUGGESTION.

## Final Verdict: PASS
