# Apply Progress: surface-skipped-extractions (#187)

## Batch 1 (this run) — PR1: Phases 1-3 (21 tasks)

**Mode**: Strict TDD (RED -> GREEN per task; existing tests run as safety net
before edits; both did NOT change).

**Branch**: `feat/record-extraction-status` (targets `main`, per
`stacked-to-main` chain strategy).

### Completed Tasks
- [x] 1.1-1.4 — `okf.py` vocabulary: `EXTRACTION_STATUS_KEY`,
  `ExtractionStatus` (4-token `Literal`), `EXTRACTION_STATUS_VALUES`,
  `EXTRACTION_STATUS_FAILED`; `get_args` added to the `typing` import;
  `build_source_concept` gained an optional `extraction_status` param,
  emitted only when non-`None`.
- [x] 2.1-2.6 — `_stage_derived_objects` now returns
  `tuple[list[_DerivedPlan], okf.ExtractionStatus | None]`; the single call
  site in `ingest` unpacks the tuple; a local `_build_source_document`
  closure builds the Source once with `None`, and re-renders a second time
  ONLY when `skip_reason is not None` (conditional re-render, per design's
  central decision). The two existing degrade-path tests
  (`test_confidential_default_sensitivity_floor_skips_extraction`,
  `test_spinner_cleared_on_ollama_error_and_degrade_proceeds`) drive the CLI
  and were left completely unmodified — both still pass.
- [x] 3.1-3.11 — all 8 stamping/cross-guard scenarios implemented as tests
  and passing: `no-extractable-text`, `blocked-by-sensitivity`, `failed`
  (no raw exception text leaked), `no-concepts-found`, successful write (no
  key), self-clearing re-ingest (top functional risk), unrecognized-value
  ignored, and the cross-guard test proving `sensitivity` IS read+combined
  from disk while `extraction_status` is NEVER read from disk, in one run.
  Spec `openspec/changes/surface-skipped-extractions/specs/ingestion/spec.md`
  verified to already match implemented behavior — no edit needed.

### Files Changed
| File | Action | What Was Done |
|------|--------|---------------|
| `src/openkos/model/okf.py` | Modified | `EXTRACTION_STATUS_KEY`/`ExtractionStatus`/`EXTRACTION_STATUS_VALUES`/`EXTRACTION_STATUS_FAILED`; `get_args` import; `build_source_concept(extraction_status=...)` |
| `src/openkos/cli/main.py` | Modified | `_stage_derived_objects` -> `(plans, skip_reason)`; `ingest` binds `_build_source_document` closure, conditional re-render |
| `tests/unit/model/test_okf.py` | Modified | 3 new tests: vocabulary constants, omission-by-default, emits-each-value (parametrized x4) |
| `tests/unit/cli/test_ingest.py` | Modified | 6 direct `_stage_derived_objects` tuple-shape tests, 1 build-count spy test, 8 stamping/cross-guard/self-clearing tests |

### TDD Cycle Evidence
| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 1.1-1.2 | `test_okf.py::test_extraction_status_vocabulary_constants` | Unit | 259/259 baseline | Written | Passed | N/A (structural constants) | Clean |
| 1.3-1.4 | `test_okf.py::test_build_source_concept_emits_each_extraction_status` | Unit | 259/259 | Written | Passed | 4 parametrized values | Clean |
| 2.1-2.3 | `test_ingest.py::test_stage_derived_objects_returns_*_reason` (5 tests) | Unit | 259/259 | Written | Passed | 4 skip-reason cases + 1 success case | Clean |
| 2.4-2.6 | `test_ingest.py::test_healthy_ingest_builds_the_source_document_exactly_once` | Unit (CLI) | 259/259 | Written | Passed | N/A (single invariant) | Clean |
| 3.1-3.9 | `test_ingest.py` (8 stamping/cross-guard tests) | Unit (CLI) | 259/259 | Written | Passed | 4 degrade paths + success + clearing + unrecognized + cross-guard | Clean |

### Test Summary
- **Total tests written**: 17 (3 in `test_okf.py`, 14 in `test_ingest.py`)
- **Total tests passing**: 276/276 in PR1 scope (`test_okf.py` + `test_ingest.py`); 2501/2501 full suite
- **Layers used**: Unit (17)
- **Approval tests**: `test_healthy_ingest_builds_the_source_document_exactly_once` (byte-identical healthy-path guard)
- **Pure functions created**: 0 new (extended `build_source_concept`, an existing pure builder)

