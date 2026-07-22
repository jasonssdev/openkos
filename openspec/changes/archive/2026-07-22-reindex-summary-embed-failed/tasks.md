# Tasks: Surface embed-failed in the reindex stdout summary

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~40-60 (1 production f-string edit, ~10 spec lines, ~20-25 test-assertion lines) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | auto-forecast |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Add `embed-failed` to the reindex stdout summary (RED→GREEN, spec widened) | PR 1 (single, no chaining) | `uv run pytest tests/unit/cli/test_reindex_cmd.py` | N/A — display-only stdout string, no external process/service to exercise beyond the existing typer `CliRunner` | Revert the one `typer.echo` f-string line + the spec requirement/scenario text; no data/schema/migration impact |

## Phase 1: RED — Failing Tests First

- [x] 1.1 In `tests/unit/cli/test_reindex_cmd.py::test_reindex_successful_run_prints_summary_and_exits_zero` (~65-82), add `assert "0 embed-failed" in result.stdout`.
- [x] 1.2 In `tests/unit/cli/test_reindex_cmd.py::test_reindex_summary_and_prune_skipped_notice_still_surface_when_graph_write_fails` (~804-839), add `assert "0 embed-failed" in result.stdout` alongside the existing embedded/cache-hit/pruned/skipped assertions.
- [x] 1.3 In `tests/unit/cli/test_reindex_cmd.py::test_reindex_embed_failed_prints_actionable_rerun_notice` (~227-247, `embed_failed=1`), add `assert "1 embed-failed" in result.stdout` — kept distinct from the existing `"incomplete" in result.stderr.lower()` assertion so the stdout tally and stderr call-to-action stay separately verified.
- [x] 1.4 Run `uv run pytest tests/unit/cli/test_reindex_cmd.py` and confirm the three new assertions fail (RED) against the unmodified `main.py`.

## Phase 2: GREEN — Minimal Implementation

- [x] 2.1 Edit the single `typer.echo` in `src/openkos/cli/main.py:2590-2594`: append `, {report.embed_failed} embed-failed` before the terminal period (hyphenated like `cache-hit`, unpluralized like `pruned`/`skipped`, always shown).
- [x] 2.2 Run `uv run pytest tests/unit/cli/test_reindex_cmd.py` and confirm all three sites now pass (GREEN).

## Phase 3: Spec Lockstep

- [x] 3.1 Widen `openspec/specs/reindex-command/spec.md` (~60-75, "CLI Verb Is Thin Wiring" requirement prose) to list `embed-failed` among the printed summary counts.
- [x] 3.2 Widen the same file's "Successful run prints a summary and exits 0" scenario (~71-75) so the THEN clause names `embed-failed`.
- [x] 3.3 Confirm the change's delta spec (`openspec/changes/reindex-summary-embed-failed/specs/reindex-command/spec.md`, already drafted at sdd-spec) matches this wording — no drift between delta and master text.

## Phase 4: Verify Gate (REFACTOR / Cleanup)

- [x] 4.1 Run `uv run pytest` (full suite) — clean.
- [x] 4.2 Run `uv run ruff check .` — clean.
- [x] 4.3 Run `uv run ruff format .` (apply formatting), then `uv run ruff format --check .` to confirm no drift before commit — a prior change missed this and needed a follow-up commit.
- [x] 4.4 Run `uv run mypy .` — clean.
- [x] 4.5 Diff-review: confirm the stderr re-run notice (`main.py:2642-2649`) and the success gate (`main.py:2619`) are untouched — only the stdout tally line changed.
