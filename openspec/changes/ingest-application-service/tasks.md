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
- [ ] 3.1 RED: `test_ingest.py` (application) asserts `DropKind` literal values and `StagingDrop`/`StagedDerivedObjects` field shapes — symbols absent.
- [ ] 3.2 GREEN: define `DropKind`, `StagingDrop`, `StagedDerivedObjects` dataclasses in `application/ingest.py` per design's Interfaces/Contracts.
- [ ] 3.3 RED: `test_stage_derived_objects_returns_typed_outcome_and_renders_nothing` — inspects the module for `typer`/`rich`/`observability` calls and asserts none; `OllamaError` from `llm.chat` propagates unwrapped, not caught.
- [ ] 3.4 GREEN: move `stage_derived_objects` (548 lines) from `main.py` into `application/ingest.py`. Strip all 24 `typer.echo` calls and the `Console(...).status`/`phase_callback` construction. Accept `on_progress: ProgressHook | None = None`, forwarded unchanged to the selected extractor. Catch nothing from `llm.chat`; only the three degrade reasons (`no-extractable-text`, `blocked-by-sensitivity`, `no-concepts-found`) are the service's own. The 15 `_*_notice(report)` helpers stay in `main.py`, moved verbatim to the call site — not into the service.

### Phase 4: Adapter wiring — render disclosure data
- [ ] 4.1 GREEN: replace `main.py`'s call to the deleted `_stage_derived_objects` with a call to `ingest_service.stage_derived_objects`; adapter builds `Console(stderr=True).status(...)` + `observability.phase_callback` and passes the result as `on_progress`; adapter's own `except OllamaError` (verbatim advisory block, including the #746 concurrency arm) supplies `skip_reason="failed"`; adapter renders every notice/drop via the relocated `_*_notice(report)` helpers, in the original render order.
- [ ] 4.2 GREEN: confirm `extract_concept`/`extract_concept_union` imports in `main.py` are now unused (the extractor-selection line moved with the service); `uv run ruff check .` (F401) forces their deletion.

### Phase 5: Test migration — call sites + monkeypatch
- [ ] 5.1 Update `_stage_kwargs` (`tests/unit/cli/test_ingest.py:1139`) — the single edit point — to build kwargs for the new `ingest_service.stage_derived_objects` signature and unpack the returned `StagedDerivedObjects`.
- [ ] 5.2 Repoint all 20 `main._stage_derived_objects(**_stage_kwargs(...))` call sites (verified: `rg -n 'main\._stage_derived_objects\(' tests/`) to call `ingest_service.stage_derived_objects`. Re-run the same `rg` command and confirm 0 remaining matches against `main._stage_derived_objects`.
- [ ] 5.3 Update the 6 `plans == []` sites (verified: `rg -n 'plans == ' tests/unit/cli/test_ingest.py`) to `outcome.plans == ()` — `StagedDerivedObjects.plans` is a tuple.
- [ ] 5.4 Repoint the computed-name monkeypatch at `tests/unit/cli/test_ingest.py:8862-8863` (`target = "extract_concept_union" if union_judge else "extract_concept"`, `monkeypatch.setattr(main, target, ...)`). Before fixing, confirm leaving it unrepointed raises `AttributeError` under pytest's default `raising=True` (proves the design's mechanical guarantee), then retarget it to `openkos.application.ingest`.
- [ ] 5.5 Add `test_main_no_longer_exposes_the_extractor_names`: assert `not hasattr(main, "extract_concept")` and `not hasattr(main, "extract_concept_union")`.

