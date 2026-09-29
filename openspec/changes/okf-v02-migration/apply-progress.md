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

## Next

Slice 3b (Phase 5, PR 5 → `main`, after PR 4 merges): the ledger
migration, `migrate_sidecars_to_okf_v02` -- snapshot/recursive/
`index_before`/offset-shift migration of merge-ledger sidecars, with
Check B (nested-prefix equality) preserved. A library with no caller
until Phase 6 wires `repair`'s apply phase. Requires a fresh `sdd-apply`
dispatch scoped to Phase 5.
