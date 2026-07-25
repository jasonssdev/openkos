# Tasks: Deterministic Slug-Collision Disambiguation at Ingest (#131)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~200-260 (prod ~55-75, tests ~120-160, spec ~25) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Disambiguation loop + audit log in `_stage_derived_objects` | PR 1 | `uv run pytest tests/unit/cli/test_ingest.py -k slug_collision` | N/A — pure filesystem staging, no external runtime | Revert `main.py` hunk; prior `.exists()` drop and existing `-N` files remain valid |

## Phase 1: Foundation (family scan + provenance predicate)

- [x] 1.1 RED: `tests/unit/cli/test_ingest.py` — add `test_family_regex_excludes_base_word_slug` asserting `^{base}(-\d+)?$` does not match `<base>-word` (false-positive guard).
- [x] 1.2 RED: add `test_family_scan_skips_malformed_frontmatter_member` — a family member with broken frontmatter is skipped, scan does not crash.
- [x] 1.3 GREEN: in `src/openkos/cli/main.py`, add `_collision_family(link_dir, base_slug)` (regex-matched, sorted by `N`) and `_family_owns_source(family, source_slug)` reading `okf.load_frontmatter(text)[0]["provenance"]`, catching parse errors per member (skip, don't raise).

## Phase 2: Disambiguation loop (RED then GREEN)

- [x] 2.1 RED: `test_foreign_collision_writes_slug_2`, `test_third_foreign_source_writes_slug_3`.
- [x] 2.2 RED: `test_reingest_owner_of_base_slug_is_noop` (no new file).
- [x] 2.3 RED: `test_reingest_owner_of_slug_2_does_not_spawn_slug_3` (critical — scans whole family, recognizes own `-N`).
- [x] 2.4 RED: `test_noncolliding_candidate_written_without_suffix` (unchanged path).
- [x] 2.5 GREEN: replace the `derived_path.exists()` drop (`main.py:1024-1033`) with the disambiguation loop: on collision, call `_collision_family`/`_family_owns_source`; same-source → create-only no-op (unchanged); foreign source → first-free `<slug>-N` from batch-local `seen_slugs`-aware scan, single-source `provenance`.

## Phase 3: Audit log (RED then GREEN)

- [x] 3.1 RED: `test_disambiguation_writes_audit_log_entry` — asserts `insert_log_entry` entry contains source slug, extracted title, original slug, chosen slug.
- [x] 3.2 RED: `test_status_surfaces_disambiguation_entry` (mirror existing `status` recent-activity test convention).
- [x] 3.3 GREEN: in Phase B write loop, after a disambiguated plan writes, append one `insert_log_entry` call with the required fields; verify no new persisted ledger file is introduced.

## Phase 4: Regression + resolution integration

- [x] 4.1 RED: `test_byte_identical_reingest_short_circuit_still_holds` (D2 untouched).
- [x] 4.2 RED: `test_disambiguated_pair_forms_candidate_group` in the `duplicates`/`adjudicate` test module — contrasts prior "No candidates found".
- [x] 4.3 GREEN: confirm no changes needed to `find_candidates`/`adjudicate`/`merge` (design-confirmed generic grouping); tests pass unmodified against new fixtures.
- [x] 4.4 Update `openspec/specs/ingestion/spec.md` to match `sdd/ingest-slug-collision/spec` delta (Modified + Added requirements). **MERGED during archive** — per explicit orchestrator process rule, `sdd-apply` must not touch the main `openspec/specs/` tree; the delta spec at `openspec/changes/ingest-slug-collision/specs/ingestion/spec.md` was verified already in sync and has been merged during archive.

## Phase 5: Quality Gate

- [x] 5.1 Run `uv run pytest` — all green, including new and pre-existing ingest/duplicates/status tests. (2141 passed, exit 0)
- [x] 5.2 Run `uv run ruff check . && uv run ruff format --check .` — clean. (All checks passed! 134 files already formatted)
- [x] 5.3 Run `uv run mypy .` — clean. (Success: no issues found in 134 source files)
