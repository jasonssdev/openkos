# Apply Progress: okf-v02-migration

Refs #1064. Store: hybrid (openspec authoritative, Engram mirror at
`sdd/okf-v02-migration/apply-progress`). Delivery: auto-chain,
stacked-to-main, 8 PRs (`tasks.md` "Review Workload Forecast").

## Status

| Slice | Phase | PR | Status |
|---|---|---|---|
| 1 | Readers + display + ADR | PR 1 → `main` | **Done** — commit `6849b36` |
| 2a | Writers: generated + status | PR 2 → `main` | **Done** — commit `c7ed0c7` |
| 2b | Writers: sources | PR 3 → `main` | **Done** — commit `c0e0cdb` |
| 3a | Migration function | PR 4 → `main` | **Done** — commit `0edb102` |
| 3b | Ledger migration | PR 5 → `main` | **Done** — commit `363107e` |
| 3c | `repair` verb | PR 6 → `main` | **Done** — commit `42459a5` |
| 4a | Fixture (Phase 7) | PR 7 → `main` | **Done** — commit `a3cb43f` |
| 4b | Docs + renumbering (Phase 8) | PR 8 → `main` | **Done** — commit `72aa477` |

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

## Slice 2 (Phase 2, PR 2) — Done

**Branch**: `feat/1064-okf-v02-p2a-generated`, stacked on `afafa8f`
(Phase 1, PR 1, not yet merged to `main`).
**Commit**: `c7ed0c7` —
`feat(model): emit generated/status: stable from every writer (#1064)`.
**Mode**: Strict TDD (`uv run pytest`).
**Tasks**: 2.1–2.24, all `[x]` in `tasks.md`.

### TDD Cycle Evidence

| Task(s) | Test file | RED reason (observed) | GREEN |
|---|---|---|---|
| 2.1 | `tests/unit/model/test_okf.py` | `AttributeError: module 'openkos.model.okf' has no attribute 'Generated'` (collection error) | pass after adding `Generated`/`LEGACY_ACTOR`/`engine_actor` |
| 2.3 | same file | `AssertionError: assert '0.1' == '0.2'` | pass after bumping `OKF_VERSION` |
| 2.5–2.6 | same file | `TypeError: build_source_concept() got an unexpected keyword argument 'timestamp'` (once `_build_call_source` was updated to pass `generated=`) | pass after the signature change + `# Citations` removal |
| 2.8 | same file | same `TypeError` shape for `build_concept` | pass after the signature change |
| 2.10 | `tests/unit/application/test_ingest.py` | `TypeError` at the `build_source_concept`/`build_concept` call sites inside `compose_source_document`/`stage_derived_objects` (still passed `timestamp=`) | pass after wrapping `generated=okf.Generated(by=okf.engine_actor(), at=timestamp)` at each call |
| 2.12 | `tests/unit/application/test_query_filing.py` | same `TypeError` shape at `stage_filed_answer`'s `build_concept` call | pass after the same wrapping |
| 2.15–2.16 | `tests/unit/model/test_okf.py` | `AssertionError` — `_absorbed_is_more_recent` still read raw `timestamp`, and the merged document kept a bare `timestamp` key / unnormalized `status: active` | pass after routing through `generation_time`, writing v0.2 `generated`, and adding the `active`→`stable` normalization |

Triangulation: 2.15's `test_build_merged_document_generation_rule_table`
covers all four of design.md Decision 5/6's cases (v0.2×legacy both
directions, both v0.2, both legacy) in one function; 2.16's
`test_build_merged_document_status_active_to_stable` is parametrized over
4 survivor/absorbed status combinations.

### Mutation-proof check (this session)

`build_merged_document`'s `if merged.get("status") == "active": merged["status"]
= "stable"` line was replaced with `if False: ...` (mutation), confirmed 3
tests fail (`test_build_merged_document_status_active_to_stable[active-...]`,
`[None-active-stable]`, `test_build_merged_document_scalar_fields_survivor_wins`),
then reverted with the exact inverse edit and `__pycache__` purged; re-run
confirmed green.

### Collateral test fixes (pre-existing tests broken by the intentional
`OKF_VERSION` bump and the `timestamp`→`generated`/`status: active`→`stable`
writer changes, not individually enumerated in `tasks.md` 2.20 by name but
required to keep `uv run pytest --cov` green — the same "fix the golden,
never skip it" rule Phase 1 and design.md's "Goldens and fixtures" section
both state)

- `tests/unit/model/test_okf.py`: `test_okf_version_is_0_1` renamed to
  `test_okf_version_is_0_2`; `test_frontmatter_round_trip` expected value;
  the byte-pinned `test_build_concept_output_byte_identical_regression` and
  `test_build_concept_related_notes` goldens regenerated with
  `generated:`/`status: stable`; 5 direct `okf.build_concept(...,
  timestamp=...)` calls (type-alternative tests) switched to `generated=`;
  4 `build_merged_document` tests asserting a bare `merged["timestamp"]`
  switched to asserting `merged["generated"] == {"by": okf.LEGACY_ACTOR,
  "at": ...}` plus `"timestamp" not in merged`.
- `tests/unit/bundle/test_index.py`:
  `test_render_index_returns_version_frontmatter_and_empty_body`'s expected
  `okf_version` value.
- `tests/unit/bundle/test_merge.py`:
  `test_plan_merge_frontmatter_conflicts_scalar_list_freshness`'s merged-
  timestamp assertion, same `generated`/`LEGACY_ACTOR` shape.
- `tests/unit/cli/test_backfill_sensitivity.py`,
  `tests/unit/cli/test_set_sensitivity.py` (3 call sites),
  `tests/unit/cli/test_backfill_source_titles.py`,
  `tests/unit/cli/test_ingest.py` (1 fixture-builder call site): direct
  `okf.build_concept`/`build_source_concept` calls switched from
  `timestamp=` to `generated=okf.Generated(...)`.
- `tests/unit/cli/test_ingest.py`:
  `test_successful_ingest_of_valid_path` (no more `# Citations`, asserts
  `generated`/`status: stable`); `test_reingest_still_refreshes_timestamp_and_description`
  (reads `metadata["generated"]["at"]` instead of `metadata["timestamp"]`);
  `test_reingest_with_equal_values_writes_byte_identical_output` and
  `test_reingest_of_identical_bytes_writes_a_byte_identical_source_document`
  (the "only the refreshing field changed" line-diff assertion now checks
  `b.strip().startswith("at:")` instead of `"timestamp" in b`, since the
  refreshing field is now the nested `generated.at` line, not a top-level
  `timestamp:` line).
- `tests/unit/cli/test_reconcile.py`:
  `test_additive_only_preserves_existing_body_and_relations`'s post-reconcile
  status assertion (`"active"` → `"stable"`, since the concepts under test
  are created by a real `ingest` call using the new builder default).
- `tests/unit/application/test_ingest.py`: `_prior_concept_text`'s
  `timestamp=` fixture kwarg switched to `generated=`.
- `tests/unit/model/test_okf.py`: added `from importlib.metadata import
  PackageNotFoundError` and referenced it directly (not via
  `okf.PackageNotFoundError`) — mypy's strict re-export check flagged the
  attribute as not explicitly re-exported from `model/okf.py`, the same
  class of fix Phase 1 needed for `okf.declares_deprecated` spies.

**Confirmed NOT collateral (deliberately left as `status: "active"` /
`timestamp:`)**: `tests/unit/bundle/test_source_titles.py`'s
`_source_doc`/`_NON_CANONICAL_DOC` fixtures (hand-built v0.1-shaped
documents via `okf.dump_frontmatter` directly, testing that
`backfill-source-titles` still works on a legacy-shaped document — dual-
reader compatibility, not a builder call); `tests/unit/retrieval/test_answer.py`'s
`_write_doc(status="active", ...)` (a hand-written fixture testing the
status-aware-retrieval "legacy `active` is not deprecated" scenario on
purpose).

### Design/task-list deviations (owner pre-authorized: take the design's
recommended option, report it)

- **Task 2.14 confirmed as a no-op.** Design.md's File Changes table and
  task 2.14 both describe `cli/main.py`'s 4 call sites (approx. lines 5302,
  5421, 5516, 15081) as needing a `timestamp=` → `generated=` edit. During
  implementation this was confirmed unnecessary: those 4 CLI call sites
  invoke the APPLICATION-layer staging functions
  (`compose_source_document`, `stage_derived_objects`,
  `compose_catalog_update`, `stage_filed_answer`), whose own `timestamp:
  str` parameter is unchanged by this slice — only the deeper call FROM
  those functions TO `okf.build_source_concept`/`okf.build_concept`
  (task 2.11/2.13, inside `application/ingest.py`/`application/query.py`)
  wraps the value into `okf.Generated(...)`. Evidence: `grep -n
  "timestamp=" src/openkos/cli/main.py` after 2.11/2.13 landed still shows
  the same 4 lines unchanged, and the full CLI test suite (including the
  real end-to-end `test_successful_ingest_of_valid_path`) is green with no
  edit there. This mirrors task 1.17's precedent (confirm, don't blindly
  edit, when the design's anticipated change turns out to already be
  satisfied one layer down).
- **Task 2.18's fixture had no pre-existing "golden" to move.** No JSON
  golden of `build_source_concept`/`build_concept` output existed anywhere
  in the repo before this slice (confirmed by grep) — the byte-pinned
  goldens task 2.18 describes as "today's v0.1 builder goldens" were
  actually the INLINE byte-pinned string assertions in `test_okf.py`
  (`test_build_concept_output_byte_identical_regression` etc.), which stay
  in place as regenerated v0.2 goldens (see collateral fixes above). Since
  Phase 4 (`migrate_document`, out of scope for this slice) genuinely needs
  a frozen v0.1 fixture file, `tests/unit/model/fixtures/okf_v01_documents.json`
  was CREATED (not moved) by calling the pre-change `build_source_concept`/
  `build_concept` (before 2.7/2.9 landed) with 4 representative argument
  sets (verbatim-embed Source, binary-fallback Source, minimal Concept,
  Concept with `related_notes`), recording both the exact arguments and the
  resulting text — matching the fixture shape task 4.15 later expects
  ("the exact builder arguments that produced it").
- **Task 2.19's named file is unrelated to builder output.**
  `tests/unit/model/fixtures/okf_framing_goldens.json` (confirmed by
  reading it and its one consumer, `test_okf_framing_characterization.py`)
  pins `split_frontmatter_verbatim`/framing round-trip edge cases (CRLF,
  non-ASCII, an unrelated hand-picked `okf_version: 0.1` sample STRING used
  only as arbitrary frontmatter content) — it has no `build_concept`/
  `build_source_concept` output in it at all and needs no v0.2
  regeneration. No edit made; this is a design.md/tasks.md naming drift
  against the actual repository, not a missed task.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command | `uv run pytest tests/unit/model/test_okf.py tests/unit/application/test_ingest.py tests/unit/application/test_query_filing.py` → 338 passed |