### Work Unit Evidence
| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/model/test_okf.py tests/unit/cli/test_ingest.py -q` -> `276 passed` |
| Runtime harness command/scenario and exact result | `openkos ingest raw/<sample>` against a stubbed failing LLM backend — exercised via `CliRunner.invoke(app, ["ingest", ...])` end-to-end in `test_failed_extraction_writes_extraction_status` and `test_successful_reingest_clears_a_previous_failed_extraction_status`; both pass |
| Rollback boundary | Revert `src/openkos/model/okf.py`, `src/openkos/cli/main.py`, `tests/unit/model/test_okf.py`, `tests/unit/cli/test_ingest.py` — inert `extraction_status` keys left on disk are §4.1-tolerated, ignored by every reader, and self-delete on the next re-ingest; no migration |

### Deviations from Design
None — implementation matches design exactly: conditional re-render via
`_build_source_document` closure, tuple return shape, closed four-token
vocabulary, write-side typed / read-side fail-silent, no raw exception text
in frontmatter, self-clearing by construction (no clearing code).

### Issues Found
None. Line-count note: this work unit measured ~484 changed lines
(455 insertions + 29 deletions, uncommitted working tree diff), above the
tasks artifact's ~220-280 estimate for PR1 — the codebase's established
convention of long rationale-bearing docstrings/comments (matching every
surrounding function in `okf.py`/`main.py`) accounts for the difference.
Already covered by the pre-resolved `auto-chain`/`stacked-to-main` decision
for this work unit; no new decision needed, but noted for the reviewer.

### Quality Gates (PR1 scope + full repo)
- `uv run pytest -q` -> `2501 passed`
- `uv run pytest --cov=openkos --cov-branch -q` -> `Total coverage: 97.56%` (gate: 90%); `okf.py` 100% branch coverage
- `uv run ruff check .` -> `All checks passed!`
- `uv run ruff format --check .` -> `146 files already formatted`
- `uv run mypy .` -> `Success: no issues found in 146 source files`

### Status (Batch 1)
21/21 tasks complete for PR1 (Phases 1-3). Ready for verify / PR1 delivery.
PR2 (Phases 4-6) starts fresh on a new branch off this one, per
`stacked-to-main`.

## Batch 2 (this run) — PR2: Phases 4-6 (20 tasks)

**Mode**: Strict TDD (RED -> GREEN per task; existing tests run as safety
net before edits; all stayed green throughout).

**Branch**: `feat/surface-unextracted-sources` (branches from PR1's HEAD,
targets PR1's branch `feat/record-extraction-status`, per `stacked-to-main`
chain strategy). PR1's `okf.py` constants and `_stage_derived_objects`
tuple-return were consumed as-is; `okf.py` and the ingest write path were
NOT modified in this batch.

### Completed Tasks
- [x] 4.1-4.9 — `LintDoc` gained `extraction_status: str = ""` and
  `resource: str = ""`, populated in `collect_docs`'s existing single walk
  (two dict lookups added, no new read). `check_unextracted(docs: list[LintDoc])
  -> list[LintFinding]` implemented with the STRUCTURAL no-`bundle_dir`-param
  guard (pinned by a signature-introspection test); matches ONLY
  `extraction_status == "failed"`; detail names the exact retry command
  from `doc.resource`, falling back to a generic hint when empty.
  `LintReport.unextracted` added. `lint`'s Non-Gating Exit Contract
  confirmed unchanged (exit 0 with findings present).
- [x] 5.1-5.7 — `lint`'s CLI rendering gained an `Unextracted sources:`
  section (after `Dangling references:`), with its own empty-state line.
  `status` folds `unextracted` findings into `needs_attention` at the
  existing call site, reusing the SAME `docs` list already bound for
  dangling-reference findings — proved by a plain-function counting-spy
  test on `lint_check.collect_docs` (`calls == 1`), not a `yield from`
  generator (which would record the call at first `next()` instead of call
  time). `blocked-by-sensitivity` proven absent from both `lint`'s and
  `status`'s output by dedicated tests.
- [x] 6.1-6.4 — `docs/cli.md` updated: `lint` section gained "Dangling
  references" (previously undocumented, added for section-list accuracy)
  and "Unextracted sources" bullets; `status` section documents the
  needs-attention fold-in and the `blocked-by-sensitivity` exclusion. Spec
  deltas (`specs/lint/spec.md`, `specs/status/spec.md`) verified to already
  match implemented behavior — no edit needed (already committed in
  `a381e83`). Full PR2-scope and full-repo gates green (see below).

### Files Changed
| File | Action | What Was Done |
|------|--------|---------------|
| `src/openkos/lint.py` | Modified | `LintDoc.extraction_status`/`.resource` fields; populated in `collect_docs`; `LintReport.unextracted`; new `check_unextracted(docs)` function |
| `src/openkos/cli/main.py` | Modified | `lint` command renders `Unextracted sources:` section; `status` folds `check_unextracted` findings into `needs_attention` (same `docs` list, no new walk); both docstrings updated |
| `tests/unit/test_lint.py` | Modified | `_write_doc`/`_doc` helpers gained `extraction_status`/`resource` params; 2 `collect_docs` field tests; 6 `check_unextracted` tests (flags-failed, ignores-non-failed x5 parametrized, names-retry-command, falls-back-when-missing, signature-guard) |
| `tests/unit/cli/test_lint.py` | Modified | 3 tests: empty-state render, flags-a-failed-extraction (retry command in stdout), ignores-blocked-by-sensitivity |
| `tests/unit/cli/test_status.py` | Modified | 3 tests: lists-unextracted-under-needs-attention, blocked-by-sensitivity-never-in-retry-prompt, unextracted-reuses-the-single-collect_docs-call (counting-spy, no-fifth-walk proof) |
| `docs/cli.md` | Modified | `lint`/`status` sections document the new finding, retry-command format, and the `blocked-by-sensitivity` exclusion |

### TDD Cycle Evidence
| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 4.1-4.2 | `test_lint.py::test_collect_docs_reads_extraction_status_and_resource`, `..._defaults_both_fields_when_absent` | Unit | 163/163 baseline | Written | Passed | 2 cases (present/absent) | Clean |
| 4.3-4.7 | `test_lint.py::test_check_unextracted_*` (6 tests) | Unit | 163/163 | Written | Passed | 5 non-failed values parametrized + signature guard | Clean |
| 4.8-4.9 | `test_lint.py::cli::test_lint_flags_a_failed_extraction`, `test_lint_renders_empty_unextracted_section` | Unit (CLI) | 18/18 (test_lint.py CLI) | Written | Passed | empty-state + finding-present, both exit 0 | Clean |
| 5.1-5.2 | `test_lint.py::cli` (same as above) | Unit (CLI) | 18/18 | Written | Passed | N/A | Clean |
| 5.3-5.7 | `test_status.py::test_status_lists_unextracted_under_needs_attention`, `..._blocked_by_sensitivity_never_in_retry_prompt`, `..._unextracted_reuses_the_single_collect_docs_call` | Unit (CLI) | 28/28 (test_status.py) | Written | Passed | failed / blocked-by-sensitivity / walk-count | Clean |

### Test Summary
- **Total tests written**: 14 (8 in `test_lint.py`, 3 in `tests/unit/cli/test_lint.py`, 3 in `tests/unit/cli/test_status.py`)
- **Total tests passing**: 180/180 in PR2 scope (`test_lint.py` + `tests/unit/cli/test_lint.py` + `tests/unit/cli/test_status.py`); 2518/2518 full suite
- **Layers used**: Unit (14)
- **Approval tests**: none new (structural signature-introspection test used instead for the no-fifth-walk guard)
- **Pure functions created**: 1 (`check_unextracted`)

### Work Unit Evidence
| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/cli/test_lint.py tests/unit/cli/test_status.py tests/unit/test_lint.py -q` -> `180 passed` |
| Runtime harness command/scenario and exact result | `openkos lint <bundle>` / `openkos status <bundle>` against a fixture bundle with a `failed` Source — exercised via `CliRunner.invoke(app, ["lint"])`/`invoke(app, ["status"])` end-to-end in `test_lint_flags_a_failed_extraction` and `test_status_lists_unextracted_under_needs_attention`; both pass |
| Rollback boundary | Revert `src/openkos/lint.py`, `src/openkos/cli/main.py`, `tests/unit/test_lint.py`, `tests/unit/cli/test_lint.py`, `tests/unit/cli/test_status.py`, `docs/cli.md` — pure, read-only, no data changes; PR1's `okf.py`/ingest write path is untouched and unaffected by this revert |

