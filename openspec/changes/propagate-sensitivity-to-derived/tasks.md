# Tasks: Propagate Source Sensitivity to Derived Objects

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~740 total (PR 1 ~150, PR 2 ~590) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (ingest inheritance + spec) -> PR 2 (set-time propagation + spec + ADR-0009 + docs, base PR 1) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Real creation-time inheritance: split `sensitivity` param into `workspace_floor`/`stamp_sensitivity`, read Source's own value back | PR 1 | `uv run pytest tests/unit/cli/test_ingest.py -q` | `openkos ingest <path>` on scratch workspace, config `default_sensitivity` != forged Source value | `cli/main.py:1178-1367,1653-1679` + `test_ingest.py` additions, revertable alone (no callers outside ingest) |
| 2 | Set-time raise-only propagation: Source branch, descendant staging/writes, preview/messages/`--help`, ADR-0009 | PR 2 (base PR 1) | `uv run pytest tests/unit/cli/test_set_sensitivity.py -q` | `openkos set-sensitivity <source-id> confidential --auto` on scratch bundle with derived objects | `cli/main.py:3043-3245` Source branch + `test_set_sensitivity.py` additions, revertable without touching PR 1's parameter split |

## Phase 1: Ingest inheritance (PR 1)

- [x] 1.1 RED — `tests/unit/cli/test_ingest.py`: `test_derived_object_inherits_source_document_value_not_config` — config `default_sensitivity: public`, monkeypatch `main.okf.build_source_concept` to return a doc stamped `confidential`, assert derived object's `sensitivity == "confidential"`; fails today (gets `public`) — proves real inheritance vs. shared-constant coincidence (ingestion spec, Requirement "Derived Object Provenance and Sensitivity Inheritance")
- [x] 1.2 RED — same file: `test_extract_gate_still_reads_workspace_floor` — config floor `confidential`, forged Source `public`; assert extraction is skipped and `llm.chat` never called — pins `sensitivity-aware-llm` Requirement 4 against the parameter split
- [x] 1.3 Retitle existing `test_derived_object_inherits_source_sensitivity` (`test_ingest.py:1824`) as the same-value baseline; keep it, note it alone no longer proves inheritance
- [x] 1.4 GREEN — `src/openkos/cli/main.py:1178-1367`: split `_stage_derived_objects`' `sensitivity` parameter into `workspace_floor` (gates the `extract` fail-closed check at the old `:1262`) and `stamp_sensitivity` (stamps `okf.build_concept` at the old `:1357`); update docstring
- [x] 1.5 GREEN — `src/openkos/cli/main.py:1653-1679`: build `concept_content` first, read `source_sensitivity = okf.load_frontmatter(concept_content)[0]["sensitivity"]`, pass `workspace_floor=cfg.default_sensitivity, stamp_sensitivity=source_sensitivity` into `_stage_derived_objects`
- [x] 1.6 REFACTOR — confirm no remaining call site references the old single `sensitivity` param name; `ruff check`/`ruff format --check`/`mypy --strict` clean
- [x] 1.7 Apply `openspec/changes/propagate-sensitivity-to-derived/specs/ingestion/spec.md` delta to `openspec/specs/ingestion/spec.md`
- [x] 1.8 Run `uv run pytest tests/unit/cli/test_ingest.py -q --cov` and confirm branch coverage on the new `workspace_floor` vs `stamp_sensitivity` split

## Phase 2: Set-time propagation (PR 2, base PR 1)