| Runtime harness | `uv run openkos init && uv run openkos ingest <fixture>` (via `tests/unit/cli/test_ingest.py::test_successful_ingest_of_valid_path`, a real Typer CLI invocation over a `tmp_path` workspace) — the written Source carries `generated: {by: <installed engine_actor()>, at: <ISO-8601>}` and `status: stable`, no `timestamp` key, no `# Citations` heading |
| Rollback boundary | Revert commit `c7ed0c7`: `Generated`/`engine_actor`/`LEGACY_ACTOR`, `OKF_VERSION`, the two builder signature changes, the four call sites (`ingest.py` ×3, `query.py` ×1), the `build_merged_document` generation rule + status normalization, and every collateral test fix listed above. Phase 1's readers (`generation_time`/`declares_deprecated`) keep reading the reverted v0.1 output correctly; no `sources` key exists yet to revert. |

### Full verification (this session, unpiped, foreground/background as noted)

- `uv run ruff check .`: **All checks passed!**
- `uv run ruff format --check .`: 3 files needed reformatting
  (`src/openkos/model/okf.py`, `tests/unit/application/test_ingest.py`,
  `tests/unit/model/test_okf.py`) — applied via `uv run ruff format .`,
  then re-verified clean.
- `uv run mypy .`: 1 error found and fixed (`attr-defined` on
  `okf.PackageNotFoundError` in the new `engine_actor` degrade test — not
  explicitly re-exported; fixed by importing `PackageNotFoundError`
  directly) → **Success: no issues found in 358 source files**.
- `uv run pytest --cov` (unpiped, background, ~7 min): **6957 passed, 2
  skipped in 424.59s (0:07:04)**. Coverage 96.97% total (line+branch),
  90.0% branch gate held (`Required test coverage of 90.0% reached`).
  Two intermediate full-suite runs during this session (before all
  collateral fixes landed) surfaced 81 then 3 failing tests; both counts
  are stale-collection artifacts of editing test files while a `uv run
  pytest` process was already mid-collection — re-running fresh after each
  batch of fixes is what produced the accurate, shrinking failure counts.
- `uv run python evals/run_self_tests.py`: **44 of 44 harness self-test(s)
  run, 0 failing.**

### Deviations from design/tasks

None beyond the three confirmed-no-op/naming-drift items recorded above
under "Design/task-list deviations" — all genuinely new behavior (`Generated`,
`engine_actor`, the builder signature change, the `# Citations` removal, the
`build_merged_document` generation rule and status normalization, the
ingest/query call-site wrapping) matches `tasks.md` 2.1–2.24 and design.md
Decision 5/6 exactly.

### Git

`git diff --shortstat afafa8f..HEAD` (after commit `c7ed0c7`, before the
following `docs(sdd)` commit): `14 files changed, 530 insertions(+), 112
deletions(-)` (458 insertions/112 deletions authored; 72 lines are the
generated `okf_v01_documents.json` fixture, excluded from authored risk
count per the review-budget policy). This exceeds the tasks.md forecast of
~250-390 authored lines — the excess is the ~12-file collateral sweep
(byte-pinned goldens + `timestamp=`→`generated=` call sites broken by the
intentional writer-shape change), which cannot be split from the production
change without leaving `main` red between commits. This `apply-progress.md`
and `tasks.md`'s checkbox updates land in the separate `docs(sdd)` commit
that follows.

## Slice 2b (Phase 3, PR 3) — Done

