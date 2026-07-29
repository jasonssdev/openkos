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

### Remaining Tasks (PR2, out of scope for this run — separate branch)
- [ ] Phase 4: PR2 — Lint Detection (`lint.py`)
- [ ] Phase 5: PR2 — CLI Wiring (`cli/main.py`, lint/status)
- [ ] Phase 6: PR2 — Docs and Verification

### Status
21/21 tasks complete for PR1 (Phases 1-3). Ready for verify / PR1 delivery.
PR2 (Phases 4-6) starts fresh on a new branch off this one, per
`stacked-to-main`.
