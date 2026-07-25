# Apply Progress: adjudicate --apply (interactive merge path, #137 Slice 2b-ii)

**Status**: 28/28 tasks complete. All 6 phases done. Ready for verify.

## TDD Cycle Evidence (Strict TDD)

| Task | RED (test written first, observed failing) | GREEN (implementation, observed passing) | REFACTOR |
|------|----------------------------------------------|-------------------------------------------|----------|
| 1.1/1.2 | `test_adjudicate_apply_and_json_rejected_with_exit_code_two` — failed with `No such option: --apply` (unknown flag), then strengthened to assert D1-specific stderr wording, confirmed RED | Added `apply` typer.Option + D1 mutual-exclusion check before workspace gate; test green | None needed |
| 1.3 | Full pre-existing `test_adjudicate.py` (45 tests) run as regression baseline before any further change — green | N/A (no new code) | N/A |
| 2.1 | `test_adjudicate_apply_offers_a_same_two_member_group` — failed (no prompt text emitted pre-impl) | Eligibility filter + prompt wired; green | None |
| 2.2 | `test_adjudicate_apply_never_prompts_different_or_uncertain_groups` — trivially true pre-impl (negative invariant: no prompting code existed yet); kept as a regression guard post-impl | Verified still green after full apply-walk implementation | None |
| 2.3 | `test_adjudicate_apply_same_group_with_three_members_is_skipped_not_prompted` — failed (no "skipped (N>2..." message) | N>2 branch implemented; green | None |
| 2.5 | `test_adjudicate_apply_overlapping_groups_second_reports_already_merged` — failed (no stale-id message) | D4 stale-id guard via `_resolve_concept_path` try/except; green | None |
| 3.1 | `test_adjudicate_apply_preview_precedes_the_exact_prompt_text` — failed (no preview/prompt at all) | D5 preview line + D6 prompt; green | None |
| 3.3 | `test_adjudicate_apply_accepts_merge_updates_filesystem_and_ledger` — failed (no apply path) | D7 apply path (`prepare_merge` → `merge_core` → `_autocommit`); green | None |
| 3.4 | `test_adjudicate_apply_declining_inputs_do_not_merge_and_continue` (parametrized `\n`/`n\n`/`skip\n`) — failed | Strict allowlist `{"y","yes"}` parse; green | None |
| 3.6 | `test_adjudicate_apply_two_accepted_merges_produce_two_separate_commits` — failed (0 new commits; first attempt also surfaced a real bug: absorbed files must be pre-tracked before their deletion can be staged, matching `merge`'s own real-world precondition — fixed via `_seed_commit` test helper, not production code) | Per-merge `_autocommit` call; green (2 separate commits confirmed via `git log --format=%H`) | None |
| 3.7 | `test_adjudicate_apply_then_unmerge_restores_the_absorbed_member` — failed (no write) | Reuses `merge_core`'s ledger writes verbatim; `unmerge` round-trip green | None |
| 4.1 | `test_adjudicate_apply_mid_run_merge_core_failure_stops_the_run` — failed (no try/except around `merge_core`) | D8 try/except (OSError, ValueError) → stderr + `typer.Exit(code=1)`, loop stops; green | None |
| 4.3 | `test_adjudicate_apply_summary_reflects_applied_and_skipped_counts` — failed (no summary line) | D9 four-counter summary; green | None |
| 4.5 | `test_adjudicate_apply_no_eligible_groups_prints_nothing_to_apply` — failed (no "nothing to apply" message) | Zero-eligible branch prints `nothing to apply -- applied 0, skipped 0 (...)`; green | None |
| 5.1 | `test_adjudicate_apply_same_only_is_a_no_op_composition` — failed (naive stdout comparison broke on differing tmp-path workspace lines; fixed test assertion to strip the `workspace at` line, not production code) | `--same-only` is orthogonal to `--apply`'s own SAME-only eligibility filter — no wiring needed; green | None |

## Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/cli/test_adjudicate.py -q` → **61 passed** |
| Runtime harness command/scenario and exact result | `test_adjudicate_apply_then_unmerge_restores_the_absorbed_member`: real workspace (`tmp_path`, isolated git identity via `isolate_git_identity`), real `prepare_merge`/`merge_core`/`_autocommit`/`unmerge` — no mocks on the write path. Also `test_adjudicate_apply_two_accepted_merges_produce_two_separate_commits` asserts real `git log --format=%H` commit counts. Both pass. |
| Rollback boundary | Revert diff in `src/openkos/cli/main.py` (new `apply` flag + D1 guard + `_run_adjudicate_apply` helper + its call site) and `tests/unit/cli/test_adjudicate.py` (new `--apply` test section). No other command, helper, or file touched. |

## Files Changed

| File | Action | What Was Done |
|------|--------|---------------|
| `src/openkos/cli/main.py` | Modified | Added `apply: bool = typer.Option(False, "--apply", ...)` to `adjudicate`; D1 mutual-exclusion guard (`apply and json_output` → stderr + exit 2) as first statement in body; `index_path`/`log_path` now computed unconditionally in `adjudicate`; new `_run_adjudicate_apply()` helper (D2-D9: eligibility filter, D4 stale-id guard via `_resolve_concept_path`, D5 preview, D6 prompt with strict `{"y","yes"}` allowlist, D7 apply+per-merge `_autocommit`, D8 mid-run failure handling, D9 end summary); `if apply: _run_adjudicate_apply(...); return` wired after the `--json` short-circuit, before the human-render path. `merge`/`unmerge`/`prepare_merge`/`merge_core`/`_autocommit`/`_resolve_concept_path` reused VERBATIM — zero changes to any of them. |
| `tests/unit/cli/test_adjudicate.py` | Modified | New `--apply` test section (18 new tests): D1 rejection, eligibility (SAME/DIFFERENT/UNCERTAIN, N>2), stale-id guard (overlapping groups), preview/prompt ordering, accept/decline paths (parametrized), two-commits, unmerge round-trip, mid-run failure, summary breakdown, zero-eligible state, `--same-only` no-op composition. Added `_init_apply_workspace`/`_commit_count`/`_last_commit_subject`/`_seed_commit`/`_seed_one_same_group` test helpers (mirrors `test_main_autocommit.py` conventions); imports `openkos.vcs.git` and `tests.unit.vcs.conftest.isolate_git_identity`. |

## Deviations From Design

None — implementation matches design exactly, including the `merge_core(bundle_dir, index_path, log_path, prepared)` 4-arg signature the design flagged as a proposal mismatch. One design-consistent choice made during implementation: the D9 "nothing to apply" zero-eligible message and the "applied 0, skipped 0 (...)" breakdown format are BOTH satisfied in the same line (`openkos adjudicate --apply: nothing to apply -- applied 0, skipped 0 (N>2: 0, already-merged: 0, declined: 0)`), since the spec text ("clear nothing to apply message") and the design text (exact `applied 0, skipped 0 (...)` format) both had to hold simultaneously and were not contradictory once combined.

## Issues Found

One test-only issue, no production-code issue: the first draft of the "two accepted merges produce two commits" test wrote concept files directly to disk without committing them first, then hit a real (and correct) git limitation — `git add -- <path>` cannot stage the deletion of a path that was never tracked. This mirrors `merge`'s own real-world precondition (every mutating verb auto-commits its own writes, so a later verb's targets are always already tracked) and was fixed by seeding a commit in the test setup (`_seed_commit`), not by changing `_autocommit`/`commit_paths`.