**Branch**: `feat/1064-okf-v02-p2b-sources`, stacked on `a86e79b` (Phase 2,
PR #1078, not yet merged to `main`).
**Commit**: `c0e0cdb` —
`feat(model): project sources from provenance at every write point (#1064)`.
**Mode**: Strict TDD (`uv run pytest`).
**Tasks**: 3.1–3.25, all `[x]` in `tasks.md`; 3.26 (commit/PR) done via this
commit, PR not yet opened by this agent (see Rules/scope: apply implements
and commits, delivery — push/PR — stays the user's decision per repository
policy, consistent with Slices 1/2a/2a's own commits).

### TDD Cycle Evidence

| Task(s) | Test file | RED reason (observed) | GREEN |
|---|---|---|---|
| 3.1–3.4 | `tests/unit/model/test_okf_sources_projection.py` | `AttributeError: module 'openkos.model.okf' has no attribute 'project_sources'` | pass after adding `SOURCES_KEY`/`project_sources` |
| 3.6 | same file | `AttributeError: ... has no attribute 'refresh_sources'` | pass after adding `refresh_sources` |
| 3.8 | `tests/unit/model/test_okf.py` | `KeyError: 'sources'` | pass after wiring `project_sources` into `build_concept` |
| 3.9 | same file | vacuous pre-3.10 pass (no `sources` logic existed); re-confirmed genuinely exercised post-3.10 | still passes — `build_source_concept` never calls `project_sources` (comment-only, per Decision 3) |
| 3.11 | same file | `KeyError: 'sources'` | pass after wiring `project_sources` (over the UNIONED `provenance`) into `build_merged_document` |
| 3.13 | `tests/unit/bundle/test_provenance.py` | `AssertionError` — stale `sources` unchanged by a retarget | pass after adding the `okf.refresh_sources(metadata)` call in `apply_provenance_rewrites` |
| 3.15 | `tests/unit/model/test_okf.py` | extended (not new-RED) — `test_build_source_concept_empty_source_note` gained a `"# Citations" not in body` assertion, confirmed already-GREEN (Phase 2's removal covers the empty-source path too) |
| 3.16 | `tests/unit/model/test_okf_sources_projection.py` | full e2e (real `compose_source_document`+`stage_derived_objects`+`plan_merge`, offline `_FakeLLM`, no CLI subprocess, no Ollama) RED with `AttributeError: ... 'SOURCES_KEY'` before 3.5, then RED with `AssertionError: assert_sources_parity found no document carrying a \`sources\` key` after 3.5 alone (vacuous-precondition RED, exactly as tasks.md anticipated), GREEN once 3.10/3.12/3.14 landed |
| 3.17, 3.20 | `tests/unit/test_sources_key_guard.py` (new file) | file did not exist; both scanner+test pairs written together (test-only code, no separate IMPL step beyond the scanners themselves) | both green on first run against the real tree |

Triangulation: 3.1 parametrizes all four Concept-ID shapes in one loop;
3.3 parametrizes 7 distinct `None`-yielding cases; 3.6 covers all three
`refresh_sources` branches (replace/remove/unchanged) in one test.

### Mutation-proof checks (this session, `__pycache__` purged before each verdict)

1. **`project_sources` `.md`-normalization** (task 3.16's suggested
   mutation): replaced `if normalized.endswith(".md"): normalized =
   normalized[:-len(".md")]` with `if False: ...`. Confirmed
   `test_project_sources_normalizes_concept_id_entries` fails (`sources/
   foo.md` no longer normalizes). The bundle-wide parity test
   (`test_sources_parity_after_ingest_and_merge`) did NOT fail under this
   same mutation — its fixture provenance entries have no `.md` suffix, so
   this mutation was invisible to it. Reverted with the exact inverse edit.
2. **`project_sources` id/resource swap** (task 3.16's alternative
   suggested mutation): swapped `{"id": normalized, "resource": f"/
   {normalized}.md"}` to `{"resource": normalized, "id": f"/{normalized}.md"}`.
   **Confirmed this does NOT fail `assert_sources_parity`** at all (task
   3.16 anticipated it would) — the parity check compares
   `metadata["sources"] == project_sources(metadata["provenance"])`, and
   both sides call the SAME (mutated) `project_sources`, so they still
   agree with each other. This is a genuine, recorded blind spot of any
   self-referential parity check: only the direct unit tests
   (`test_project_sources_normalizes_concept_id_entries`,
   `test_project_sources_key_order_is_id_then_resource`), which compare
   against a HARD-CODED literal, actually catch this class of bug. Reverted.
3. **`build_merged_document`'s union call** (task 3.16, to genuinely
   exercise the parity e2e test itself): temporarily changed `merged_sources
   = project_sources(merged.get("provenance"))` to `project_sources
   (survivor_metadata.get("provenance"))` (survivor-only instead of
   unioned). Confirmed **first PASSED VACUOUSLY** against the original
   single-shared-source e2e fixture (survivor and absorbed both cited the
   same one source, so "unioned" and "survivor-only" produced identical
   results) — the fixture was rewritten to derive the survivor and
   absorbed concepts from two DISTINCT sources so their provenance
   genuinely differs, and the mutation then correctly failed
   `test_sources_parity_after_ingest_and_merge` with a real `AssertionError`
   naming the missing second source. Reverted with the exact inverse edit;
   re-run confirmed green. (Both this and finding 2 above are instances of
   "a test that passes the first mutation check may still be vacuous" —
   recorded per project practice rather than silently accepted.)
4. **Guard 1 (`find_sources_key_reads`)**: a planted fixture file (`def
   not_exempt(metadata): return metadata.get("sources")`) confirmed
   REPORTED by the scanner when passed via `paths=`; a structurally
   identical fixture inside a function named `refresh_sources` confirmed
   NOT reported (proves the exemption gate, not just the detector). Both
   are permanent tests using `tmp_path`, not a temporary production edit.
5. **Guard 2 (`find_provenance_key_writers`)**: a planted fixture function
   (`def rogue_writer(metadata): metadata["provenance"] = [...]`) confirmed
   DETECTED and confirmed to fail `writers <= _ALLOWED_PROVENANCE_WRITERS`
   if it were part of the real scan. Separately, `apply_provenance_rewrites`
   monkeypatched with a no-op `okf.refresh_sources` stand-in (task 3.22)
   confirmed a document's stale `sources` key survives a retarget
   unchanged, no longer matching `project_sources(provenance)` — proving
   the real call added in 3.14 is load-bearing. Both are permanent tests
   (`tmp_path` fixture / `monkeypatch`), not temporary production edits.

### Design/implementation deviations (owner pre-authorized: take the
design's recommended option, report it)

- **Guard 2 scoped to `model/okf.py` + `bundle/provenance.py`, not the
  whole `src/openkos/` tree.** Task 3.20 describes an AST walk "across
  `src/openkos/`". A whole-tree literal scan was tried first and produces
  a genuine false positive: `mcp/gate.py::disclose_get` builds an
  UNRELATED MCP disclosure payload dict that also happens to have a
  `"provenance"` key (a filtered echo of a concept's provenance ids for
  API disclosure, never a document's frontmatter). Design.md Decision 4
  itself names the exact file scope ("the projection is applied in
  exactly these functions, all in `model/okf.py` except the last...
  `bundle/provenance.apply_provenance_rewrites`"), so the guard is scoped
  to test THAT claim precisely, avoiding the false positive without
  weakening the safety property (an unauthorized bypass added to either
  of those two files is still caught; a hypothetical bypass added
  elsewhere in the tree, outside those two files, was never something a
  literal-string scan could reliably distinguish from an unrelated
  same-named key anyway).
- **Guard 2 is a subset check (`writers <= allowed`), not equality.**
  `build_merged_document`'s pre-existing generic per-key union loop
  propagates `provenance` through a DYNAMIC subscript key
  (`merged[key] = ...` inside `for key, value in absorbed_metadata.
  items()`), never a literal `"provenance"` string in source, so it is
  structurally undetectable by literal AST matching — and `migrate_document`
  (Phase 4) does not exist yet. Neither gap weakens the guard's actual
  safety property (catching an unauthorized NEW literal writer); the guard
  additionally asserts POSITIVE coverage of the two writers it CAN see
  (`build_concept`, `build_source_concept`, `apply_provenance_rewrites`),
  so it is not vacuously satisfied by an empty detected set either.
- **`test_build_concept_sources_matches_provenance_projection` drops the
  "key placement immediately after `provenance`" assertion task 3.8
  describes.** `build_concept`'s dict LITERAL does insert `"sources"`
  immediately after `"provenance"` (implemented exactly that way, with a
  comment), but `dump_frontmatter`'s YAML emission always re-sorts keys
  alphabetically (this module's own documented behavior), so a round trip
  through `load_frontmatter` always yields alphabetical key order
  regardless of the builder's insertion order -- the property is real in
  the source code but not observable from the returned STRING a black-box
  test can inspect. Value equality is the only black-box-observable
  contract; documented inline in the test.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command | `uv run pytest tests/unit/model/test_okf_sources_projection.py tests/unit/test_sources_key_guard.py tests/unit/bundle/test_provenance.py tests/unit/model/test_okf.py` → 310 passed |
| Runtime harness | `test_sources_parity_after_ingest_and_merge`: a real `application.ingest.compose_source_document` + `stage_derived_objects` (offline `_FakeLLM`, structural `LLMBackend`) ingest of TWO sources, followed by a real `bundle.merge.plan_merge` fusing their two derived concepts, all written to a `tmp_path` bundle, then `assert_sources_parity(bundle_dir)` walks every document via `okf.iter_bundle_markdown` — the merged survivor's `sources` matches `project_sources` of its UNIONED `provenance` |
| Rollback boundary | Revert commit `c0e0cdb`: `SOURCES_KEY`/`project_sources`/`refresh_sources`, the four call sites (`build_concept`, `build_source_concept`'s comment-only no-op, `build_merged_document`, `apply_provenance_rewrites`), and the two new test files. Phase 2's `generated`/`status: stable` output is untouched; Phase 1's readers keep working; no existing document's `provenance` semantics changed. |

### Full verification (this session, unpiped, foreground/background as noted)

- `uv run ruff check .`: **All checks passed!**
- `uv run ruff format --check .`: 3 test files needed reformatting after
  first draft (`tests/unit/bundle/test_provenance.py`,
  `tests/unit/model/test_okf_sources_projection.py`,
  `tests/unit/test_sources_key_guard.py`) — applied via `uv run ruff format`,
  then re-verified clean.
- `uv run mypy .`: **Success: no issues found in 360 source files** (clean
  on first run, no fixes needed).
- `uv run pytest --cov` (unpiped, background, ~7 min): **6972 passed, 2
  skipped in 419.49s (0:06:59)**. Coverage 96.98% total (line+branch),
  90.0% branch gate held (`Required test coverage of 90.0% reached`).
- `uv run python evals/run_self_tests.py`: **44 of 44 harness self-test(s)
  run, 0 failing.**

### Collateral test fixes (pre-existing byte-pinned goldens, updated
because a Concept-ID-shaped `provenance` fixture now legitimately gains a
`sources` key — anticipated and pre-authorized as "legitimate collateral"
in this session's own instructions)

- `tests/unit/model/test_okf.py`:
  `test_build_concept_output_byte_identical_regression` and
  `test_build_concept_related_notes` (the same shared golden string) both
  gained a `sources:\n- id: sources/call-with-maria-salazar\n  resource:
  /sources/call-with-maria-salazar.md\n` block between `sensitivity:` and
  `status:` (alphabetical YAML key order) — the fixture's own
  `provenance` (`["sources/call-with-maria-salazar"]`) is a genuine
  Concept ID, not a `raw/` path, so `project_sources` now legitimately
  returns a non-`None` projection for it. Both docstrings updated to note
  the re-update.

### Deviations from design/tasks

None beyond the "Design/implementation deviations" recorded above — all
genuinely new behavior (`SOURCES_KEY`, `project_sources`, `refresh_sources`,
the four call-site wirings, both AST guards, the parity helper/e2e test)
matches `tasks.md` 3.1–3.25 and design.md Decisions 3/4 exactly.

### Git

`git diff --shortstat a86e79b..HEAD` (after commit `c0e0cdb`, before the
following `docs(sdd)` commit): `6 files changed, 890 insertions(+), 2
deletions(-)`. This exceeds the tasks.md forecast of "under or near 400"
per slice — the excess is almost entirely the two NEW test files
(`test_okf_sources_projection.py` 310 lines,
`test_sources_key_guard.py` 353 lines: two independent AST scanners plus
their own mutation-proof tests, an e2e ingest→merge fixture, and this
project's established heavily-documented docstring convention). This
slice cannot be split further without leaving `main` red between commits
(the parity e2e test and both guards depend on every builder/retarget call
site landing together) — reported per the owner's pre-approved
`size:exception` for an unsplittable slice, the same authorization Slice
2a used at 530 lines. This `apply-progress.md` update and `tasks.md`'s
checkbox updates land in the separate `docs(sdd)` commit that follows.

## Slice 3a (Phase 4, PR 4) — Done

**Branch**: `feat/1064-okf-v02-p3a-migrate-document`, stacked on `634b3f6`
(Phase 3, PR #1079, not yet merged to `main`).
**Commit**: `0edb102` —
`feat(model): add the pure OKF v0.1 to v0.2 document migration function (#1064)`.
**Mode**: Strict TDD (`uv run pytest`).
**Tasks**: 4.1–4.21, all `[x]` in `tasks.md`.

### TDD Cycle Evidence

| Task(s) | Test file | RED reason (observed) | GREEN |
|---|---|---|---|
| 4.1–4.3 | `tests/unit/model/test_okf_migrate_document.py` (new file) | `AttributeError: module 'openkos.model.okf' has no attribute 'migrate_document'` (all 27 tests in the file, collected before any implementation existed) | pass after adding `MigrationChanges`/`Unchanged`/`Migrated`/`Refused`/`MigrationResult`/`migrate_document` with R1 + the two refusal checks (frontmatter-missing, non-scalar `timestamp`) |
| 4.5–4.6 | same file | same collection-time `AttributeError` (written before any implementation, per the file's single-commit build order — see "Deviations" below) | pass once R2/R3 were added in the same implementation pass |
| 4.8–4.9 | same file | same | pass once R4 + the `legacy_citations` report were added |
| 4.11 | same file | same | pass once the unparseable-frontmatter refusal was wired as the first check |
| 4.13–4.16 | same file | same | pass on the same implementation pass — no divergence found (4.17 needed no fix) |

Triangulation: 4.1–4.3 is a 5-case parametrized table (3 no-op shapes + 2
refusal shapes) covering the whole R1 condition space in one function;
4.14/4.15 loop over all 4 entries of `okf_v01_documents.json`.

### Mutation-proof checks (this session, `__pycache__` purged before each
verdict)

1. **R1's `yaml.compose` extraction**: replaced the `_scalar_source_text`
   call with a naive `datetime.isoformat()`/`str()` conversion. Confirmed
   `test_migrate_document_r1_preserves_unquoted_timestamp_source_text`
   fails (`'2026-07-14T09:00:00+00:00' != '2026-07-14T09:00:00Z'` — the
   exact corruption design.md Decision 5 names). Reverted with the exact
   inverse edit.
2. **R4's `bare_trailing` detection**: forced it to always `True`.
   Confirmed `test_migrate_document_reports_legacy_citations_without_converting`
   fails — the mutated function silently deleted a hand-authored
   `# Citations` section instead of reporting it. Reverted.
3. **R3's sources diff check**: dropped the `!= projected_sources`
   comparison (always set when non-`None`). Confirmed
   `test_migrate_document_r3_sources_set_or_unchanged`'s
   already-matching case fails (`Migrated` instead of `Unchanged`) —
   proves the idempotency property's "no rule fires on a second pass"
   case is genuinely exercised, not vacuous. Reverted.

All three reverted with the exact inverse edit; `find . -name __pycache__
-exec rm -rf {} +` run before each verdict per project practice.

### Design/implementation deviations (owner pre-authorized: take the
design's recommended option, report it)

- **Strict TDD's per-rule RED/GREEN cycle was compressed into one
  implementation pass, not four.** Tasks 4.1–4.17 are written as four
  paired TEST/IMPL groups (R1 in isolation, then R2+R3, then R4, then the
  properties), each expected to show its OWN distinct RED reason once the
  PRECEDING group's implementation exists (e.g. task 4.5's docstring
  anticipates "`AssertionError` — R2 not implemented" as a RED state
  reached only after R1 alone is implemented). In practice, the full test
  file (all 27 tests across all four rule groups plus the three property
  tests) was written first, observed RED as one batch (`AttributeError:
  ... has no attribute 'migrate_document'`, since the function does not
  exist until ANY of it is implemented), then `migrate_document` was
  implemented in one pass covering R1–R4 together, and the whole file went
  GREEN in one run. This still satisfies the Three Laws (no production
  code before a failing test existed for it) and the Hard Gate (a TDD
  Cycle Evidence table with an observed RED reason per task group, GREEN
  confirmed by execution), but does NOT show four SEPARATE RED
  reasons progressing task-by-task as tasks.md's docstrings anticipated,
  because implementing R1 alone first and re-running the suite between
  each rule would have taken 4 separate pytest invocations for a function
  whose four rules share one control-flow body (an early return after R1
  alone would make R2–R4's tests fail with a DIFFERENT symptom --
  `Unchanged` instead of `Migrated` -- not the specific `AssertionError`
  text tasks.md's docstrings predict either, since a partial R1-only
  `migrate_document` returns `Unchanged` for a document only R2 should
  change, which is what `assert isinstance(result, ...)` still calls
  `AssertionError` — the anticipated reason and the actual one only differ
  in which assertion trips first). No task's coverage or acceptance
  criteria were skipped; every scenario 4.1–4.16 names has its own test,
  passing for the reason its docstring states. Recorded as a deviation in
  PROCESS, not in test coverage.
- **Task 4.16's commutation test also covers `apply_link_rewrites`**,
  beyond the two the task literally names (`apply_provenance_rewrites`,
  `apply_relation_rewrites`) — design.md's own prose says "and the same
  for `apply_relation_rewrites`/`apply_link_rewrites`", so all three are
  exercised via their real bundle scan functions
  (`find_inbound_provenance_rewrites`/`find_inbound_relation_rewrites`/
  `find_inbound_link_rewrites`), never hand-constructed rewrite records.
- **`migrate_document`'s two `guard` exemptions (Phase 3's
  `test_sources_key_guard.py`) needed no edit.** Both guards already
  pre-exempted the literal function name `"migrate_document"` (tasks
  3.17/3.20's own forward-reference note); confirmed by running
  `tests/unit/test_sources_key_guard.py` unchanged after this slice landed
  — both guard tests still pass with zero modification to that file.
- **R3's rule intentionally does NOT remove a stale `sources` key when
  `project_sources(provenance)` is `None`** (unlike `refresh_sources`,
  which does). This matches design.md Decision 8's R3 row literally
  ("`project_sources(provenance)` is not `None` and differs...") — a v0.1
  document migrated by `migrate_document` never had a `sources` key to
  begin with (no engine before this change ever wrote one), so this
  asymmetry has no observable effect on any real migration; documented in
  `migrate_document`'s own docstring so a future reader does not "fix" it
  into matching `refresh_sources`.
- **A defensive `if not isinstance(metadata, dict): return Refused(...)`
  check was written, then removed** after `mypy .` flagged it "Statement
  is unreachable" (`load_frontmatter`'s own return type is `tuple[dict[str,
  object], str]`, and an empirical check confirmed `frontmatter.loads`
  degrades a non-mapping frontmatter root to `{}` at runtime too, never a
  non-dict value) — removing it costs no safety and satisfies `mypy`'s
  strict analysis; the malformed-YAML refusal path is still covered by the
  surrounding `try`/`except Exception` block.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command | `uv run pytest tests/unit/model/test_okf_migrate_document.py` → 27 passed |
| Runtime harness | N/A — a pure library with no caller until Phase 6 wires `repair`'s apply phase (per `tasks.md`'s own "Suggested Work Units" table for this unit); the property tests (idempotency, builder-equivalence, three-way commutation) are the closest thing to an integration check this slice has, and all three ran against real fixture data / real bundle scan functions, never hand-constructed rewrite records |
| Rollback boundary | Revert commit `0edb102`: `migrate_document`/`MigrationResult`/`MigrationChanges`/`Unchanged`/`Migrated`/`Refused`, `_citations_state`/`_scalar_source_text`, and the new test file. Nothing else in the tree imports `migrate_document` yet, so no other bundle-facing behavior changes. |

### Full verification (this session, unpiped, foreground/background as noted)

- `uv run ruff check .`: **All checks passed!**
- `uv run ruff format --check .`: 2 files needed reformatting
  (`src/openkos/model/okf.py`, `tests/unit/model/test_okf_migrate_document.py`)
  — applied via `uv run ruff format`, then re-verified clean.
- `uv run mypy .`: 4 errors found and fixed on first run (`no-any-return`
  on `_scalar_source_text`'s `ScalarNode.value` — narrowed with an explicit
  `isinstance(scalar_value, str)` check; "Statement is unreachable" on the
  dead `isinstance(metadata, dict)` guard — removed, see deviations above;
  two errors in the test file's `dict(fixture["args"])` call over an
  `object`-typed value — fixed with explicit `isinstance` narrowing instead
  of a `type: ignore`) → **Success: no issues found in 361 source files**.
- `uv run pytest --cov` (unpiped, background, ~7 min): **6999 passed, 2
  skipped in 436.89s (0:07:16)**. Coverage 96.95% total (line+branch),
  90.0% branch gate held (`Required test coverage of 90.0% reached`).
  `src/openkos/model/okf.py` itself: 97% (the uncovered lines are
  defensive branches never exercised by any fixture -- e.g. a YAML scalar
  node whose resolved `.value` is not a `str`, and the `has_generated=False,
  has_timestamp=False` combination with no other rule firing either --
  both fail-closed paths, not missing behavior).
- `uv run python evals/run_self_tests.py`: **44 of 44 harness self-test(s)
  run, 0 failing.**

### Deviations from design/tasks

None beyond the four items recorded above under "Design/implementation
deviations" (the TDD-cycle-granularity process note, the link-rewrites
addition to 4.16, the guard-exemption confirmation, and the R3-vs-
`refresh_sources` asymmetry) — all genuinely new behavior (`migrate_document`
and its four rules, the three required properties) matches `tasks.md`
4.1–4.21 and design.md Decision 8 exactly.

### Git

`git diff --shortstat` for this slice's commit (`0edb102`): `2 files
changed, 714 insertions(+)` — 223 lines in `src/openkos/model/okf.py`, 491
lines in the new `tests/unit/model/test_okf_migrate_document.py`. This
exceeds the tasks.md forecast of ~200-330 authored lines for this slice —
the excess is almost entirely the new test file's size (27 tests covering
4 independent rules, 5 no-op/refusal branches, and 3 required properties,
each following this project's documented docstring convention). This
slice cannot be split further without leaving `main` red between commits
(the idempotency/builder-equivalence/commutation property tests all
require every rule R1–R4 implemented together to be meaningful) —
reported per the owner's pre-approved `size:exception` for an
unsplittable slice, the same authorization Slices 2a/2b used. This
`apply-progress.md` update and `tasks.md`'s checkbox updates land in the
separate `docs(sdd)` commit that follows.

## Slice 3b (Phase 5, PR 5) — Done

**Branch**: `feat/1064-okf-v02-p3b-ledger`, stacked on `8a6b73f` (Phase 4,
PR #1080/#1079, not yet merged to `main`).
**Mode**: Strict TDD (`uv run pytest`).
**Tasks**: 5.1-5.18, all `[x]` in `tasks.md`.

### TDD Cycle Evidence

| Task(s) | Test file | RED reason (observed) | GREEN |
|---|---|---|---|
| 5.1-5.9 (real tests, run) | `tests/unit/bundle/test_ledger_okf_migration.py` (new file) | `AttributeError: module 'openkos.bundle.ledger' has no attribute 'migrate_sidecars_to_okf_v02'` (all 11 real tests, collected before any implementation existed) | pass after adding `migrate_sidecars_to_okf_v02` + its helpers (`_apply_migrate_document`, `_migrate_whole_document_snapshot`, `_needs_okf_version_flip`/`_flip_index_okf_version`, `_body_start`, `_build_snapshot_index`, `_resolve_post_merge_text`, `_shift_link_rewrites`, `_migrate_entry`) in one implementation pass |
| 5.10-5.14 (round-trip, skipped) | same file | `ModuleNotFoundError` on `from openkos.application import repair` (verified: the deferred import inside each test body references a module that does not exist until Phase 6) | intentionally left `pytest.mark.skip(reason="okf-v02-migration Phase 6 not yet landed")`; collected (5 items), not executed; unskip is Phase 6's own task (6.16 references back) |

Triangulation: 5.4's `index_before` flip test is parametrized over all four
V1-V4 schema constants (one shared body, four cases); 5.7/5.8 cover the
two `_resolve_post_merge_text` branches (current text vs. a real later
snapshot) with a decisive, non-coincidental construction (see deviations);
5.6 additionally proves the recursive/top-level treatment MUST share
`snapshot_events`/`current_texts` context (see deviations).

Fixtures throughout are built with the REAL merge core
(`application.lifecycle.prepare_merge`/`merge_core` -- the same two
functions `openkos merge` itself calls) in `tmp_path`, never a hand-faked
ledger; an older schema version is reached by `dataclasses.replace`
-downgrading a REAL V5 entry a real merge produced (mirrors
`test_unmerge.py::test_unmerge_snapshot_entry_still_warns_on_interleaved_drift`'s
own established technique), never inventing a fictional schema shape.

### Mutation-proof checks (this session, `__pycache__` purged before each
verdict)

1. **The below-body-start offset guard**: changed `if shift != 0 and
   rewrite.offset >= old_body_start:` to `if shift != 0:` in
   `_shift_link_rewrites`. Confirmed
   `test_migrate_sidecars_leaves_an_offset_below_body_start_unshifted`
   fails (`27 == 0` — offset shifted when it should have stayed at the
   `0` sentinel below the OLD body start). Reverted with the exact
   inverse edit.
2. **The "later snapshot over current text" priority**: changed `if
   candidates:` to `if False:` in `_resolve_post_merge_text`, forcing
   every lookup to fall through to `current_texts`. Confirmed
   `test_migrate_sidecars_shifts_link_rewrite_offsets_from_a_later_snapshot`
   fails (`109 == 109 + 27` — the shift silently became `0`, since the
   forced-current-text path is already v0.2-shaped after the real
   merge's own v0.2-emitting write, per design.md's own generation
   rule). Reverted.
3. **The recursive-entry migration's fidelity**: made
   `_migrate_whole_document_snapshot` return immediately after the
   top-level `okf.migrate_document` call, skipping the embedded-
   `merged_from` recursion entirely. Confirmed BOTH
   `test_migrate_sidecars_recurses_into_embedded_merged_from_snapshots`
   AND `test_migrate_sidecars_check_b_still_passes_after_migration` fail
   (the latter: `scan_nesting_violations` reports a fresh
   `[('concepts/z', 1)]` violation post-migration that did not exist
   pre-migration) -- proving the recursion is load-bearing for Check B's
   own nested-prefix equality, not merely for the embedded snapshot's own
   bytes. Reverted.

All three reverted with the exact inverse edit; `find . -name __pycache__
-exec rm -rf {} +` run before each verdict per project practice.

### Design/implementation deviations (owner pre-authorized: take the
design's recommended option, report it)

- **Recursion threads the FULL `_migrate_entry` pipeline into an embedded,
  pre-relocation `merged_from` list, not just its snapshot fields.**
  Task 5.3's own IMPL wording ("migrate its own snapshot fields the same
  way") reads narrower than what turned out to be REQUIRED: task 5.6
  (Check B still passes) needs an embedded historical entry's
  `index_before` flip AND `link_rewrites` offset shift to end up IDENTICAL
  to the equivalent top-level entry's, because Check B compares whole
  `MergeLedgerEntry` equality (every field), not just the four snapshot
  strings. `_migrate_whole_document_snapshot` therefore calls back into
  `_migrate_entry` itself (the SAME function applied to a sidecar's
  top-level entries), threading the identical `snapshot_events`/
  `current_texts` context, rather than a narrower snapshot-only helper --
  confirmed load-bearing by mutation-proof check 3 above. No task's
  coverage was skipped; this is a widening of 5.3's own recursion, not a
  narrowing.
- **Test 5.8's original "hand-edit the current file" construction was
  replaced with a decisive, real-merge-only construction.** The first
  draft manually appended an extra frontmatter field to "concepts/
  other.md" after a second real merge, to make "current text" and "the
  later snapshot" differ -- but the SECOND real merge's OWN write already
  emits v0.2 shape for its result (Phase 2's `build_merged_document`
  generation rule, shipped in an earlier slice, applies regardless of
  whether the merge's two inputs were v0.1), so the hand-edited current
  text turned out to be a `migrate_document` no-op (`Unchanged`) on its
  own, invalidating the intended non-zero-vs-non-zero comparison. Fixed
  by using that fact directly instead of fighting it: the test now asserts
  the CURRENT text is `Unchanged` (shift 0) while the real LATER snapshot
  (captured before that merge wrote anything) is still genuinely v0.1
  (a non-zero shift), making "which source was used" observable from a
  single non-zero-vs-zero comparison. Documented inline in the test's own
  comment.
- **`migrate_sidecars_to_okf_v02` raises `ValueError` on an `okf.Refused`
  whole-document snapshot or a missing `okf_version` field**, rather than
  returning a three-way result type. Design.md's own Interfaces/Contracts
  section lists only `list[tuple[Path, str, list[MergeLedgerEntry]]]` as
  the return type (no `Refused` variant), and this function is a pure
  library with no caller until Phase 6 wires `repair`'s apply phase, which
  is documented (design.md Decision 9) as the layer that turns a
  migration refusal into a whole-run refusal -- "never guess," matching
  `migrate_document`'s own posture. Not separately tested in this slice
  (no task in 5.1-5.18 names it); Phase 6's `plan_repair` is expected to
  catch it.
- **V1-V4's `index_before`/`log_before`/`carried_content_ids`/
  `index_restores` downgrade needed no per-schema conditionals** in the
  test fixtures: the two-concept merge fixture used for the parametrized
  `index_before` flip test never produces relation/provenance rewrites or
  a second absorption, so `relation_rewrites=[]`, `provenance_rewrites=[]`,
  and `carried_content_ids=[]` are already valid for every one of V1-V4's
  encode-time guards (`okf.encode_merge_ledger_entry`) without branching
  by schema.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command | `uv run pytest tests/unit/bundle/test_ledger_okf_migration.py` -> 11 passed, 5 skipped |
| Runtime harness | N/A -- a pure library with no caller until Phase 6 wires `repair`'s apply phase (tasks.md's own Suggested Work Units table for this unit says the same); the 5 round-trip tests (5.10-5.14) are the closest thing to an integration check this slice has, and stay `pytest.mark.skip`-marked exactly as tasks.md specifies until Phase 6 lands `application/repair.py` |
| Rollback boundary | Revert `migrate_sidecars_to_okf_v02` and its private helpers in `src/openkos/bundle/ledger.py`, plus the new test file. No ledger sidecar has been rewritten by anything in production -- this slice adds a pure function with no caller yet. |

### Full verification (this session, unpiped, foreground/background as noted)

- `uv run ruff check .`: **All checks passed!** (after `--fix` resolved an
  unused `# noqa: F401` and an import-sort/wrap normalization in the new
  test file's five deferred `application.repair` imports).
- `uv run ruff format --check .`: 2 files needed reformatting
  (`src/openkos/bundle/ledger.py`, `tests/unit/bundle/test_ledger_okf_migration.py`)
  -- applied via `uv run ruff format`, then re-verified clean.
- `uv run mypy .`: 5 errors on first run, all in the new test file's
  deferred `from openkos.application import repair` imports (`Module
  "openkos.application" has no attribute "repair"` -- expected, Phase 6
  has not landed) -- fixed with `# type: ignore[attr-defined]` on the
  `from ... import (` line (where mypy attributes the error for a
  parenthesized multi-line import) -> **Success: no issues found in 362
  source files**.
- `uv run pytest --cov` (unpiped, background, ~7 min): **7010 passed, 7
  skipped in 426.85s (0:07:06)**. Coverage 96.93% total (line+branch),
  90.0% branch gate held (`Required test coverage of 90.0% reached`).
  7 skipped = the 2 pre-existing (Phase 4) plus this slice's 5 new
  round-trip stubs (5.10-5.14), each collected and skip-marked with a
  reason naming Phase 6. `src/openkos/bundle/ledger.py` itself: covered by
  the 11 real tests above; no new uncovered branch introduced (the module's
  overall coverage was already 100% pre-slice per its own file listing in
  the coverage report).
- `uv run python evals/run_self_tests.py`: **44 of 44 harness self-test(s)
  run, 0 failing.**
- Targeted regression check (pre-existing suites this change could affect):
  `uv run pytest tests/unit/bundle/test_ledger.py
  tests/unit/bundle/test_ledger_crash_injection.py
  tests/unit/bundle/test_ledger_walk_exclusion.py tests/unit/cli/test_merge.py
  tests/unit/cli/test_unmerge.py` -> **176 passed** (zero regressions in the
  code this slice's function reads from and reuses).

### Git

`git diff --shortstat` for this slice (working tree, pre-commit):
`src/openkos/bundle/ledger.py` +298 lines (production, the new function
and its 9 private helpers); `tests/unit/bundle/test_ledger_okf_migration.py`
+982 lines (new file: 11 real tests + 5 skip-marked round-trip stubs +
shared fixture helpers); `openspec/changes/okf-v02-migration/tasks.md`
18 lines flipped `[ ]` -> `[x]`. This exceeds the ~good-practice budget
for a single PR -- reported per the owner's pre-approved `size:exception`
for an unsplittable slice (same authorization Slices 2a/2b/3a used): the
snapshot migration (5.1-5.3), the `index_before` flip (5.4-5.5), the
link-offset shift (5.7-5.9), and the Check B-preserving recursion (5.2/5.6)
all share ONE `_migrate_entry` function and ONE threaded
`snapshot_events`/`current_texts` context -- confirmed load-bearing by
mutation-proof check 3 above -- so landing them across separate commits
would mean an intermediate commit whose recursion is provably wrong
(exactly what check 3 caught). The test file's size is dominated by
building each fixture from REAL merge code (per this session's own
instructions, never hand-faked) rather than lighter mocked inputs.

## Slice 3c (Phase 6, PR 6) — Done

**Branch**: `feat/1064-okf-v02-p3c-repair`, stacked on `427a515` (Phase 5,
PR #1081, not yet merged to `main`).
**Commit**: `42459a5` — `feat(cli): migrate an OKF v0.1 bundle to v0.2 via
repair (#1064)`.
**Mode**: Strict TDD (`uv run pytest`).
**Tasks**: 6.1-6.20, all `[x]` in `tasks.md`.

### TDD Cycle Evidence

| Task(s) | Test file | RED reason (observed) | GREEN |
|---|---|---|---|
| 6.4-6.7 | `tests/unit/application/test_repair.py` (new file) | written and implemented together (see "Deviations" below); `plan_repair`'s 4 tests confirmed by direct execution: pass on first run | 4/4 pass |
| 6.10-6.11 | same file (relocated from `tests/unit/cli/test_repair.py`, see Deviations) | `apply_repair`'s write-order and crash-injection tests, following `tests/unit/bundle/test_ledger_crash_injection.py`'s direct-monkeypatch-on-the-write-primitive pattern rather than through the CLI | pass on first run; mutation-proof checks below substitute for a literal RED history |
| 6.1-6.2 | `tests/unit/cli/test_repair.py` | Gate 2 scoping (not evaluated when nothing to extract; still refuses when extraction has pollution risk) | pass on first run against `plan_repair`'s scoped condition |
| 6.9 | same file | report-line rendering against a fixture exercising ledger extraction + document migration + sidecar migration + index flip + preserved `# Citations` at once | pass after fixing two fixture bugs (see Deviations) |
| 6.13-6.14 | same file | commit message/one-commit and second-run-no-op, using a real git identity (`isolate_git_identity`) so `_autocommit` genuinely lands | pass after fixing a duplicated `"git"` argv bug in the test's own `_git` helper |
| 6.15 | `tests/unit/cli/test_unmerge.py` | the `okf_version`-predates-0.2 hint appended to `unmerge`'s existing drift refusal | pass on first run |
| 5.10-5.14 (unskipped) | `tests/unit/bundle/test_ledger_okf_migration.py` | `ModuleNotFoundError` before this slice (collected, skip-marked); a latent `_workspace()` fixture bug (`root.mkdir` missing for the round-trip tests' separate "merged"/"reference" subdirectories, invisible while skip-marked) fixed as part of unskipping | all 5 (V1-V5 ledger schemas) pass; **0 skipped remain in the file** (16/16 collected and green) |

Per this project's "TDD-cycle-granularity" precedent (Slices 3a/3b): the
full implementation (Gate 2 scoping, `plan_repair`, `apply_repair`, CLI
wiring, the `unmerge` hint) was written in one pass rather than four
separately-observed RED states, because `plan_repair`/`apply_repair`'s
five-step plan phase and eight-step apply phase share one control-flow
body — an early partial implementation would not reproduce the SPECIFIC
RED reason each task's docstring names, only a different symptom. Every
test does have its own real RED-equivalent evidence: the 4
`tests/unit/application/test_repair.py` plan-phase tests and the CLI-level
Gate 2/unmerge-hint tests passed on first execution against the
already-written implementation (confirmed by reading each test's
assertions against the code, not assumed); the report-line and
commit-message tests each failed at least once against real fixture bugs
before passing (see below) — genuine RED, for a fixture reason rather than
a missing-implementation reason. The three targeted **mutation-proof
checks** below are the retroactive substitute for a literal RED-first
history on the implementation itself.

### Mutation-proof checks (this session, `__pycache__` purged before each
verdict, each reverted with the exact inverse edit)

1. **Gate 2 scoping condition**: changed `if unmigrated and
   bundle_ledger.bundle_wide_max_entries(bundle_dir) >= 2:` to `if
   bundle_ledger.bundle_wide_max_entries(bundle_dir) >= 2:` (dropping the
   `unmigrated and` scope). Confirmed
   `test_repair_gate2_not_evaluated_when_nothing_to_extract` fails (exit 1
   instead of 0 — Gate 2 fires even with nothing to extract). Reverted.
2. **Bundle-version flip detection**: changed `if
   index_metadata.get("okf_version") != okf.OKF_VERSION:` to `if False:`.
   Confirmed `test_plan_repair_detects_bundle_version_flip_needed` fails
   (`index_new_text` stays `None`). The 16 round-trip/Phase-5 tests in
   `test_ledger_okf_migration.py` stayed green under this same mutation --
   a genuine, recorded blind spot, since none of their fixtures hand-edit
   `index.md` to a stale version (their bundles start and stay v0.2 at the
   index level; only concept-document and ledger-snapshot shape is under
   test there). Reverted.
3. **Refused-document collection**: changed `if isinstance(result,
   okf.Refused): refused.append((concept_id, result.reason)); continue` to
   drop the `refused.append(...)` call. Confirmed
   `test_plan_repair_okf_scan_refuses_whole_run_on_any_refused_document`
   fails (`plan_repair` silently returns a `RepairPlan` instead of a
   `RepairRefusal` -- the refused document is dropped from the scan instead
   of aborting the whole run). Reverted.
4. **`_reject_drifted_targets`'s new `hint` append**: changed `if hint:` to
   `if False:`. Confirmed
   `test_unmerge_refusal_names_repair_on_an_unrepaired_bundle` fails (the
   drift refusal fires correctly but omits the `openkos repair` sentence).
   Reverted.

All four reverted with the exact inverse edit; `find . -name __pycache__
-exec rm -rf {} +` run before each verdict per project practice; full
`tests/unit/{application,cli}/test_repair.py tests/unit/cli/test_unmerge.py
tests/unit/bundle/test_ledger_okf_migration.py` suite (100 tests)
re-confirmed green after every revert.

### Design options taken (owner pre-authorized: take the design's
recommended option, report it)

- **`plan_repair`/`apply_repair` split the "apply phase" design.md's prose
  describes as eight steps across TWO layers, matching `application/
  lifecycle.py`'s own established `prepare_X`/`X_core` pattern exactly.**
  Design.md's Decision 9 literally lists `apply_repair`'s steps as "1.
  Reset-point note ... 2. `_reject_drifted_targets` ... 7. one
  `_autocommit` ... 8. `_refresh_derived_after_write`", but
  `tests/unit/application/test_layering.py::
  test_application_modules_never_import_cli_typer_or_rich` and
  `test_shared_write_helpers_are_never_forked` (ADR-0018) forbid
  `application/repair.py` from importing `typer`/`openkos.vcs` or defining
  `_reject_drifted_targets`/`_autocommit`/`_refresh_derived_after_write` --
  and the Phase 5 round-trip tests call `plan_repair`/`apply_repair`
  DIRECTLY with no drift check in between, confirming the split is
  intentional at the interface level even where design.md's prose reads
  as one function doing all eight steps. Resolution: `apply_repair`
  performs writes 3-6 (extraction, sidecar migration, documents, index
  flip); the CLI's thin `repair()` performs steps 1-2 (reset-point note,
  `_reject_drifted_targets` against `plan.baselines`) before calling
  `apply_repair`, and steps 7-8 (`_autocommit`, `_refresh_derived_after_
  write`) after -- the exact shape `merge`/`unmerge`'s own commands already
  use around `prepare_merge`/`merge_core` and `prepare_unmerge`/
  `unmerge_core`. No task's coverage was skipped; every one of design.md's
  eight steps still happens, in the same order, just split across the
  same two-layer boundary this codebase already established.
- **`apply_repair(root: Path, plan: RepairPlan)`'s `root` parameter** is
  used to derive `bundle_dir` via `config.WorkspaceLayout(root).bundle_dir`
  for building workspace-relative `"bundle/..."` touched-path strings
  (`RepairOutcome.touched`), mirroring the existing `repair()` command's
  own `f"bundle/{...relative_to(bundle_dir)...}"` convention -- `plan`
  itself already carries every absolute `Path` the writes need, so `root`
  is not otherwise load-bearing for the writes themselves.
- **`index.md`'s `okf_version` flip re-renders the frontmatter via
  `okf.load_frontmatter`/`dump_frontmatter` (like `migrate_document`
  itself), not the surgical regex substitution `bundle_ledger._
  flip_index_okf_version` (Phase 5, ledger-sidecar-internal, private) uses.**
  Design.md's own wording for this step is "frontmatter re-rendered ...
  body kept verbatim via `split_frontmatter_verbatim`" -- re-rendered, not
  a byte-surgical patch -- matching `load_frontmatter`'s own body-preserving
  parse/re-dump round trip exactly, and avoiding a second private,
  ledger-internal helper being imported/duplicated into the application
  layer for one field.
- **Extraction survivors always get a drift baseline entry, regardless of
  whether `migrate_document` also rewrites them.** `plan_repair`'s single
  `iter_bundle_markdown` scan handles both concerns per document (extraction
  strip + OKF migration), so a survivor that is ONLY an extraction target
  (no OKF changes) still needs its baseline recorded for `apply_repair`'s
  subsequent frontmatter-strip write -- confirmed correct by the existing
  `test_repair_migrates_a_clean_single_entry_ledger_verbatim` regression
  test (unchanged, still green).

### Fixture bugs found and fixed during this slice's own test-writing (not
production defects -- confirmed by direct debugging before attributing)

- **`test_repair_reports_migration_counts_and_legacy_citations`**: its
  first draft used the shared `_write_v01_concept` helper (deliberately
  v0.2-ish, no `timestamp`/`status`, used by the Gate 2 tests to prove
  "nothing to migrate") for the merge pair meant to produce a sidecar
  needing OKF migration -- so `migrate_sidecars_to_okf_v02` correctly
  found nothing to migrate and the assertion on "migrated 1 merge-ledger
  sidecar" failed. Fixed by writing genuinely v0.1-shaped concepts
  (`timestamp` + `status: active`) for that specific pair.
- **`test_repair_second_run_reports_nothing_to_migrate_and_writes_nothing`**:
  its first draft reused the file's pre-existing `_make_entry` helper,
  whose `absorbed_snapshot`/`survivor_before` are plain placeholder
  strings (`"absorbed text"`), never valid frontmatter -- harmless for
  every PRE-EXISTING test (which only exercises Gate 1/Gate 2 refusals
  before any sidecar dry-run reads that text), but the SECOND `repair` run
  in this new test's own body calls `migrate_sidecars_to_okf_v02` on the
  now-relocated sidecar's stored (still-placeholder) snapshot text, which
  correctly refuses with "cannot be migrated... missing or malformed
  frontmatter block" -- `plan_repair`'s "repair never guesses" refusal
  working exactly as designed, against an unrealistic fixture. Fixed by
  adding `_make_frontmatter_entry` (real, parseable frontmatter snapshots)
  for this test's own extraction entry.
- **The commit-message/second-run tests' own new `_git` test helper**
  double-prepended `"git"` (`_git(["git", "rev-parse", "HEAD"], ...)` where
  `_git` already prepends `"git"`), caught immediately by a
  `CalledProcessError: ['git', 'git', 'rev-parse', 'HEAD']`. Fixed by
  dropping the redundant `"git"` from every call site.
- **`test_ledger_okf_migration.py`'s pre-existing `_workspace()` helper**
  (written in Slice 3b, never exercised because the round-trip tests were
  skip-marked) calls `config.write_config(root)` without first creating
  `root` -- invisible for the file's 11 already-running Phase 5 tests
  (which call `_workspace(tmp_path)` directly, and `tmp_path` already
  exists), but the 5 round-trip tests call `_workspace(tmp_path /
  "merged")` and `_copy_workspace`'s sibling `_workspace(tmp_path /
  "reference")`-shaped paths that do NOT exist yet, so unskipping surfaced
  a genuine `FileNotFoundError`. Fixed with one `root.mkdir(parents=True,
  exist_ok=True)` line at the top of the shared helper -- backward
  compatible (a no-op when `root` already exists).

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command | `uv run pytest tests/unit/application/test_repair.py tests/unit/cli/test_repair.py tests/unit/bundle/test_ledger_okf_migration.py tests/unit/cli/test_unmerge.py` → **100 passed** |
| Runtime harness | A real `examples/good-life-demo/` copy in a fresh git repo (scratch workspace): `openkos repair` migrated 6 documents (generated: 6, status: 6, sources: 4, citations removed: 0) and flipped `bundle/index.md`'s `okf_version` `'0.1'` → `'0.2'`, all in exactly ONE commit (`git log` confirmed 1 new commit, 7 files changed); the 4 documents with hand-authored `# Citations` sections (`concepts/epicureanism`, `concepts/stoicism`, `decisions/frame-the-essay-on-the-dichotomy-of-control`, `people/maria-salazar`) were correctly reported as "left in place"; a second `openkos repair` run printed "nothing to migrate", created no new commit (`git rev-parse HEAD` unchanged), and left `git status --porcelain` empty; `openkos lint` (13/13 checks clean) and `openkos status` (clean, no OKF-shape finding) both ran cleanly against the migrated bundle |
| Rollback boundary | Revert commit `42459a5`: `src/openkos/application/repair.py` (new file, deleted on revert), the `repair()` command body and the `_okf_v02_migration_hint`/`hint=` addition to `_reject_drifted_targets` in `cli/main.py`, the new/extended test files, and `docs/cli.md`'s `repair` section. This is the first slice that mutates a REAL user bundle; per-bundle rollback of an already-migrated bundle is `git revert` of that bundle's own `openkos: repair (...)` commit, independent of this code revert (unchanged from design.md's own stated rollback boundary). |

### Full verification (this session, unpiped, foreground/background as noted)

- `uv run ruff check .`: **All checks passed!**
- `uv run ruff format --check .`: **364 files already formatted** (after
  `uv run ruff format` fixed 3 files mid-session: `cli/main.py`, the new
  `tests/unit/application/test_repair.py`, and the extended
  `tests/unit/cli/test_repair.py`).
- `uv run mypy .`: **Success: no issues found in 364 source files** (clean
  on every run this session, no fixes needed).
- `uv run pytest --cov` (unpiped, background, ~7 min): **7028 passed, 2
  skipped in 412.99s (0:06:52)**. Coverage 96.91% total (line+branch),
  90.0% branch gate held (`Required test coverage of 90.0% reached`). The
  2 skipped are the pre-existing platform/backend-conditional skips
  elsewhere in the suite (confirmed by grep -- none belong to this
  change); **zero skips remain in `test_ledger_okf_migration.py`** (16/16
  collected and passing, up from 11 real + 5 skip-marked before this
  slice).
- `uv run python evals/run_self_tests.py`: **44 of 44 harness self-test(s)
  run, 0 failing.**
- Targeted regression check: `tests/unit/cli/test_repair.py`'s 9
  pre-existing tests (ledger-only migration, both refusal gates, the
  reset-point note's two branches) all still pass unchanged against the
  new `plan_repair`/`apply_repair`-backed `repair()` command.

### Deviations from design/tasks

None beyond the "Design options taken" items recorded above (the
plan/apply-vs-CLI-adapter split for the apply phase's eight steps, the
`index.md` re-render choice, and the always-baseline-extraction-survivors
detail) and the fixture bugs found and fixed while writing this slice's
own tests (none are production defects; each is documented above with its
root cause). All genuinely new behavior -- the scoped Gate 2,
`plan_repair`'s five-step refusal-first plan, `apply_repair`'s ordered
writes, the CLI report/commit-message rendering, and the `unmerge`
`okf_version` hint -- matches `tasks.md` 6.1-6.20 and design.md Decision 9
exactly.

### Git

`git diff --shortstat 427a515..HEAD` (after commit `42459a5`, before the
following `docs(sdd)` commit): `7 files changed, 1198 insertions(+), 128
deletions(-)`. This exceeds the tasks.md forecast of ~300-400 authored
lines -- the excess is the full test suite this slice adds across three
files (`tests/unit/application/test_repair.py` new, `tests/unit/cli/
test_repair.py` extended with 6 new tests plus a `_git` helper, `tests/
unit/cli/test_unmerge.py` extended with 1 test) plus `docs/cli.md`'s
`repair` section rewrite -- reported per the owner's pre-approved
`size:exception` for an unsplittable slice (same authorization Slices
2a/2b/3a/3b used): `apply_repair`'s write-order/crash-injection tests, the
CLI wiring, and the round-trip-unskip closure all depend on the same
`plan_repair`/`apply_repair` pair landing together, and separating the
`repair` CLI wiring from the tests that exercise it end-to-end (task
6.20's own tasks.md rationale) would be the less valuable split. This
`apply-progress.md` update and `tasks.md`'s checkbox updates land in the
separate `docs(sdd)` commit that follows.

## Slice 7 (Phase 7, PR 7) — Done

**Branch**: `feat/1064-okf-v02-p4a-fixture`, stacked on `1d7116f` (Phase 6,
PR #1082, not yet merged to `main`).
**Commit**: `a3cb43f` — `docs: regenerate the good-life-demo example to
OKF v0.2 via repair (#1064)`.
**Mode**: Strict TDD (`uv run pytest`).
**Tasks**: 7.1–7.10, all `[x]` in `tasks.md`.

### Procedure (design.md "Goldens and fixtures", tasks.md 7.1–7.2)

1. Froze the CURRENT (pre-slice) v0.1-shaped `examples/good-life-demo/`
   verbatim into `tests/unit/fixtures/good_life_demo_v01/` (task 7.1) — a
   byte-for-byte copy (`openkos.yaml`, `AGENTS.md`, `raw/`, `bundle/`), so
   the example stays pinned to `repair(v0.1 fixture)` and is reproducible
   from a known input.
2. Copied that frozen fixture into a scratch git repository under this
   session's scratchpad
   (`/private/tmp/claude-501/.../scratchpad/okf-fixture-regen/`), created
   ONE baseline commit, then ran the REAL installed `openkos` binary
   (`.venv/bin/openkos`, not a CLI-runner mock) — `openkos repair` — inside
   that scratch repo. The product migrated its own canonical example: one
   commit `openkos: repair (migrate 6 document(s) and 0 ledger sidecar(s)
   to OKF 0.2)`, exit 0.
3. A second `openkos repair` run in the same scratch repo printed "nothing
   to migrate", confirming idempotency; `openkos lint` (13/13 checks
   clean) and `openkos status` (clean, no v0.1-shape finding) both ran
   clean against the migrated scratch bundle.
4. Copied the migrated `bundle/**` bytes back over the live
   `examples/good-life-demo/bundle/**` tree (task 7.2) — the 7 files
   `index.md`, `concepts/{stoicism,epicureanism}.md`,
   `decisions/frame-the-essay-on-the-dichotomy-of-control.md`,
   `people/maria-salazar.md`, `sources/{call-with-maria-2026-07-14,
   notes-on-the-enchiridion-2026-07-05}.md`. `log.md` and `raw/**` are
   untouched by `repair` and stayed byte-identical (confirmed by diff).
   No migrated document was hand-edited at any point.
5. `concepts/stoicism.md` kept its hand-written, non-empty `# Citations`
   list untouched, and the migration report named it: `openkos repair:
   left in place -- 4 documents keep a hand-written # Citations list
   (legacy, OKF 0.2 section 13.1): concepts/epicureanism, concepts/
   stoicism, decisions/frame-the-essay-on-the-dichotomy-of-control,
   people/maria-salazar` — 4 of the bundle's 6 documents carry a
   hand-authored Citations section, all correctly preserved (owner
   decision C, the pinned case this slice's tests fix in place).

### TDD Cycle Evidence

| Task(s) | Test file | RED reason (observed) | GREEN |
|---|---|---|---|
| 7.3 | `tests/unit/test_canonical_example.py` — `test_good_life_demo_bundle_equals_repair_of_frozen_v01_fixture` | `AssertionError: concepts/epicureanism.md: ... diverges from the committed example` — observed by `git stash`-ing the just-regenerated `examples/good-life-demo/bundle/**` back to its pre-slice v0.1 bytes, confirming the test fails against the OLD (still-v0.1) committed fixture, exactly as tasks.md's own docstring anticipates | pass after `git stash pop` restored the migrated bundle (task 7.2's output) |
| 7.4 | same file — `test_good_life_demo_bundle_has_no_v01_shaped_frontmatter` | `AssertionError: assert '0.1' == '0.2'` (root `index.md`), observed under the same stash | pass after the stash pop |
| 7.5 | same file — `test_good_life_demo_passes_lint_and_status_clean` | Confirmed a genuine "misleading pass" under the same stash (v0.1 content): `lint`/`status` both still exit 0 and print no literal `"0.1"` substring even against the OLD bundle, because neither command's output ever names an OKF version string — tasks.md's own docstring anticipates exactly this ("either fails or is meaningless against v0.1 content"). Real, decisive coverage for the v0.1-vs-v0.2 shape comes from 7.3/7.4, not this test; this test's job is exercising the CLI runtime path (`lint`/`status` against the shipped example), which it does regardless of shape | unaffected by the stash either way (passed both before and after) |

`test_v01_fixture_exists` (guard test, no RED needed — trivially passes
once 7.1's fixture directory exists, mirroring `test_the_example_exists`'s
own role for the shipped example).

### Design/implementation deviations (owner pre-authorized: take the
design's recommended option, report it)

- **Task 7.4's "extend the existing conformance test" targets a NEW test
  in `test_canonical_example.py`, not `test_okf.py`'s existing
  `test_check_conformance_passes_on_reference_bundle`.** The actual
  "Reference Bundle Full §11 Conformance" test tasks.md/design.md
  describe lives in `tests/unit/model/test_okf.py:319`
  (`test_check_conformance_passes_on_reference_bundle`, a single
  `okf.check_conformance(bundle_dir) == []` assertion with no notion of
  `okf_version`/`timestamp`/`status` at all — `check_conformance` checks
  OKF §11's three structural rules, not field-shape). Task 7.4's actual
  ask — assert `okf_version: "0.2"` and no bare `timestamp`/`status:
  active` — is a DIFFERENT property than conformance, so it was added as
  its own new test in `test_canonical_example.py` (the file 7.3 already
  established as this slice's home), rather than bolted onto an unrelated
  assertion in a different file under a misleading name. Same class of
  naming-drift-against-the-actual-repository already recorded in Slices
  2a/2b (e.g. task 2.19's fixture-naming note) — not a missed task; the
  conformance test itself needed no change and stayed green throughout
  (confirmed: still passes, `uv run pytest
  tests/unit/model/test_okf.py::test_check_conformance_passes_on_reference_bundle`).
- **The frozen v0.1 fixture also carries `AGENTS.md`, even though `repair`
  never touches it.** Task 7.1 names `bundle/`+`raw/`+`openkos.yaml` as
  the minimum; `AGENTS.md` was included too so the frozen copy is a
  complete, self-consistent workspace snapshot (useful independent
  evidence per the Rollback boundary note), not because `repair` or any
  test in this slice reads it.
- **No drift found (task 7.6 was a no-op).** `repair`'s scratch-run output
  matched the hand-verified expectations on the first attempt — no
  non-deterministic actor string or ordering issue surfaced (confirmed:
  `generated.by` is `openkos/legacy` everywhere, deterministic per
  design.md Decision 5; YAML list/mapping key ordering matched
  `project_sources`'s pinned `id`-then-`resource` order and
  `dump_frontmatter`'s existing alphabetical top-level sort in every
  file). No fix was needed in `application/repair.py` or the fixture
  commit.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command | `uv run pytest tests/unit/test_canonical_example.py` → 21 passed (17 pre-existing + 4 new) |
| Runtime harness | The REAL `openkos` binary (`.venv/bin/openkos`, installed editable), run twice in a scratch git repo under this session's scratchpad: first `openkos repair` (exit 0, one commit, the exact report line shape design.md specifies), then a second `openkos repair` (exit 0, "nothing to migrate", no new commit) — plus `openkos lint` (13/13 checks clean) and `openkos status` (clean) against both the scratch bundle and the FINAL shipped `examples/good-life-demo` directory itself (read-only; confirmed no `.openkos/` cache or other stray file was left behind by that direct run, via `git status --porcelain examples/good-life-demo` showing only the intended bundle-content diffs) |
| Rollback boundary | `git revert` of commit `a3cb43f` restores the v0.1-shaped `examples/good-life-demo/bundle/**` and drops the three new tests; the frozen v0.1 fixture (`tests/unit/fixtures/good_life_demo_v01/`) is independent evidence and may be kept regardless, per tasks.md's own stated rollback boundary for this phase |

### Mutation-proof check (this session, `__pycache__` purged before the
verdict)

Flipped one byte in the ALREADY-COMMITTED-TO-WORKING-TREE
`examples/good-life-demo/bundle/concepts/stoicism.md` (`status: stable` →
`status: STABLE`) and confirmed
`test_good_life_demo_bundle_equals_repair_of_frozen_v01_fixture` fails
with the exact byte-offset diff naming that file; reverted with the exact
inverse edit (`STABLE` → `stable`) and re-confirmed all 21 tests green
(`git diff --stat` on that one file returned to the same 22/11 line-change
shape as before the mutation, confirming no residual drift from the
revert).

### Full verification (this session, unpiped, foreground/background as noted)

- `uv run ruff check .`: **All checks passed!**
- `uv run ruff format --check .`: 1 file needed reformatting
  (`tests/unit/test_canonical_example.py`) — applied via `uv run ruff
  format .`, then re-verified clean (364 files already formatted).
- `uv run mypy .`: **Success: no issues found in 364 source files** (clean
  on first run, no fixes needed).
- `uv run pytest tests/unit/test_canonical_example.py` (focused): **21
  passed**.
- `uv run pytest --cov` (unpiped, background, ~7 min): **7032 passed, 2
  skipped in 420.86s (0:07:00)**. Coverage 96.92% total (line+branch),
  90.0% branch gate held (`Required test coverage of 90.0% reached`). Up
  from Slice 3c's 7028 passed (+4 new tests: `test_v01_fixture_exists`,
  `test_good_life_demo_bundle_equals_repair_of_frozen_v01_fixture`,
  `test_good_life_demo_bundle_has_no_v01_shaped_frontmatter`,
  `test_good_life_demo_passes_lint_and_status_clean`).
- `uv run python evals/run_self_tests.py`: **44 of 44 harness self-test(s)
  run, 0 failing.**
- `openkos lint` and `openkos status` against the shipped
  `examples/good-life-demo` directly (not a copy): both exit 0, 13/13
  lint checks clean, status clean with no v0.1-shape finding (see Runtime
  harness row above for the exact output).

### Deviations from design/tasks

None beyond the two naming-drift/no-op items recorded above under
"Design/implementation deviations" (7.4's actual test location, and 7.6
finding no drift to fix) — the frozen v0.1 fixture, the real `repair`-run
regeneration procedure, and all three new tests match `tasks.md` 7.1–7.10
and design.md's "Goldens and fixtures" section exactly.

### Git

`git diff --shortstat 1d7116f..HEAD` (after commit `a3cb43f`, before the
following `docs(sdd)` commit): `20 files changed, 611 insertions(+), 56
deletions(-)`. Of that, `tests/unit/fixtures/good_life_demo_v01/**` is 392
lines (12 new files, a byte-for-byte COPY of pre-existing content — the
example as it stood before this slice — excluded from authored risk count
per the review-budget policy, the same treatment Phase 2's
`okf_v01_documents.json` fixture received). Authored risk: 158 lines
across the 7 regenerated `examples/good-life-demo/bundle/**` files +
117 new lines in `tests/unit/test_canonical_example.py` = **275 authored
lines**, within design.md's ~150-290 forecast for this slice (4a). This
`apply-progress.md` update and `tasks.md`'s checkbox updates land in the
separate `docs(sdd)` commit that follows.

## Slice 8 (Phase 8, PR 8) — Done

**Branch**: `feat/1064-okf-v02-p4b-docs`, stacked on `8ad3a6b` (Phase 7,
PR #1083, not yet merged to `main`).
**Commit**: `72aa477` —
`docs: adopt OKF v0.2 field set and section numbering across docs, the
template, and code comments (#1064)`.
**Mode**: N/A (prose-only slice; no behavioral test, per tasks.md's own
"Threat Matrix: N/A" framing for this phase). Structural readback
(grep before/after) plus the full suite is the proportional check.
**Tasks**: 8.1–8.13, all `[x]` in `tasks.md`; 8.14 (commit) done via this
commit, PR not yet opened by this agent (delivery — push/PR — stays the
user's decision per repository policy, consistent with every prior slice's
own commits). 8.9 is an explicit no-op by design: the two living-spec
passages (`## OKF §9 Conformance Rules 1-3` in `openspec/specs/ingestion/
spec.md`, and the `## Non-Goals` §9 mention in `openspec/specs/lint/
spec.md`) are edited at `sdd-archive` time, not in this PR — confirmed
untouched.

### OKF v0.1 → v0.2 section-number mapping used (fetched from the live
spec — GitHub's `main` branch now serves v0.2; the true v0.1 text was
recovered from an earlier commit, `ee67a5c`, since `main` no longer serves
it)

| v0.1 section | v0.2 section | Topic |
|---|---|---|
| §2 (Terminology, Concept ID) | §2 (unchanged) | Identity |
| §4.1 (Frontmatter extensions) | §4.1 (unchanged) | Extension keys |
| §4.2 (Body headings, incl. `# Citations`) | §4.2 (unchanged number; table content differs — `# Citations` removed, `# Computation` added) | Conventional headings |
| §5.1/§5.2/§5.3 (Cross-linking: absolute links, relative links, link semantics) | §6.1 (Links between concepts — all three folded into one subsection) | Links |
| §6 (Index Files) | §8 (Index files) | Reserved file |
| §7 (Log Files) | §9 (Log files) | Reserved file |
| §8 (Citations) | Retired — superseded by frontmatter `sources`, §5.1, and the footnote convention, §13.1 | Citations |
| §9 (Conformance) | §11 (Conformance) | The three rules |
| §10 (Relationship to other formats) | Retired — no v0.2 equivalent | — |
| §11 (Versioning) | §12 (Versioning) | Root `okf_version` exception |
| — (new) | §5 (Provenance, trust, lifecycle: §5.1 `sources`, §5.2 `generated`/`verified`, §5.3 trust tiers, §5.4 `status`, §5.5 `stale_after`) | New frontmatter families |
| — (new) | §6.2 (path-valued fields), §6.3 (`references/` convention) | New link subsections |
| — (new) | §7 (Actor convention) | `generated.by` shape |
| — (new) | §10 (Attested computations) | Out of scope for this change |
| — (new) | §13 (Changes from v0.1: §13.1 breaking, §13.2 additive) | Migration notes |

This table is the load-bearing fact this slice depends on and is recorded
here since no design.md/proposal.md passage stated it exhaustively — those
documents named only the handful of mappings their own decisions needed
(§9→§11, §11→§12, §6/§7→§8/§9, §8 retired). The link-section mapping
(old §5.1/§5.3 → new §6.1) and the "§10 has no successor" fact were
derived directly from the fetched spec text, not from any prior SDD
artifact.

### Design/implementation deviations (owner pre-authorized: take the
recommended option, report it)

- **AGENTS.md's "Adopt OKF" bullet (line 21) was rewritten beyond task
  8.4's literal two named sentences.** Task 8.4 names only the two `§9`/
  `§11`-bearing sentences at (then-)lines 23 and 42; the session's own
  instructions separately named the `§2, §5.1, §5.3, §8, §9, §4.1, §6/§7`
  bullet at line 21 as in-scope (with `§9→§11`, `§6/§7→§8/§9`, `§8→§5.1`
  given explicitly). Both sources agree in substance; the session
  instructions were followed as the more complete and explicit of the two,
  since leaving line 21's stale `§5.1, §5.3` citation for LINKS unedited
  would have left it pointing at v0.2's `sources` field instead — a
  worse defect than under-scoping task 8.4.
- **The `# Citations` heading described in `docs/knowledge-object-model.md`'s
  worked example was NOT rewritten.** Per the session's explicit
  instruction ("copy from the example, do not hand-write"), the Stoicism/
  Maria-Salazar frontmatter and body were copied byte-for-byte from the
  regenerated `examples/good-life-demo/bundle/**` (Phase 7's `repair`
  output) — confirmed identical by direct file comparison. That fixture's
  `# Citations` section is genuinely still present (a pre-migration,
  hand-authored-style section `repair` correctly preserved rather than
  converted, per design.md Decision 8's R4 rule), so the doc's prose was
  rewritten to explain that correctly instead of hiding or editing it out.
- **`docs/architecture.md`'s citation-pattern paragraph (line 162) was
  rephrased, not just renumbered.** The old text quoted OKF's Citations
  heading (`OKF §8 ... "a subdirectory that mirrors external material..."`)
  to justify OpenKOS's Source-concept bridging pattern. Since v0.2 retired
  that heading, the same rationale now maps to v0.2 §6.3 (the `references/`
  convention) — the quote was updated to the v0.2 text and the sentence
  extended to also name `sources` as the mechanism that now carries this
  lineage externally.
- **`docs/cli.md`'s `merge` section (line 548) needed a substantive fix,
  not just a citation renumber.** Its prose said `freshness`+`timestamp`
  "are taken together from whichever side has the strictly more recent
  `timestamp`" — stale since Phase 2 (`build_merged_document`'s generation
  rule reads `generation_time()`, which prefers `generated.at` with a
  legacy `timestamp` fallback, and writes `generated`, never a bare
  `timestamp`). Corrected to describe the actual current rule, and added
  the `sources`-regenerated-from-unioned-`provenance` fact `merge` also
  performs but the doc never mentioned.
- **`docs/okf-alignment.md`'s field-list bullet (was line 36) dropped
  `timestamp` from OKF's own generic field description**, since v0.2's
  real §4.1 table no longer lists it as an optional field at all (`generated`
  supersedes it) — confirmed against the fetched spec text, not assumed.
- **`docs/roadmap.md`'s MVP 1 deliverables bullets were updated**, not left
  as historical narration: MVP 1's own field list/`# Citations`/`okf_version`
  mentions describe what `ingest` currently outputs (a living roadmap, per
  AGENTS.md's "docs describe the shape" rule), not a fixed historical
  snapshot — distinguished from an ADR's append-only Context section, which
  legitimately keeps `OKF v0.1 §4.1`-style citations describing the state at
  the time a past decision was made.
- **Two docs sections were left deliberately untouched** despite matching
  the `§[0-9]` grep pattern: `okf.py`'s `check_conformance` docstring
  (line 2859) and `cli/main.py`'s `next` docstring (line 10718) both quote
  the LIVING spec's own not-yet-renamed requirement-heading text
  (`"OKF §9 Conformance -- ..."` / `"Needs-Attention via §9 Conformance"`).
  Renumbering the code comment while the actual `openspec/specs/{ingestion,
  status}/spec.md` heading still reads `§9` (deferred to archive, task 8.9)
  would create a code-vs-spec-heading mismatch; left as-is on purpose.
- **`openspec/specs/status/spec.md`'s "Needs-Attention via §9 Conformance"
  requirement heading was NOT renamed**, despite `tasks.md`'s own
  Scenario/Requirement Coverage table describing it as "title-only
  renumber, covered by 8.x." The session's explicit scope boundary
  ("Do NOT edit `openspec/specs/` living specs") was followed as the
  controlling instruction: only `sdd-archive`'s delta-merge step is
  expected to touch any file under `openspec/specs/`, matching how task
  8.9 treats the two named ingestion/lint passages. If this reading is
  wrong, `status/spec.md` (and any sibling living spec whose only Phase-8-
  relevant change is a bare `§9`→`§11` title renumber with no behavior
  change) still needs that one-line heading edit before or during archive.

### Verification (this session, unpiped, foreground/background as noted)

- `grep -rn '§[0-9]' AGENTS.md docs/ src/openkos/templates examples/
  good-life-demo/AGENTS.md | head -60` — run before (baseline: `§9`/`§6`/
  `§7`/`§11`(versioning)/`§5.1,§5.3`(links)/`§8`(citations) scattered
  across all touched files) and after (59 remaining `§N` hits, all either
  already-correct v0.2 numbers this slice set, the two intentionally
  deferred living-spec-heading quotes, or accepted/append-only ADRs
  0002/0004/0023/0026/0029 correctly left alone per AGENTS.md's ADR
  policy) — confirmed clean by manual per-file review, not just count.
- `uv run pytest tests/unit/test_canonical_example.py -k agents_md`:
  **1 passed** (task 8.2's regression confirmation — the template/example
  `AGENTS.md` stayed byte-identical to a fresh `init` after 8.1/8.3).
- `uv run ruff check .`: **All checks passed!**
- `uv run ruff format --check .`: **364 files already formatted** (no
  reformatting needed — prose-only edits, no code style drift).
- `uv run mypy .`: **Success: no issues found in 364 source files**.
- `uv run pytest --cov` (unpiped, background, ~7 min): **7032 passed, 2
  skipped in 409.64s (0:06:49)**. Coverage 96.92% total (line+branch),
  90.0% branch gate held (`Required test coverage of 90.0% reached`) — no
  coverage regression, as tasks.md anticipated (only 8.2's existing
  regression check touches covered production code).
- `uv run python evals/run_self_tests.py`: **44 of 44 harness self-test(s)
  run, 0 failing.**

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command | `uv run pytest tests/unit/test_canonical_example.py -k agents_md` → 1 passed |
| Runtime harness | N/A — prose-only change, no executable behavior; structural readback (the grep above) is the proportional check, per tasks.md's own framing for this phase |
| Rollback boundary | Revert each doc/template/comment file independently (`git revert` of commit `72aa477`, or a per-file `git checkout 8ad3a6b -- <path>`); no executable behavior depends on this slice, so a partial revert is always safe |

### Deviations from design/tasks

None beyond the "Design/implementation deviations" recorded above (all
either confirmed-correct fixes to text that was already stale for reasons
beyond a bare citation renumber, or documented scope-boundary judgment
calls where two instructions could be read two ways) — every genuinely
new prose change matches `tasks.md` 8.1–8.14 and design.md's "Goldens and
fixtures" / proposal.md scope item 5 exactly.

### Git

`git diff --shortstat 8ad3a6b..HEAD` (after commit `72aa477`, before the
following `docs(sdd)` commit): `15 files changed, 103 insertions(+), 92
deletions(-)` — well under the ~150-290 authored-line forecast and the
400-line review budget. This `apply-progress.md` update and `tasks.md`'s
checkbox updates land in the separate `docs(sdd)` commit that follows.

## Next

All 8 phases are now complete (Phase 1 through Phase 8, PRs 1-8, one
commit each). `okf-v02-migration` is ready for `sdd-archive`: verify the
full chain (all 8 branches/commits), merge deltas from `openspec/changes/
okf-v02-migration/specs/` into the corresponding `openspec/specs/{domain}/
spec.md` files (including the two passages task 8.9 deferred — the
ingestion `## OKF §9 Conformance Rules 1-3` heading and the lint
`## Non-Goals` §9 mention, both renumbered to `§11` at archive time), and
archive the change. PR opening/merging for PRs 1-8 remains a user/
repository-policy decision outside apply's scope.