- [ ] 2.1 RED — `tests/unit/cli/test_set_sensitivity.py`: `test_raising_source_raises_derived_objects` — ingest, then `set-sensitivity <source> confidential --auto`, assert derived file's stored `sensitivity` raised
- [ ] 2.2 RED — same file: `test_lowering_source_never_lowers_derived` — raise both, then lower the Source with `--allow-downgrade`; derived files byte-identical
- [ ] 2.3 RED — same file: `test_non_source_concept_touches_only_itself` — target a derived (non-Source) object; assert byte-identical single-file behavior, no bundle scan
- [ ] 2.4 RED — same file: `test_preview_lists_every_derived_raise` — non-TTY `--auto`, assert one preview line per descendant
- [ ] 2.5 RED — same file: `test_dangling_provenance_warns_and_never_lowers` — hand-write a concept citing a missing source id; assert stderr WARNING naming it, no write, target Source write still succeeds
- [ ] 2.6 RED — same file: `test_descendants_written_before_target_on_failure` — force the target concept write to fail; assert descendants are already raised on disk (fail-closed ordering)
- [ ] 2.7 RED — same file: `test_commit_stages_every_changed_path` — assert `_autocommit` path list includes each descendant path, and an unrelated dirty file stays unstaged
- [ ] 2.8 RED — same file: `test_source_with_zero_derived_objects_unchanged` — Source with no provenance descendants behaves exactly as today (single-file write)
- [ ] 2.9 RED — same file, invert `test_success_message_contains_honesty_line` (`:355`) — on a Source, success message must name the propagated objects; add companion `test_non_source_success_message_keeps_only_this_concept_line` preserving the original single-concept assertion for non-Source targets
- [ ] 2.10 RED — same file, reword `test_help_contains_honesty_line` (`:373`) — `--help` states the bounded new scope (named concept + Source provenance descendants, raise-only)
- [ ] 2.11 GREEN — `src/openkos/bundle/provenance.py`: expose/import `find_provenance_descendants` in `cli/main.py` (no signature change)
- [ ] 2.12 GREEN — `src/openkos/cli/main.py:3043-3245`: add `_DescendantRaise` dataclass; in `set_sensitivity_cmd`, after resolving `concept_id`, branch on `metadata.get("type") == "Source"` — build bundle snapshot (same `rglob` pattern as `forget`), call `find_provenance_descendants`, compute `okf.combine_sensitivity(descendant_current, level)` per descendant, stage only strict raises, warn on unresolvable provenance and exclude
- [ ] 2.13 GREEN — same file: extend preview and success message to list each staged descendant raise
- [ ] 2.14 GREEN — same file: order Phase B writes as descendants first, then target concept, then `log.md`, then one `_autocommit` covering every changed path
- [ ] 2.15 GREEN — same file: extend `--help` text with the new bounded-scope honesty line
- [ ] 2.16 REFACTOR — confirm idempotence short-circuit (`current == level`) still returns early with no descendant work; `ruff check`/`ruff format --check`/`mypy --strict` clean
- [ ] 2.17 Run `uv run pytest tests/unit/cli/test_set_sensitivity.py -q --cov` and confirm branch coverage for: Source branch, empty-descendant branch, dangling-provenance branch
- [ ] 2.18 Apply `openspec/changes/propagate-sensitivity-to-derived/specs/sensitivity-config/spec.md` delta to `openspec/specs/sensitivity-config/spec.md`
- [ ] 2.19 Create `docs/adr/0009-source-sensitivity-propagation.md` per design's ADR-0009 content plan; add index row to `docs/adr/README.md`, status `Proposed`. Do NOT edit ADR-0008.

## Rules Carried Forward (do not re-derive at apply time)

- `_stage_derived_objects` signature: `workspace_floor` gates the `extract` fail-closed check (Req 4, unchanged); `stamp_sensitivity` is the read-back Source value that stamps derived objects.
- Descendant closure: reuse `find_provenance_descendants(files, root_ids={canonical_id})` unchanged; descendant set = returned list minus the root id.
- Combine rule for descendants only: `okf.combine_sensitivity(descendant_current, level)`, stage only when it differs from `descendant_current`. The target concept's own assignment is untouched by this rule and may still lower (ADR-0008 unchanged for the human-assigned target).
- Write order: descendants BEFORE target concept BEFORE `log.md`, one `_autocommit` at the end — fail-closed on partial failure.
- Source detection: `metadata.get("type") == "Source"`, never path-based.
- Idempotence short-circuit (`current == level`) stays first, unchanged, no descendant work.
- ADR-0009 supersedes only ADR-0008's scope sentence; ADR-0008 is not edited.