### Deviations from Design
None — implementation matches design exactly: `LintDoc` two-field addition
inside the existing walk, `check_unextracted(docs)` with no `bundle_dir`
parameter (structural guard), write-side-typed/read-side-fail-silent
matching only `EXTRACTION_STATUS_FAILED`, `status` reusing the same
in-memory `docs` list via a plain-function counting spy (not a generator).
One additive doc improvement beyond the literal task text: `docs/cli.md`'s
`lint` section previously never documented the pre-existing "Dangling
references" bullet at all — added it alongside "Unextracted sources" so
the section list is complete and accurate; this is documentation-only, no
behavior change.

### Issues Found
None.

### Quality Gates (PR2 scope + full repo)
- `uv run pytest -q` -> `2518 passed`
- `uv run pytest --cov=openkos --cov-branch -q` -> `Total coverage: 97.58%` (gate: 90%)
- `uv run ruff check .` -> `All checks passed!`
- `uv run ruff format --check .` -> `146 files already formatted`
- `uv run mypy .` -> `Success: no issues found in 146 source files`

### Status (cumulative)
41/41 tasks complete across PR1 (Phases 1-3, 21 tasks) and PR2 (Phases 4-6,
20 tasks). Ready for verify / PR2 delivery, stacked onto
`feat/record-extraction-status`.
