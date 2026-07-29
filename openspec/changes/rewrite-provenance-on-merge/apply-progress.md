# Apply Progress: Rewrite inbound provenance on merge (issue #230) — PR1 + PR2 (COMPLETE, 46/46 tasks)

**Branch**: `feat/merge-retargets-provenance` (base `main@3f26c98`, which already carries the merged PR1). Both PRs' tasks are done.

## Mode
Strict TDD (RED before GREEN, verified per phase). Test runner: `uv run pytest`.

## PR1 (Phases 1-4, 22/22) — merged into main as `3f26c98`
`ProvenanceRewrite` / `MERGE_LEDGER_SCHEMA_V3` in `okf.py`, the find/apply/reverse trio in `bundle/provenance.py`, and `MergePlan` / `UnmergePlan.provenance_rewrites` threading in `bundle/merge.py`. All primitives, no CLI wiring.

## PR2 (Phases 5-9, 24/24) — this run

### Phase 5: `prepare_merge` third scanner
- [x] 5.1-5.6: `find_inbound_provenance_rewrites` wired as a THIRD scanner in `prepare_merge`, reusing the SAME `other_files` snapshot the link and relation scanners already built (proved via a plain-function `_counting_rglob` wrapper — not a generator — asserting `rglob(bundle_dir, "*.md")` is called exactly once). `PreparedMerge` gains `provenance_rewrites` / `provenance_rewritten_files`; `touched_files` is now the three-way sorted union. Preview gained a `(retarget provenance to survivor)` bullet per file.

### Phase 6: `merge_core` third transform
- [x] 6.1-6.4: `apply_provenance_rewrites` chained as the third link in `merge_core`'s per-file transform (`apply_link_rewrites` → `apply_relation_rewrites` → `apply_provenance_rewrites`), single atomic write per file. T4 (snapshot byte-identity) verified against the ON-DISK ledger.

### Phase 7: `unmerge` precedence and reversal
- [x] 7.1-7.7: Precedence generalized to three kinds — `provenance_files` first, then `relation_files = relations - provenance_files`, then `link_files = links - provenance_files - relation_files`. `reverse_provenance_rewrites` called with BOTH `link_rewrites` and `relation_rewrites`. T10 (drift refuses, exit 1, no write) and T9 (v1/v2 ledger entries still unmerge exactly) both proved via dedicated tests.

### Phase 8: End-to-end round-trip and functional proof
- [x] 8.1-8.2: `test_merge_then_unmerge_round_trip_covers_all_three_rewrite_kinds` — bundle-wide byte parity (modulo `log.md`) for a file touched by all three kinds and separately for provenance-only / relations-only / links-only files.
- [x] 8.3-8.4: **The functional defect #230 proof** — `test_merge_retarget_then_later_set_sensitivity_raise_reaches_descendant`. A LATER `set-sensitivity <survivor> confidential --auto` raises the descendant's actual on-disk `sensitivity` via `combine_sensitivity`. Zero changes needed to `set_sensitivity_cmd`, confirming the fix is purely that `provenance` now names the survivor.

Also added per the launch prompt: `test_merge_absorbing_non_source_concept_still_retargets_third_party_provenance` — CLI-level proof that a `type: Decision` absorbed concept still gets its inbound provenance retargeted.

### Phase 9: Docs and PR2 checkpoint
- [x] 9.1-9.2: `docs/cli.md` merge section documents the third provenance-retarget pass (type-ungated, same bundle walk), the retarget-then-dedupe collapse rule, and why this closes the `set-sensitivity` propagation gap. The unmerge section documents the three-way precedence rule and the ADR-0011 rollback failure mode: reverted v2 code meeting a v3 ledger entry raises `unsupported merged_from schema version`; recovery is `unmerge` before the revert, or hand-editing `schema` back to v2 and dropping `provenance_rewrites` after it.
- [x] 9.3: Confirmed `docs/adr/0011-provenance-retarget-on-merge.md` stays `status: Proposed` in frontmatter, body, and the index row — untouched by apply, the archive phase's exclusive job.
- [x] 9.4-9.5: All quality gates green.

## Work Unit Evidence
- Focused: `uv run pytest tests/unit/cli/test_merge.py tests/unit/cli/test_merge_core.py tests/unit/cli/test_merge_roundtrip.py tests/unit/cli/test_unmerge.py -q` → 64 passed.
- Full suite: `uv run pytest -q` → 2565 passed.
- `uv run ruff check .` clean; `uv run ruff format --check .` clean; `uv run mypy .` clean (146 source files).
- Coverage: 97.62% total (gate 90%); `cli/main.py` at 96%, with no gaps in the new provenance-wiring regions.
- Runtime harness: every new CLI-level test drives the real Typer app through `CliRunner` against a real temp workspace, not mocks.
- Rollback boundary: revert the two commits on this branch; PR1's primitives on `main` are untouched and inert without this wiring.

## TDD Cycle Evidence
Every RED test was run and observed failing before its GREEN implementation, batched by phase. Phase 5/6: 4 RED failures in `test_merge_core.py` (missing field, wrong `touched_files` union, wrong chain output, wrong byte-identity), then GREEN in `cli/main.py`. Phase 7: 3 RED failures in `test_unmerge.py` (file not restored, precedence not applied, drift not refused), then GREEN in `unmerge()`. Phase 8's round-trip and functional-proof tests passed on first run given Phases 5-7 were complete, matching the design's own framing for those tasks.

## Deviations from Design
None.

## Workload / PR Boundary
- `git diff --numstat main...HEAD`: 7 files, 930 insertions, 38 deletions = 968 total changed lines.
- Code+tests: 904 (`cli/main.py` 82, `test_merge.py` 194, `test_merge_core.py` 255, `test_merge_roundtrip.py` 108, `test_unmerge.py` 265).
- Docs/artifacts: 64.
- Exceeds the design's PR2 forecast (~240-300) and the 800-line review budget, covered by the maintainer-accepted `delivery_strategy: exception-ok`.

## Status
46/46 tasks complete across both PRs. All quality gates green.
