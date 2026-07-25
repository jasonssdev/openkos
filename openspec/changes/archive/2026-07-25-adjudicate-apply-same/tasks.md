# Tasks: adjudicate --apply-same (guarded batch merge, closes #137)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~400-540 (prod ~150-190, tests ~250-350) |
| 400-line budget risk | Medium |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Medium

Note: session review budget is explicitly set to 800 (not the 400 default). Estimated 400-540 lines fits comfortably under the session budget as a single PR; no chaining decision required.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Extract 3 shared helpers, refactor `_run_adjudicate_apply` to use them, keep existing `--apply` suite green | PR 1 (part of single PR) | `uv run pytest tests/unit/cli/test_adjudicate.py -k apply` | `openkos adjudicate --apply` on a fixture workspace | Revert `main.py:516-631` refactor commit; `--apply` behavior unaffected |
| 2 | Add `--apply-same`/`--confirm-count`, batch logic, and full test matrix | PR 1 (same PR) | `uv run pytest tests/unit/cli/test_adjudicate.py -k apply_same` | `openkos adjudicate --apply-same --confirm-count N` on fixture workspace | Drop `--apply-same` branch + option flags; `--apply` untouched |

## Phase 1: Extract Shared Helpers (Refactor Safety Net)

- [x] 1.1 In `src/openkos/cli/main.py`, extract `_prepare_one_merge(root, layout, index_path, log_path, group) -> PreparedMerge | None` from `_run_adjudicate_apply` (lines ~516-631): resolves both ids, returns `None` if already-merged, calls `prepare_merge`.
- [x] 1.2 Extract `_format_merge_preview_line(prepared) -> str` (existing "merge X into Y (...)" line, ~587-592).
- [x] 1.3 Extract `_commit_one_merge(root, layout, index_path, log_path, prepared) -> None` (merge_core + `_autocommit`, ~602-622).
- [x] 1.4 Refactor `_run_adjudicate_apply` to call the three helpers with its existing `[y/N/skip]` prompt between prepare and commit.
- [x] 1.5 Run `uv run pytest tests/unit/cli/test_adjudicate.py -k apply` and confirm all existing `--apply` tests stay green with no behavior change.

## Phase 2: RED Tests — `--apply-same` Batch (Strict TDD)

- [x] 2.1 RED: mutual exclusion `--apply-same --apply` exits 2, `--apply-same --json` exits 2, before workspace/adjudicate calls.
- [x] 2.2 RED: eligibility filter — only SAME 2-member groups previewed; SAME >2-member skipped and reported; DIFFERENT/UNCERTAIN absent from batch.
- [x] 2.3 RED: aggregate preview prints one `_format_merge_preview_line` per eligible pair plus "Total: N" before any prompt or write.
- [x] 2.4 RED: `--confirm-count <exact>` proceeds, applies all, commits count == N.
- [x] 2.5 RED: `--confirm-count` wrong/empty/non-numeric aborts, zero writes, workspace byte-identical.
- [x] 2.6 RED: TTY prompt with exact typed count proceeds after printing full preview; wrong/empty/non-numeric input aborts with zero writes.
- [x] 2.7 RED: non-TTY without `--confirm-count` refuses, exit 1, zero writes.
- [x] 2.8 RED: mid-batch failure (2nd of 3 pairs fails in `merge_core`) stops run, keeps pair-1 commit, never attempts pair 3, reports applied vs not.
- [x] 2.9 RED: chained shared-member pairs — second pair skipped/re-resolved safely, no crash, skip reported in summary (applied < previewed).
- [x] 2.10 RED: reversibility — batch of N applied merges round-trips via N sequential LIFO `unmerge` calls per survivor chain.

## Phase 3: GREEN Implementation

- [x] 3.1 Add `--apply-same` flag and `--confirm-count <str>` option to the `adjudicate` command in `src/openkos/cli/main.py`; wire mutual-exclusion check (exit 2) before workspace gate.
- [x] 3.2 Implement `_run_adjudicate_apply_same(root, layout, index_path, log_path, results, *, confirm_count: str | None) -> None`: Pass 1 filters SAME + `len(member_ids)==2`, builds `PreparedMerge` list via `_prepare_one_merge`, prints preview lines + total count.
- [x] 3.3 Implement confirmation gate resolution order: `--confirm-count` exact match → proceed; TTY → `typer.prompt` full preview then exact match; else refuse exit 1 zero writes.
- [x] 3.4 Implement Pass 2: sequential re-resolution per pair (skip already-merged), `_commit_one_merge` per pair, stop-on-failure keeping prior commits, final applied/skipped summary.
- [x] 3.5 Run all Phase 2 RED tests and confirm GREEN; run full `test_adjudicate.py` suite to confirm no regressions.

## Phase 4: Quality Gate

- [x] 4.1 Update `openspec/specs/entity-resolution-adjudication/spec.md` with the delta requirements (already drafted in spec artifact).
- [x] 4.2 Run `uv run pytest` (full suite) — must be clean.
- [x] 4.3 Run `uv run ruff check . && uv run ruff format --check .` — must be clean.
- [x] 4.4 Run `uv run mypy .` — must be clean.
