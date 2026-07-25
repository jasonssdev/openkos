# Tasks: adjudicate --apply (interactive merge path, #137 Slice 2b-ii)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~220-320 (production ~90-130 in `adjudicate`; tests ~130-190, many small scenarios) |
| 400-line budget risk | Medium |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | auto-forecast |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Medium

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `--apply` flag + full interactive merge walk in `adjudicate` | PR 1 | `uv run pytest tests/unit/cli/test_adjudicate.py -k apply` | Real workspace fixture: `adjudicate --apply` with CliRunner `input=` over seeded bundle docs, then `unmerge` round-trip | Revert diff in `src/openkos/cli/main.py` (`adjudicate` ~3790) + test file; no other command touched |

## Phase 1: Flag And Guard (Foundation)

- [x] 1.1 RED: test `adjudicate --apply --json` exits 2, stderr rejection message, `adjudicate_candidates` never called (monkeypatch spy) — `tests/unit/cli/test_adjudicate.py`
- [x] 1.2 GREEN: add `apply: bool = typer.Option(False, "--apply", ...)` to `adjudicate` in `src/openkos/cli/main.py` (~3790); add D1 check (`if apply and json_output:` stderr + `typer.Exit(code=2)`) before workspace gate
- [x] 1.3 RED: test plain `adjudicate` / `--json` / `--same-only` behavior unaffected (run full existing `test_adjudicate.py` suite as regression baseline before further changes)

## Phase 2: Eligibility And Stale-Id Guard

- [x] 2.1 RED: test SAME 2-member group is offered (prompted) — fake `adjudicate_candidates` result, `input="n\n"`, assert prompt text shown
- [x] 2.2 RED: test DIFFERENT group never prompted; UNCERTAIN group never prompted
- [x] 2.3 RED: test SAME group with 3 members prints `skipped (N>2, merge manually)`, never prompted
- [x] 2.4 GREEN: implement eligibility filter in apply branch: `verdict == Verdict.SAME and len(member_ids) == 2`; survivor=`member_ids[0]`, absorbed=`member_ids[1]`; N>2 path increments `skipped_n_gt2` and prints message, no prompt
- [x] 2.5 RED: test two overlapping SAME 2-member groups sharing a member — first accepted (`y`), second group prints `skipped (member already merged)`, no crash
- [x] 2.6 GREEN: implement D4 stale-id guard — per group, `_resolve_concept_path(bundle_dir, survivor_id)` / `(..., absorbed_id)` in `try/except ValueError` before `prepare_merge`; on `ValueError` → message + `skipped_already_merged++`, continue

## Phase 3: Preview, Prompt, Apply, Commit

- [x] 3.1 RED: test preview line is printed before the prompt (assert `prepare_merge` preview content precedes exact prompt text `Merge <absorbed> into <survivor>? [y/N/skip]`)
- [x] 3.2 GREEN: implement D5 preview line from `PreparedMerge` (`sensitivity_before/after`, `len(touched_files)`, `removed`) and D6 `typer.prompt` with parse (`{"y","yes"}` → apply; else decline)
- [x] 3.3 RED: test `input="y\n"` applies merge — survivor file updated, absorbed file removed, index/log updated, `merged_from` ledger entry written
- [x] 3.4 RED: test `input="\n"` (empty), `input="n\n"`, `input="skip\n"` — no merge, no write, run continues, `skipped_declined` counted
- [x] 3.5 GREEN: implement D7 apply path — on accept: `now = datetime.now(UTC)`, `prepare_merge(...)`, `merge_core(layout.bundle_dir, index_path, log_path, prepared)`, then `_autocommit(root, paths, message)` per design exact message format; `applied++`
- [x] 3.6 RED: test two accepted merges produce two separate commits (per-merge auto-commit, not one commit for the whole run)
- [x] 3.7 RED: integration test — apply a real merge via `adjudicate --apply`, then run `unmerge` against the survivor, assert absorbed member restored (byte-identical pre-merge state)

## Phase 4: Failure Handling And Summary

- [x] 4.1 RED: test monkeypatched `merge_core` raising `OSError` mid-run — run stops before remaining groups, exit code non-zero, clear stderr message, prior commit(s) remain and stay reversible via `unmerge`
- [x] 4.2 GREEN: implement D8 — wrap `prepare_merge`/`merge_core` call in `try/except (OSError, ValueError)` → stderr message, `raise typer.Exit(code=1)`, loop stops
- [x] 4.3 RED: test end-of-run summary line exact wording `applied X, skipped Y (N>2: a, already-merged: b, declined: c)` for a mixed run (one applied, one N>2, one declined)
- [x] 4.4 GREEN: implement D9 — track `applied`, `skipped_n_gt2`, `skipped_already_merged`, `skipped_declined`; print final summary line per design
- [x] 4.5 RED: test no SAME 2-member groups exist — clear "nothing to apply" message, exit 0, no filesystem writes
- [x] 4.6 GREEN: ensure zero-eligible path prints summary/message correctly and exits 0 without crashing

## Phase 5: Non-Regression And `--same-only` Composition

- [x] 5.1 RED: test `adjudicate --apply --same-only` produces identical eligibility set, prompts, and outcomes as `adjudicate --apply` alone (no-op composition)
- [x] 5.2 Run full pre-existing `test_adjudicate.py` suite (plain / `--json` / `--same-only` paths) — confirm zero regressions

## Phase 6: Quality Gate

- [x] 6.1 `uv run pytest` — full suite green
- [x] 6.2 `ruff check` — clean
- [x] 6.3 `ruff format --check` — clean
- [x] 6.4 `mypy` — clean