### Phase 6: Byte-identity proof (characterization goldens)
- [ ] 6.1 Before Phase 4/5 land, on the pre-move tree, generate full-stream goldens: complete `stdout`, `stderr`, exit code for the scenario matrix — healthy multi-object; `no-extractable-text`; `blocked-by-sensitivity`; `OllamaError` degrade (with and without the #746 concurrency advisory: `concurrent_extraction` on + timeout + `fans_out`); `no-concepts-found`; each of the five staging drops; the `lost_in_staging` summary; the #773 convergence skip; the `(OSError, ValueError)` refusal.
- [ ] 6.2 Add a characterization test comparing live CLI `stdout`+`stderr`+exit code against the committed goldens byte-for-byte; run it against the post-move tree (after Phase 4/5) to prove no regression.
- [ ] 6.3 Falsification: mutate exactly one character in one relocated echo string, purge `__pycache__` (a same-size mutation otherwise runs stale bytecode), run `uv run pytest`, confirm RED, revert with the exact inverse replace — never `git checkout --`. Repeat for one drop message and one notice line (3 mutate/revert cycles total).
- [ ] 6.4 Filesystem identity: confirm `snapshot_bytes`/`snapshot_with_mtime` (`tests/unit/cli/conftest.py`) still pin that the refusal scenario wrote nothing.

### Phase 7: Slice 2 gate
- [ ] 7.1 `uv run pytest tests/unit/cli/test_ingest.py -q` — all 307 tests pass, ~290 output-text assertions unmodified.
- [ ] 7.2 `uv run pytest --cov=src/openkos/application tests/unit/application -q` — branch coverage above the 90% gate on the service's arms.
- [ ] 7.3 `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy .` — whole-repo gate green.
- [ ] 7.4 `uv run openkos ingest <file>` with a live extraction against a seeded workspace — output matches the pre-change baseline.

## Slice 3 — PR 3 (~700–1,000 lines)

### Phase 8: `converged_reingest` — the #773 gate
- [ ] 8.1 RED: `test_converged_reingest_falls_through_on_unparseable_frontmatter`, `..._on_legacy_source_no_origin_key`, `..._on_retryable_debt`, and `test_converged_reingest_returns_carried_notices_on_convergence`.
- [ ] 8.2 GREEN: implement `ConvergedReingest` (frozen dataclass) + `converged_reingest(concept_text, *, re_extract) -> ConvergedReingest | None`. Move `_extraction_retry_due` and `_carried_extraction_notice` from `main.py` into `application/ingest.py` (single definition); repoint `main.py:5300`'s `_reingest_will_skip` (batch cost-gate predictor, out of scope otherwise) to import the moved predicate — its docstring's "the shared predicate" wording stays accurate.
- [ ] 8.3 GREEN: adapter wiring — `main.py`'s #773 mid-region `return` becomes a call to `converged_reingest`; a non-`None` result maps to the same exit path (verbatim `typer.echo` + `_SingleIngestOutcome(...)`); `None` falls through to the full run.

### Phase 9: `compose_source_document`
- [ ] 9.1 RED: `test_compose_source_document_reads_back_on_disk_sensitivity`, `..._on_disk_title`, `..._concept_text_none_means_no_prior_source`.
- [ ] 9.2 GREEN: implement `SourceDocumentPlan` + `compose_source_document(...)` per design's signature. Adapter passes decoded concept text, not bytes (D2); adapter retains the `_snapshot_read` bytes for `guarded_targets`.

### Phase 10: `compose_catalog_update`
- [ ] 10.1 RED: `test_compose_catalog_update_conditional_rerender_on_skip_reason`, `..._on_notices`, `test_compose_catalog_update_derived_plans_index_log_loop_incl_disambiguation_bullet`.
- [ ] 10.2 GREEN: implement `CatalogUpdate` + `compose_catalog_update(...)` per design's signature — owns the conditional re-render and the derived-plans index/log loop including the disambiguation audit bullet.

### Phase 11: Adapter wiring — plan-composition core
- [ ] 11.1 GREEN: `_chat_client(cfg, task="extraction")` construction moves up in `_ingest_single`, ahead of the service calls. Adapter calls `compose_source_document`, `converged_reingest`, `stage_derived_objects`, `compose_catalog_update` in the documented order. The write/guard/preview/confirm shell (`guarded_targets`, `_reject_drifted_targets`, `fsio.write_*`, `_autocommit`, `_refresh_derived_after_write`, confirm prompt, preview, exit codes) is unchanged — only its inputs are now computed by the service.
- [ ] 11.2 Repoint docstring references to `cli/main._extraction_retry_due`/`_carried_extraction_notice` in `src/openkos/lint.py`, `src/openkos/extraction/evidence.py`, `src/openkos/model/okf.py` to the new `application.ingest` location. Verify: `rg -n 'cli\.main\._extraction_retry_due|cli\.main\._carried_extraction_notice'` returns zero matches repo-wide.

### Phase 12: Byte-identity proof — remaining scenarios
- [ ] 12.1 Extend the Phase 6 characterization goldens to cover the plan-composition core's typed paths; confirm byte-for-byte match against the S2 baseline for shared scenarios.
- [ ] 12.2 Falsification repeat: mutate one character in one S3-relocated string (e.g., the #773 disclosure line), purge `__pycache__`, confirm RED, revert with the inverse replace.

### Phase 13: Slice 3 gate
- [ ] 13.1 `uv run pytest tests/unit/cli/test_ingest.py -q` — all 307 tests pass, output-text assertions unmodified.
- [ ] 13.2 `uv run pytest --cov=src/openkos/application tests/unit/application -q` — branch coverage above 90%.
- [ ] 13.3 `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy .` — whole-repo gate green.
- [ ] 13.4 `uv run openkos ingest <file>` and `uv run openkos ingest --batch <dir>` against a seeded workspace — end-to-end smoke, byte-identical vs baseline; confirm `_ingest_batch`'s body is unchanged.

### Phase 14: Documentation
- [ ] 14.1 Confirm `docs/adr/0018-application-layer-for-bounded-context-services.md` still reads `status: Proposed` (flips to Accepted only at archive).
- [ ] 14.2 Leave `openspec/changes/ingest-application-service/specs/ingestion/spec.md`'s Purpose delta for archive-time merge — no edit under `openspec/specs/` during apply.
- [ ] 14.3 Confirm #918 stays OPEN at archive (also covers the lifecycle context); every commit used `Refs #918`, never `Closes`.

## Notes

- Threat Matrix: N/A per design — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary is introduced or changed.
- `extraction/concept.py` is out of scope and untouched throughout.
