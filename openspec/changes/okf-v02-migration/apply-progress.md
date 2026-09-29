# Apply Progress: okf-v02-migration

Refs #1064. Store: hybrid (openspec authoritative, Engram mirror at
`sdd/okf-v02-migration/apply-progress`). Delivery: auto-chain,
stacked-to-main, 8 PRs (`tasks.md` "Review Workload Forecast").

## Status

| Slice | Phase | PR | Status |
|---|---|---|---|
| 1 | Readers + display + ADR | PR 1 → `main` | **Done** — commit `6849b36` |
| 2a | Writers: generated + status | PR 2 → `main` | Not started |
| 2b | Writers: sources | PR 3 → `main` | Not started |
| 3a | Migration function | PR 4 → `main` | Not started |
| 3b | Ledger migration | PR 5 → `main` | Not started |
| 3c | `repair` verb | PR 6 → `main` | Not started |
| 4a | Fixture + template | PR 7 → `main` | Not started |
| 4b | Docs + renumbering | PR 8 → `main` | Not started |

## Slice 1 (Phase 1, PR 1) — Done

**Branch**: `feat/1064-okf-v02-p1-readers`, stacked on `f360390` (planning
docs + ADR-0029, already committed).
**Commit**: `6849b36` —
`feat(model): add the OKF v0.2 dual-reader accessors and stable display
vocabulary (#1064)`.
**Mode**: Strict TDD (`uv run pytest`).
**Tasks**: 1.1–1.24, all `[x]` in `tasks.md`.

### TDD Cycle Evidence

| Task(s) | Test file | RED reason (observed) | GREEN |
|---|---|---|---|
| 1.1–1.4 | `tests/unit/model/test_okf_v02_readers.py` | `AttributeError` — `okf.generation_time`/`_parse_instant` did not exist (15 failures) | 15/15 pass after adding `_parse_instant`/`generation_time` |
| 1.6 | same file | `AttributeError` — `okf.declares_deprecated` did not exist | pass after adding `declares_deprecated` |
| 1.8 | `tests/unit/test_lifecycle.py` | `AssertionError: Expected 'declares_deprecated' to be called once. Called 0 times.` (spy) | pass after routing `deprecated_concept_ids` through `okf.declares_deprecated` |
| 1.10 | `tests/unit/bundle/test_listing.py` | same spy pattern, `Called 0 times` | pass after routing `list_objects` through `okf.declares_deprecated` |
| 1.12 | same file | `AssertionError: assert 'active' == 'stable'` | pass after flipping the three `"active"` literals to `"stable"` |
| 1.14 | `tests/unit/application/test_concept_read.py` | `AssertionError: assert 'active' == 'stable'` | pass after flipping `ConceptRecord.status`'s `Literal` and computed value |
| 1.16 | `tests/unit/mcp/test_gate.py` | `AssertionError: assert 'active' == 'stable'` (fixture default) | pass after updating the `_record` fixture default; `mcp/gate.py` itself needed no change (confirmed: reads `record.status` verbatim, no duplication) |

Triangulation: 1.1–1.4 and 1.6 are parametrized tables (6–15 cases each)
covering the full value/shape space design.md's Decision 6 names.

### Collateral test fixes (pre-existing tests broken by the intentional
`active` → `stable` display-vocabulary change, not enumerated in
`tasks.md` but required to keep `uv run pytest --cov` green)

- `tests/unit/bundle/test_listing.py`: 4 pre-existing output assertions
  (`test_superseded_target_marked_deprecated_regardless_of_own_status`,
  `test_self_superseding_edge_is_dropped_and_stays_{active→stable}`,
  `test_supersedes_cycle_marks_all_members_deprecated` unaffected,
  `test_revises_edge_leaves_both_rows_{active→stable}`,
  `test_malformed_relations_contributes_no_edges_and_does_not_crash`).
- `tests/unit/cli/test_list.py`: `test_list_column_layout_is_id_type_sensitivity_status_title_in_order`
  — CLI STATUS column now prints `stable`.
