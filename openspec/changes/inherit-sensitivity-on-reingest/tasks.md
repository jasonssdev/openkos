# Tasks: Re-ingest Must Not Lower a Source's Sensitivity

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~370-490 (prod 45-60, tests 230-300, docs 60-80, spec 30-50) |
| 400-line budget risk | Medium (over shared 400 default, under session's 800 budget) |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main (cached, unused — single PR) |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: stacked-to-main
400-line budget risk: Medium

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Resolve + stamp + preview + ADR-0010, single narrow call site | PR 1 | `uv run pytest tests/unit/cli/test_ingest.py -q --cov` | `openkos ingest <path>` regenerate on scratch bundle with a forged `confidential` Source and a `private` config default | `cli/main.py:1115` neighborhood + `:1666-1695,1757-1767` + `test_ingest.py` additions, revertable alone (no callers outside re-ingest) |

## Spec reconciliation note

Design's preview table states the resolved level uniformly across three outcomes (preserved / raised / unchanged), varying only the trailing cause clause. The delta spec's single scenario "Preview reports a preserved or raised level on regenerate" lumps two of those into one and omits the `unchanged` case. **Resolved toward the design**: Phase 0 below splits it into three scenarios matching design's wording; the design's table is authoritative.

## Phase 0: Align delta spec with design (no code)

- [x] 0.1 Edit `openspec/changes/inherit-sensitivity-on-reingest/specs/ingestion/spec.md`: replace the single "Preview reports a preserved or raised level on regenerate" scenario with three — `Preview reports a preserved level`, `Preview reports a raised level`, `Preview reports an unchanged level` — each stating the resolved level and the matching cause clause from design's table.

## Phase 1: Foundation

- [x] 1.1 Add `_read_source_sensitivity(concept_path: Path) -> object` in `src/openkos/cli/main.py` near `_family_owns_source` (`:1115`): read + `okf.load_frontmatter`; on `OSError` or any parse failure (including `yaml.YAMLError`, not `OSError`/`ValueError`) `raise ValueError(...)`; never degrades.
- [x] 1.2 Confirm `okf.combine_sensitivity` and `okf.sensitivity_direction` (`okf.py:233,264`) exist and match design's expected signatures — no changes needed, note for Phase 3/5.

## Phase 2: RED — resolution + stamping (`tests/unit/cli/test_ingest.py`)

- [x] 2.1 `test_reingest_does_not_downgrade_the_source_document` — on-disk `confidential`, config `private` → Source stays `confidential` (ingestion spec: modified requirement)
- [x] 2.2 `test_reingest_stamps_new_derived_objects_with_the_preserved_level` — new-slug derived object stamped `confidential`
- [x] 2.3 `test_reingest_raises_when_workspace_default_exceeds_on_disk` — on-disk `public`, config `private` → Source becomes `private`
- [x] 2.4 `test_reingest_still_refreshes_timestamp_and_description` — only `sensitivity` carries across
- [x] 2.5 `test_reingest_with_equal_values_writes_byte_identical_output` — on-disk == config → write byte-identical to current regenerate behavior for that field
- [x] 2.6 `test_reingest_leaves_existing_derived_objects_byte_untouched` — pre-existing derived file's bytes, including `sensitivity`, unchanged after a re-ingest that raises the Source
- [x] 2.7 `test_reingest_after_forget_uses_the_config_default` — concept absent (`:1791-1793`); config `public`; assert result stays `public`, never raised to `private` by feeding `None` into `combine_sensitivity`
- [x] 2.8 `test_reingest_with_unparseable_source_frontmatter_refuses` — forged bad YAML → exit 1, `refusing to ingest`, on-disk bytes unchanged
- [x] 2.9 `test_reingest_with_unknown_on_disk_sensitivity_fails_closed_to_confidential` — `sensitivity: secret` → resolves `confidential`
- [x] 2.10 `test_reingest_resolved_sensitivity_does_not_leak_into_workspace_floor` — config `public`, disk raised to `confidential`, new-slug reply; assert `blocks_llm_send` still fires on the LITERAL config floor, LLM called, no `"workspace default_sensitivity floor is confidential"` in stderr; complements `test_extract_gate_still_reads_workspace_floor` (`:1899-1938`), which must pass unmodified

## Phase 3: GREEN — resolution + stamping

- [x] 3.1 In `src/openkos/cli/main.py`, between `cfg = config.read_config(root)` (`:1666`) and `okf.build_source_concept(...)` (`:1667`): under `regenerate and concept_path.exists()`, call `_read_source_sensitivity`; `resolved = okf.combine_sensitivity(on_disk, cfg.default_sensitivity)`; else `resolved = cfg.default_sensitivity` (no read, no `None` into `combine_sensitivity`)
- [x] 3.2 Pass `sensitivity=resolved` into `build_source_concept` (`:1673`); leave `workspace_floor=cfg.default_sensitivity` at `:1689` untouched
- [x] 3.3 REFACTOR — confirm `:1683-1684` readback stays byte-unchanged; `ruff check`/`ruff format --check`/`mypy --strict` clean
- [x] 3.4 Run Phase 2 tests 2.1-2.10 GREEN; confirm `test_extract_gate_still_reads_workspace_floor` still passes unmodified

## Phase 4: RED — preview reporting

- [x] 4.1 `test_reingest_preview_reports_preserved_level` — disk above config → preview line states resolved level + "preserved from the existing Source"
- [x] 4.2 `test_reingest_preview_reports_raised_level` — config above disk → "raised by the workspace default"
- [x] 4.3 `test_reingest_preview_reports_unchanged_level` — equal → "unchanged"

## Phase 5: GREEN — preview reporting

- [x] 5.1 At `main.py:1763`, replace the flat `(regenerated)` line with `okf.sensitivity_direction(on_disk, cfg.default_sensitivity)`-selected wording per design's table (`preserved` / `raised` / `unchanged` / post-forget `from the workspace default`)
- [x] 5.2 Run Phase 4 tests 4.1-4.3 GREEN; `ruff`/`mypy` clean

## Phase 6: Spec, ADR, coverage

- [x] 6.1 Apply `openspec/changes/inherit-sensitivity-on-reingest/specs/ingestion/spec.md` (post-0.1 reconciliation) to `openspec/specs/ingestion/spec.md`
- [x] 6.2 Create `docs/adr/0010-reingest-raise-only-sensitivity.md`, Status `Proposed`, per design's ADR content plan; does not supersede ADR-0003/0008/0009
- [x] 6.3 Add one index row to `docs/adr/README.md` after line 47 (ADR-0009 row), Status `Proposed`
- [x] 6.4 Run `uv run pytest tests/unit/cli/test_ingest.py -q --cov`; confirm branch coverage `fail_under = 90` on the new resolve/preview branches
- [x] 6.5 Run full `uv run pytest`; confirm no regression outside `test_ingest.py`

## Rules Carried Forward

- `workspace_floor` stays literally `cfg.default_sensitivity` — never `resolved`.
- Absent concept file → `resolved = cfg.default_sensitivity` directly; never feed `None` into `combine_sensitivity`.
- `_read_source_sensitivity` raises `ValueError` (translated from any parse failure, including `yaml.YAMLError`) — never degrades to the config default.
- Only `sensitivity` carries across the regenerate rebuild; `timestamp`/`description`/`resource`/`provenance`/body refresh as today.
- Existing derived objects: create-only, byte-untouched, out of scope for this change.
