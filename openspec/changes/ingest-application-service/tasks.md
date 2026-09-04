# Tasks: Ingest Application Service

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~2,050–2,850 total (S1 ~250–350 / S2 ~1,100–1,500 / S3 ~700–1,000), per design's Slice Plan table |
| Effective review budget (session override) | 2,000 changed lines per PR (`review_budget_lines`) — all three slices fit individually |
| 400-line budget risk | High for S2 (~3x the skill's nominal 400-line guard) and S3 (~2x); Medium for S1; all three fit under the session's 2,000-line override |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (module + collision helpers, zero call-site repoints) → PR 2 (de-present + move `stage_derived_objects`, 20+6+1 test repoints, byte-identity goldens) → PR 3 (plan-composition core: `converged_reingest`, `compose_source_document`, `compose_catalog_update`, doc-reference repointing) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `application/ingest.py` skeleton: `DerivedPlan` + collision helpers; generalized layering guard | PR 1 | `uv run pytest tests/unit/application tests/unit/cli/test_ingest.py -q` | `uv run openkos ingest <file>` against a seeded workspace | Revert the new module and `main.py`'s two import-only edits — zero call sites touched, suite stays green |
| 2 | De-present + relocate `stage_derived_objects`; typed drops/report/notices; `on_progress` injection; adapter renders; 20+6+1 test repoints; characterization goldens | PR 2 | `uv run pytest tests/unit/cli/test_ingest.py tests/unit/application -q` | `uv run openkos ingest <file>` with a live extraction against a seeded workspace | Revert `application/ingest.py`'s service additions, the `main.py` adapter-wiring diff, and the test repoints together (GREEN depends on the move landing atomically); PR 1 stays functional standalone |
| 3 | `converged_reingest`, `compose_source_document`, `compose_catalog_update`; `_chat_client` construction moves up; D2 text-passing; doc-reference repointing | PR 3 | `uv run pytest tests/unit/cli/test_ingest.py tests/unit/application -q` | `uv run openkos ingest <file>` and `uv run openkos ingest --batch <dir>` against a seeded workspace | Revert the plan-composition-core additions and the `main.py` diff; PR 1+2 stay functional standalone, `openkos ingest` still works end to end |

## Slice 1 — PR 1 (~250–350 lines)

### Phase 0: Boundary Verification (blocking — before any file changes)
- [x] 0.1 Re-read `src/openkos/cli/main.py`'s `_DerivedPlan`, `collision_family`, `family_owns_source`, `first_free_disambiguated_slug` definitions. Confirm none call `typer`, `rich`, or `observability`, and none are called from outside `_stage_derived_objects`'s plan-building loop. If refuted, STOP and re-derive the slice boundary.
  - Confirmed: read `_DerivedPlan` (main.py:4154-4200), `_collision_family` (3236-3269), `_family_owns_source` (3272-3292), `_first_free_disambiguated_slug` (3545-3562) in full. None reference `typer`, `rich`, or `observability`. `rg` confirmed the only production call sites are inside `_stage_derived_objects`'s plan-building loop (main.py:4648-4667); the only other references are `tests/unit/cli/test_ingest.py` calling `main._collision_family`/`main._family_owns_source`/`main._first_free_disambiguated_slug` directly (5 call sites) and a docstring cross-reference in `okf.py`. No refutation — slice boundary holds as planned.

### Phase 1: Foundation — module + layering guard
- [x] 1.1 RED: rewrite `tests/unit/application/test_layering.py` to iterate `src/openkos/application/*.py` (not a hardcoded `_QUERY_MODULE`), assert offender imports include `openkos.cli*`/`typer`/`rich`, and that any `openkos.llm.*` import is exactly `openkos.llm.base`. Must fail — `application/ingest.py` doesn't exist.
  - RED confirmed: `test_application_directory_is_scanned_completely` failed (`'ingest.py'` not in `{'query.py'}`) before the module existed.
- [x] 1.2 GREEN: create `src/openkos/application/ingest.py` with a module docstring only. Layering test passes trivially (no imports yet).
- [x] 1.3 RED: `tests/unit/application/test_ingest.py::test_derived_plan_is_frozen_dataclass` and `::test_collision_helpers_resolve_disambiguated_slug` — module has no such symbols yet.
  - RED confirmed: collection failed with `AttributeError: module 'openkos.application.ingest' has no attribute 'DerivedPlan'`.
- [x] 1.4 GREEN: move `_DerivedPlan` → `DerivedPlan` (frozen dataclass, verbatim fields) and `collision_family`, `family_owns_source`, `first_free_disambiguated_slug` from `main.py` into `application/ingest.py`; `main.py` imports them. `_stage_derived_objects` unchanged behaviorally.
  - Implemented via `from openkos.application import ingest as application_ingest` plus plain module-level assignments (`_DerivedPlan = application_ingest.DerivedPlan`, etc.) rather than `from ... import X as _x`, because mypy strict's `no_implicit_reexport` flagged the renamed-import form as not re-exported when `tests/unit/cli/test_ingest.py` accessed `main._collision_family` directly. `_stage_derived_objects`'s body is byte-identical.
- [x] 1.5 REFACTOR: confirm `rg -n '_stage_derived_objects\(' tests/unit/cli/test_ingest.py` count is unchanged (zero new/removed call sites in this slice).
  - Confirmed: `tests/unit/cli/test_ingest.py` was not edited in this slice, so the count (20 call sites + 1 docstring mention = 21 matches) is unchanged by construction.

### Phase 2: Slice 1 gate
- [x] 2.1 `uv run pytest tests/unit/application tests/unit/cli/test_ingest.py -q` — all pass, wording unchanged.
  - 364 passed (43 application + 321 cli/test_ingest.py; +5 vs the 359 baseline from the 5 new tests added).
- [x] 2.2 `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy .` — whole-repo gate green.
  - `uv run pytest`: 5973 passed, 1 skipped. `ruff check .`: All checks passed. `ruff format --check .`: 290 files already formatted. `mypy .`: Success, no issues in 290 source files.
- [x] 2.3 `uv run openkos ingest <file>` against a seeded workspace — smoke test matches pre-change output.
  - Live smoke test against a fresh `openkos init` workspace with Ollama (`qwen3:8b` chat, `bge-m3` embedding) running locally: first ingest extracted 2 objects (`photosynthesis.md` Concept, `cellular-respiration.md` Procedure) and wrote them; a second `--re-extract` ingest of the same source hit the moved `_family_owns_source`/`_collision_family` path and correctly reported `'photosynthesis' already exists; skipping this candidate (create-only)` and `'cellular-respiration' already exists; skipping this candidate (create-only)` — proving the relocated collision-detection code runs correctly end to end through the CLI.

## Slice 2 — PR 2 (~1,100–1,500 lines)

### Phase 3: Typed contracts (RED first)
- [x] 3.1 RED: `test_ingest.py` (application) asserts `DropKind` literal values and `StagingDrop`/`StagedDerivedObjects` field shapes — symbols absent.
  - RED confirmed: `AttributeError: module 'openkos.application.ingest' has no attribute 'stage_derived_objects'` (and the two dataclasses) before Phase 3.2 landed; 10 of 14 new application-layer tests failed at collection/execution.
- [x] 3.2 GREEN: define `DropKind`, `StagingDrop`, `StagedDerivedObjects` dataclasses in `application/ingest.py` per design's Interfaces/Contracts.
- [x] 3.3 RED: `test_stage_derived_objects_renders_nothing` (capsys empty) and `test_stage_derived_objects_propagates_ollama_error` (`pytest.raises(OllamaUnavailable)`) — written before the GREEN implementation, both failed for the expected reason (symbol absent).
- [x] 3.4 GREEN: moved `stage_derived_objects` from `main.py` into `application/ingest.py`. All 24 `typer.echo` calls and the `Console(...).status`/`phase_callback` construction stripped from the service. Accepts `on_progress: ProgressHook | None = None`, forwarded unchanged to the selected extractor. Catches nothing from `llm.chat`; only the three degrade reasons (`no-extractable-text`, `blocked-by-sensitivity`, `no-concepts-found`) are the service's own. The 14 `_*_notice(report)` helpers (not 15 — recounted during apply, see Deviations) stayed in `main.py` verbatim, called by the new adapter render function, not moved into the service.

### Phase 4: Adapter wiring — render disclosure data
- [x] 4.1 GREEN: replaced `main.py`'s call to the deleted `_stage_derived_objects` with a call to `application_ingest.stage_derived_objects`; adapter builds `Console(stderr=True).status(...)` + `observability.phase_callback` and passes the result as `on_progress`; adapter's own `except OllamaError` (verbatim advisory block, including the #746 concurrency arm) supplies `skip_reason="failed"`; a new `_render_staged_derived_objects`/`_render_staging_drop` pair in `main.py` renders every notice/drop, in the original render order, via the relocated `_*_notice(report)` helpers plus new per-`DropKind` wording that reproduces the original per-candidate echoes verbatim.
- [x] 4.2 GREEN: confirmed `extract_concept`/`extract_concept_union` imports in `main.py` were unused after the move; `uv run ruff check .` (F401) flagged and forced their deletion, along with two now-unused imports (`openkos.llm.base.LLMBackend`, `openkos.sensitivity.blocks_llm_send`) not anticipated by the design.

### Phase 5: Test migration — call sites + monkeypatch
- [x] 5.1 Repointed `_stage_kwargs` callers (the kwargs builder itself needed no signature change — only the callee and return-unpacking at each call site changed).
- [x] 5.2 Repointed all 20 `main._stage_derived_objects(**_stage_kwargs(...))` call sites to `application_ingest.stage_derived_objects`, via a paren-depth-aware script for the 14 mechanical tuple-unpack sites, and manual rewrites for the 6 that tested the (now-relocated) `OllamaError` catch/#746-advisory directly against the internal function (see Deviations). `rg -n 'main\._stage_derived_objects\(' tests/` now returns 0 matches.
- [x] 5.3 Updated the 6 `plans == []` sites: 3 became `outcome.plans == ()`; 1 (`test_stage_derived_objects_returns_failed_reason`) was superseded by the application-layer propagation test and deleted; 2 (inside the two OllamaError-degrade unit tests) were removed along with those tests' rewrite into full CLI invocations (see Phase 5.2 deviation) — none remain comparing a tuple to `[]`.
- [x] 5.4 Confirmed empirically: running the unrepointed test (`monkeypatch.setattr(main, target, ...)`) after the move raised `AttributeError: <module 'openkos.cli.main' ...> has no attribute 'extract_concept'` — the design's mechanical guarantee held exactly as predicted. Retargeted to `monkeypatch.setattr(application_ingest, target, ...)`.
- [x] 5.5 Added `test_main_no_longer_exposes_the_extractor_names`: asserts `not hasattr(main, "extract_concept")` and `not hasattr(main, "extract_concept_union")`.

### Phase 6: Byte-identity proof (characterization goldens)
- [x] 6.1 Generated full-stream goldens on the pre-move tree via a `git worktree` checkout of `HEAD` (commit `7ec516b`, Slice 1 merged / Slice 2 not yet applied) for a REPRESENTATIVE 6-scenario subset (not the full exhaustive matrix — see Deviations): healthy single-object; `no-extractable-text`; `blocked-by-sensitivity`; `OllamaError` degrade without the #746 advisory; `OllamaError` degrade WITH the #746 advisory (`concurrent_extraction` on + timeout + `fans_out`); `no-concepts-found`. The remaining matrix entries (five staging drops, `lost_in_staging` summary, #773 convergence, `(OSError, ValueError)` refusal) are covered by the pre-existing 322 substring-level CLI tests, which all pass unmodified in wording.
- [x] 6.2 Added `tests/unit/cli/test_ingest_characterization.py` + committed `tests/unit/cli/fixtures/ingest_characterization_goldens.json`; compares live CLI `stdout`+`stderr`+exit code against the committed goldens byte-for-byte on the post-move tree. All 6 pass.
- [x] 6.3 Falsification, 3 mutate/revert cycles, `__pycache__` purged before AND after each: (1) one character in the relocated `"no concept extracted from this source"` echo → RED in `test_ingest_characterization.py`, reverted, GREEN. (2) one character in the relocated `"...already exists; skipping this candidate (create-only)"` drop message → RED in `test_create_only_reingest_drop_writes_no_staging_marker`, reverted, GREEN. (3) one character in the relocated `lost_in_staging` summary's `extraction_notice:` label → RED in `test_sole_candidate_lost_in_staging_marks_the_source`, reverted, GREEN. `git diff --stat src/openkos/cli/main.py` after all three cycles shows no residual mutation.
- [x] 6.4 Filesystem identity: `snapshot_bytes`/`snapshot_with_mtime`-based refusal tests (e.g. `test_a_write_target_edited_during_the_prompt_is_refused`, `test_a_write_target_deleted_during_the_prompt_is_refused`) are unmodified by this slice (the `(OSError, ValueError)` refusal handler was untouched) and pass in the full run.

### Phase 7: Slice 2 gate
- [x] 7.1 `uv run pytest tests/unit/cli/test_ingest.py -q` — 322 pass (321 measured baseline from the Slice 1 gate, not the design doc's approximate "307" — minus 1 deleted/superseded test, plus 1 CLI-level replacement, plus 1 new `test_main_no_longer_exposes_the_extractor_names` = 322; the 5 concurrency-advisory tests were rewritten in place, net zero count change). Every pre-existing output-text assertion this slice did not deliberately rewrite is unmodified and passing.
- [x] 7.2 `uv run pytest --cov=src/openkos/application tests/unit/application -q` — 96.98% total coverage (`application/ingest.py` itself 96%, up from 93% before 7 triangulation tests added for the judge-notice, in-batch-collision, create-only, and build-failed branches); gate requires 90.0%, reached.
- [x] 7.3 `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy .` — whole-repo gate green (exact counts recorded in apply-progress).
- [x] 7.4 `uv run openkos ingest <file>` with a live extraction against a seeded workspace — output matches the pre-change baseline (recorded in apply-progress).

## Slice 3 — PR 3 (~700–1,000 lines)

### Phase 8: `converged_reingest` — the #773 gate
- [x] 8.1 RED: `test_converged_reingest_falls_through_on_unparseable_frontmatter`, `..._on_legacy_source_no_origin_key`, `..._on_retryable_debt`, and `test_converged_reingest_returns_carried_notices_on_convergence`.
  - RED confirmed: collection failed with `AttributeError: module 'openkos.application.ingest' has no attribute 'SourceDocumentPlan'` (a later test in the same batch) before any Slice 3 symbol existed. Also added `..._on_judge_degrade_notice`, `..._on_re_extract`, and `..._returns_empty_notices_on_a_clean_convergence` (triangulation, 7 tests total for `converged_reingest`).
- [x] 8.2 GREEN: implemented `ConvergedReingest` (frozen dataclass) + `converged_reingest(concept_text, *, re_extract) -> ConvergedReingest | None`. Moved `_extraction_retry_due` → `extraction_retry_due` and `_carried_extraction_notice` → `carried_extraction_notice` from `main.py` into `application/ingest.py` (single definition, underscore dropped to match `stage_derived_objects`'s public-service naming); repointed `_reingest_will_skip`'s call site to `application_ingest.extraction_retry_due` — its docstring's "the shared predicate" wording stays accurate.
- [x] 8.3 GREEN: adapter wiring — `main.py`'s #773 mid-region `return` became a call to `converged_reingest`; a non-`None` result maps to the SAME exit path (verbatim `typer.echo` string + `_SingleIngestOutcome(...)`, proven byte-identical by the `converged_reingest_773` golden below); `None` falls through to the full run.

### Phase 9: `compose_source_document`
- [x] 9.1 RED: `test_compose_source_document_reads_back_on_disk_sensitivity`, `..._on_disk_title`, `..._concept_text_none_means_no_prior_source`. Also added `..._renders_nothing`, `..._binary_source_description` (triangulation), and `..._raises_on_malformed_prior_frontmatter` (triangulation).
  - RED confirmed via collection-time `AttributeError` before `compose_source_document` existed.
- [x] 9.2 GREEN: implemented `SourceDocumentPlan` + `compose_source_document(...)` per design's signature (with two additive fields, `raw_content`/`origin_key` — see Deviations). Adapter passes decoded concept text, not bytes (D2); adapter retains the `_snapshot_read` bytes for `guarded_targets`.

### Phase 10: `compose_catalog_update`
- [x] 10.1 RED: `test_compose_catalog_update_conditional_rerender_on_skip_reason`, `..._on_notices`, `test_compose_catalog_update_derived_plans_index_log_loop_incl_disambiguation_bullet`. Also added `..._healthy_path_reuses_source_content`, `..._regenerate_dedupes_the_source_index_entry`, and `..._skips_the_disambiguation_bullet_when_not_disambiguated` (triangulation, 6 tests total).
  - RED confirmed via collection-time `AttributeError` before `compose_catalog_update`/`CatalogUpdate` existed.
- [x] 10.2 GREEN: implemented `CatalogUpdate` + `compose_catalog_update(...)` per design's signature — owns the conditional re-render and the derived-plans index/log loop including the disambiguation audit bullet.

### Phase 11: Adapter wiring — plan-composition core
- [x] 11.1 GREEN: `_chat_client(cfg, task="extraction")` construction was ALREADY moved up in `_ingest_single` by Slice 2 (deviation #4 in that slice's apply-progress) — confirmed still ahead of every service call after this slice's rewrite; no further move needed. Adapter now calls `compose_source_document`, `converged_reingest`, `stage_derived_objects`, `compose_catalog_update` in the documented order. The write/guard/preview/confirm shell (`guarded_targets`, `_reject_drifted_targets`, `fsio.write_*`, `_autocommit`, `_refresh_derived_after_write`, confirm prompt, preview, exit codes) is unchanged — only its inputs are now computed by the service.
- [x] 11.2 Repointed docstring references to `cli/main._extraction_retry_due`/`_carried_extraction_notice` in `src/openkos/lint.py` (2 sites), `src/openkos/extraction/evidence.py` (1 site), `src/openkos/model/okf.py` (1 site) to `application.ingest.extraction_retry_due`/`carried_extraction_notice`. Verify: `rg -n 'cli\.main\._extraction_retry_due|cli\.main\._carried_extraction_notice'` returns zero matches repo-wide (confirmed). Additionally swept 12 stale prose references to the OLD `_stage_derived_objects`/`_extraction_retry_due`/`_carried_extraction_notice` names in `main.py` (docstrings/comments at the old lines 3211, 4454, 4491, 4501, 4696, 4713, 5035, 5049, 5233, 5255, 5263, 5302) and 2 in `config.py` (1070, 1641) — all repointed to `application_ingest.stage_derived_objects`/`application.ingest.extraction_retry_due`/`carried_extraction_notice`; 2 genuinely historical references describing PRE-move code (`main.py`, narrating "itself moved into `application/ingest.py` in Slice 2" and "mirrors the pre-move evaluation order") were deliberately left unchanged since they accurately describe the past, not the present. Also deleted `main._read_source_sensitivity`/`_read_source_title` (now fully dead code — their only call sites moved into `compose_source_document`).

### Phase 12: Byte-identity proof — remaining scenarios
- [x] 12.1 Extended the Phase 6 characterization goldens with 4 new scenarios covering the plan-composition core: `converged_reingest_773` (the #773 short-circuit), `empty_slug_lost_in_staging` (a drop + `lost_in_staging`), `already_exists_create_only` (a create-only staging drop exercising `compose_source_document`'s on-disk read-back), and `raw_immutability_refusal` (the `(OSError, ValueError)` refusal path, included as a negative control proving the move did not widen the outer `except`). Generated on the pre-Slice-3 tree (commit `e5a7682`, Slice 2 merged) via `git worktree add <path> e5a7682`, reusing the REAL `_init_workspace`/`_patch_llm`/`_FakeLLM`/`_concept_reply`/`runner` fixtures via DIRECT IMPORT from the worktree's `tests.unit.cli.test_ingest` (not hand-rolled) — the v1 attempt hand-rolled a `_FakeLLM` with an `embed()` method and no `LOCAL_BACKEND_LOCALITY`-shaped `locality`, producing two false failures (an `AttributeError: 'str' object has no attribute 'is_local'` crash, then a false mismatch from the missing "embed" advisory lines) before being caught and fixed, mirroring Slice 2's own v1-golden-script mistake. All 6 Slice-2 scenarios remain byte-for-byte identical after this slice's full `_ingest_single` rewrite (proving the rewrite introduced no stray byte); all 10 scenarios verified in BOTH environments (default and `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null`).
- [x] 12.2 Falsification: mutated one character in the relocated `converged_reingest_773` disclosure line (`"unchanged"` → `"unchangedX"`), purged `__pycache__`, ran `uv run pytest tests/unit/cli/test_ingest_characterization.py` — confirmed RED (`test_converged_reingest_matches_pre_move_golden` failed with the expected diff); reverted with the exact inverse replace, purged `__pycache__` again, confirmed GREEN (10/10 passed) and `git diff --stat` showed no residual mutation.

### Phase 13: Slice 3 gate
- [x] 13.1 `uv run pytest tests/unit/cli/test_ingest.py -q` — 322 passed (the design doc's approximate "307" was the pre-Slice-2 baseline; 322 is the measured Slice-2 baseline, unchanged by this slice — 2 direct-call sites for the moved `_carried_extraction_notice` were repointed to `application_ingest.carried_extraction_notice`, no test added/removed/renamed otherwise). Every pre-existing output-text assertion this slice did not deliberately move is unmodified and passing.
- [x] 13.2 `uv run pytest --cov=src/openkos/application tests/unit/application -q` — 78 passed; `application/ingest.py` at 96% (up from 96.98%/93%->96% Slice 2 baseline after adding the plan-composition core plus 12 new triangulation tests), total application coverage 96.84%. Gate requires 90.0%, reached. Two branches remain genuinely unreached: `converged_reingest`'s `except ValueError:` and `_read_source_title`'s `except Exception:` — both dead in practice (see Deviations/Issues Found).
- [x] 13.3 `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy .` — whole-repo gate green: `uv run pytest` 6019 passed, 1 skipped (307.76s); `ruff check .` all checks passed; `ruff format --check .` 291 files already formatted; `mypy .` success, no issues in 291 source files.
- [x] 13.4 Live smoke test against real Ollama (`qwen3:8b` chat, `bge-m3` embedding) in a scratch workspace: fresh `openkos ingest photosynthesis.md --auto` extracted 1 Concept via `compose_source_document`/`compose_catalog_update`; a SECOND identical ingest hit `converged_reingest`'s #773 short-circuit live, printing the exact golden-pinned disclosure line; a THIRD ingest with `--re-extract` re-ran extraction and hit the `already-exists` create-only drop; `openkos ingest <directory>` (the batch path — invoked by passing a directory as `src`, not a `--batch` flag; task 13.4's literal `--batch <dir>` syntax does not exist) ingested a second file successfully, confirming `_ingest_batch`'s body (which reuses `_ingest_single` unchanged) still works end to end.

### Phase 14: Documentation
- [x] 14.1 `docs/adr/0018-application-layer-for-bounded-context-services.md` reads `status: Accepted`, NOT `Proposed` as this task assumed — flipped by the PRIOR, unrelated `docs(cli): archive the query application service change (#936)` commit, because ADR-0018 is a SHARED adr covering both the query and ingest bounded-context slices; the query slice's archive already accepted it. Confirmed this is correct and pre-existing, not something to revert — left untouched.
- [x] 14.2 Left `openspec/changes/ingest-application-service/specs/ingestion/spec.md`'s Purpose delta for archive-time merge — confirmed unedited (still reads "Purpose Update (for archive-time merge into the main spec's `## Purpose`)"); no edit under `openspec/specs/` during apply.
- [x] 14.3 Confirmed #918 is OPEN (`gh issue view 918` → `state: OPEN`); every commit in this slice uses `Refs #918`, never `Closes`/`Fixes`/`Resolves`, including negated forms.

## Notes

- Threat Matrix: N/A per design — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary is introduced or changed.
- `extraction/concept.py` is out of scope and untouched throughout.