- `tests/unit/cli/test_reconcile.py`: `test_revises_edge_leaves_both_concepts_{active→stable}_end_to_end`.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command | `uv run pytest tests/unit/model/test_okf_v02_readers.py tests/unit/test_lifecycle.py tests/unit/bundle/test_listing.py tests/unit/application/test_concept_read.py tests/unit/mcp/test_gate.py` → 120 passed |
| Runtime harness | `uv run openkos init` + a hand-written v0.1-shaped concept (`timestamp`, `status: active`, no `generated`) in a scratch `tmp`-style workspace; `uv run openkos list` → STATUS column `stable`; `uv run openkos status` → completes cleanly, no v0.1-shape finding |
| Rollback boundary | Revert commit `6849b36`: `generation_time`/`_parse_instant`/`declares_deprecated`, the four call-site edits (`lifecycle.py`, `bundle/listing.py`, `application/concept_read.py`, confirmed no-op in `mcp/gate.py`), the display-literal changes, the CHANGELOG entry, and the collateral test fixes. No writer has changed yet — no bundle byte changes to revert. ADR-0029 predates this commit and is unaffected by the revert. |

### Full verification (this session, unpiped, foreground/background as noted)

- `uv run ruff check .`: **All checks passed!** (after adding `# noqa: DTZ001` with rationale on the deliberately-naive `datetime` parametrize case in the new reader test)
- `uv run ruff format --check .`: 2 files needed reformatting (`test_okf_v02_readers.py`, `test_lifecycle.py`) — applied via `uv run ruff format`, then re-verified clean.
- `uv run mypy .`: 2 errors found and fixed (`attr-defined` on `lifecycle.okf`/`listing.okf` in two spy `wraps=` args — not explicitly re-exported; fixed by importing `okf` directly in both test files) → **Success: no issues found in 358 source files**.
- `uv run pytest --cov` (unpiped, background): **6944 passed, 2 skipped in 410.11s (0:06:50)**. Coverage 96.97% total (line+branch), 90.0% branch gate held (`Required test coverage of 90.0% reached`).
- `uv run python evals/run_self_tests.py`: **44 of 44 harness self-test(s) run, 0 failing.**

### Design options taken (owner pre-authorized: take the design's recommended option, report it)

- No open design decision was live for Phase 1's scope — Decisions 6–7 and
  the ADR-0029 staging (Decision 10) were already resolved in design.md;
  this slice implements them as specified, no deviation.
- Task 1.17 resolved exactly as design.md anticipated: `mcp/gate.py`
  needed no logic change (confirmed no local duplication of the display
  literal — it reads `record.status` verbatim at the one payload
  construction site).

### Deviations from design/tasks

None. Implementation matches `tasks.md` 1.1–1.24 and design.md Decisions
6–7 and 10 exactly. The only additions beyond the literal task list were
the collateral pre-existing-test fixes listed above (required, not
optional, to keep the full suite green — an intentional, spec-mandated
behavior change touches every test that asserted the old value) and the
one `# noqa: DTZ001` needed for a deliberately-naive-`datetime`
parametrize case the ruff `DTZ` rule otherwise flags.

### Git

`git diff --shortstat f360390..HEAD` (after commit `6849b36`, before the
following `docs(sdd)` commit): `12 files changed, 312 insertions(+), 25
deletions(-)`. This `apply-progress.md` and `tasks.md`'s checkbox updates
land in the separate `docs(sdd)` commit that follows.

## Next

Slice 2a (Phase 2, PR 2 → `main`, after PR 1 merges): writers — `Generated`,
`engine_actor()`, `LEGACY_ACTOR`, `OKF_VERSION = "0.2"`; builders take
`generated` and drop `timestamp`; ingest/query/CLI call sites;
`build_merged_document`'s generation rule; frozen v0.1 fixture + golden
regeneration. Requires a fresh `sdd-apply` dispatch scoped to Phase 2.