## Scope Guard Confirmation

- Touched ONLY `src/openkos/cli/main.py` (the `adjudicate` command + its new `_run_adjudicate_apply` helper) and `tests/unit/cli/test_adjudicate.py`.
- `prepare_merge`, `merge_core`, `_autocommit`, `_resolve_concept_path` are reused **verbatim** — zero lines changed in any of them.
- `merge` and `unmerge` command bodies are untouched.
- Only SAME + exactly-2-member groups are ever merged; N>2 groups are always skipped with a message, never merged in batch or via any survivor heuristic beyond `member_ids[0]`/`member_ids[1]` alphabetical-first.
- Non-`--apply` `adjudicate` behavior (plain / `--json` / `--same-only`) is byte-identical — confirmed by the full 45-test pre-existing suite passing unchanged, plus the new `--apply --same-only` no-op composition test.

## Quality Gate (Phase 6) — Verbatim Results

```
$ uv run pytest -q
2023 passed in 112.37s (0:01:52)

$ uv run pytest tests/unit/cli/test_adjudicate.py -q
61 passed in 3.03s

$ uv run ruff check src/openkos/cli/main.py tests/unit/cli/test_adjudicate.py
All checks passed!

$ uv run ruff format --check src/openkos/cli/main.py tests/unit/cli/test_adjudicate.py
2 files already formatted

$ uv run mypy .
Success: no issues found in 133 source files
```

## Remaining Tasks

None. 28/28 complete.

## Workload / PR Boundary

- Mode: single PR (delivery strategy: auto-forecast; 400-line budget risk: Medium; no chaining needed)
- Current work unit: Unit 1 — `--apply` flag + full interactive merge walk in `adjudicate`
- Boundary: starts at the `--apply`/D1 guard, ends at the end-of-run summary line; nothing outside `adjudicate`/its test file touched
- Estimated review budget impact: within the forecast Medium-risk band; single focused diff in one command + its test file

## Status

28/28 tasks complete. Ready for verify.
