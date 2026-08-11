# Verification Report: durable-derived-state (re-verification)

**Change**: durable-derived-state (issue #550), four chained branches, tip `fix/doctor-flagged-refusals`.
**Mode**: Full artifact set (proposal/specs/design/tasks/apply-progress) — full verification.
**Context**: re-verification after a prior FAIL (1 CRITICAL, 2 WARNING, 1 SUGGESTION); this run re-checks the remediation branch's claims against the working tree.
**Verdict**: **PASS** — the CRITICAL is genuinely closed, both spec reconciliations are honest, nothing else regressed. Archive is unblocked.

## Command Evidence (independently re-run at the tip)

| Command | Result |
|---|---|
| `uv run pytest -q` (unpiped) | 4211 passed, 1 skipped in 126.53s |
| `uv run ruff check .` | All checks passed |
| `uv run mypy .` | Success: no issues found in 192 source files |
| `git status` | clean, nothing to commit |

Test count rose from 4208→4211 (3 new tests), matching the three new tests the remediation commit adds to `tests/unit/cli/test_merge.py`.

## 1. Is the CRITICAL genuinely closed?

**Yes.** `merge()` (`src/openkos/cli/main.py:7151`) now declares a `--force` option (`:7165-7173`) and calls `_reject_flagged_ledger_write(root, layout.bundle_dir, survivor_canonical, force)` at `:7308`, immediately after the pre-existing `_reject_torn_ledger_write` call, in Phase A (before any write, before the confirm prompt at `:7356`).

`_reject_flagged_ledger_write` (`:559-598`) runs `bundle_ledger.scan_nesting_violations(bundle_dir)`, filters violations to the survivor being merged, and — unless `force` is `True` — echoes both remediation paths (the repair verb, and `git reset --hard <first-merge>~1` + `openkos reindex`, the latter gated on `vcs_git.has_reset_point`) plus a non-guaranteed-reversibility statement, then exits 1.

Three new tests bind this, not just decorate it:

- `test_merge_refuses_on_a_doctor_flagged_ledger_no_force` (`tests/unit/cli/test_merge.py:501`) — builds a genuinely flagged ledger (`_write_flagged_ledger` embeds a tampered nested snapshot that `scan_nesting_violations` detects), asserts exit 1, both remediation strings present, and the bundle snapshot unchanged. A guard-removed implementation would let the merge complete and the snapshot-unchanged assertion would fail — this is discriminating, not a tautology.
- `test_merge_force_bypasses_flagged_ledger_refusal` (`:531`) — same flagged fixture, adds `--force`, asserts exit 0 and the absorbed file is gone (merge completed). If `--force` did not bypass the check, exit would be 1 and the file would still exist.
- `test_merge_force_bypasses_refusal_not_confirm_gate` (`:550`) — TTY simulation with `--force` but no `--auto`: `input="n\n"` still exits 1 with an unchanged snapshot, `input="y\n"` completes. This proves `--force` bypasses ONLY the integrity refusal, not the confirm gate — an implementation that let `--force` also skip the prompt would make the first sub-case (decline) succeed instead of refuse, and the test would fail.

The apply-progress evidence table (lines 189-190) records that both gates were mutation-tested: reverting the `scan_nesting_violations` membership check to `if True: return` was caught by the no-force test; reverting `if force: return` to `if False: return` was caught by both force-bypass tests. I did not re-run the mutations myself, but the test bodies read as genuinely assertion-discriminating on inspection above (independent of trusting the mutation-testing claim), and `openkos merge --help` (re-run live) confirms the shipped help text: "Bypass the doctor-flagged ledger-integrity refusal... Independent of --auto -- it never skips the confirmation prompt."

## 2. Are the two spec reconciliations honest?

**Yes, both.**

- `entity-resolution-merge/spec.md`: the "`merge` Refuses On A Doctor-Flagged Ledger, With `--force`" requirement's two scenarios ("refuses by default", "`--force` bypasses the refusal, not the confirm gate") match the shipped behavior exactly — verified against the code above. The "Repair Verb..." requirement was reworded to describe the bundle-wide `>=2` entries gate (not a per-concept Check-B gate) and states this is "deliberately COARSER than Check B" because Check B has "two honest false negatives it cannot see past" (single-entry ledgers, cross-survivor pollution invisible at any index). The scenario "Repair verb refuses the whole run, with no override" explicitly says "no `--force` or equivalent flag bypasses this refusal, even for concepts whose own ledger Check B alone would have cleared" — matching `repair()`'s actual two refusal gates (torn-write, bundle-wide entry count), neither of which has any override.
- `doctor-command/spec.md` gained the "Merge-Ledger Torn-Write Check" requirement (Check A) with its own three scenarios, alongside the pre-existing Check B requirement — this is a strict addition describing already-shipped, already-tested behavior (`doctor` checks 12/13), not a behavior change.

**Repair has no override flag of any kind — confirmed independently.** `repair()`'s signature (`src/openkos/cli/main.py:11685`) takes zero `typer.Option`/`typer.Argument` parameters at all. Both refusal gates (`torn` at `:11726` and `bundle_ledger.bundle_wide_max_entries(bundle_dir) >= 2` at `:11738`) are unconditional — there is no flag anywhere in the function body or its CLI registration that could bypass either. This matches the rule the task said was applied ("implementation is right, spec is stale") rather than the spec being bent to match a shortcut: the shipped bundle-wide gate is coarser (refuses more) than the spec originally implied, not less protective, and the rewritten requirement text says so honestly rather than hiding it.

## 3. Did the remediation break anything it touched?

No regressions found. `merge()`'s confirm gate (`:7356-7365`), `--auto` precedence, and the pre-existing `_reject_torn_ledger_write` call remain textually unchanged and in the same order — `_reject_flagged_ledger_write` was inserted as a new line immediately after, not interleaved with existing logic. Full suite (4211 passed, 1 skipped), ruff, and mypy are all clean at the tip. `git status` is clean.

## 4. Re-adjudication of the two prior WARNINGs and the SUGGESTION

| # | Prior finding | Status now |
|---|---|---|
| WARNING 1 | `privacy-purge/spec.md` Scenario 2 (cross-survivor sidecar rewrite during purge) unimplemented | **Unchanged.** The remediation branch did not touch `privacy-purge/spec.md` or purge code. Scenario 2 remains formally unsatisfied, still independently justified as unreachable via the CLI today (`_resolve_concept_path`'s existence gate). Still a tracked-follow-up-level gap, not a regression. |
| WARNING 2 | `doctor-command/spec.md` under-documented Check A | **Resolved.** The remediation commit added the full "Merge-Ledger Torn-Write Check" requirement with three scenarios, matching the already-implemented/tested Check A behavior. Verified by reading the spec file directly. |
| SUGGESTION | `scan_nesting_violations`'s "skip entries with nothing embedded" rule not documented in `design.md` | **Unchanged.** Grepped `design.md` for "nothing embedded" / "nothing to compare" / related phrasing — still absent. Still a documentation-only suggestion, not a behavior risk. |

## 5. Non-goals still hold

- `bundle/.state/pending/` (an unbuilt "pending work store" path, issue #572) — `find` across the working tree returns nothing under that path; the only `.pending` concept in the codebase is the ledger's own two-phase-write marker, a distinct in-scope mechanism.
- `unmerge --to` (issue #562) — `unmerge()`'s signature (`:7421` onward) has only `survivor_id`, `absorbed_id`, `--auto`; grepped for `"--to"` in `main.py` and found no match anywhere in the file.

## Task Completeness

Tasks R.1–R.5 (the remediation slice) are all marked `[x]` in `tasks.md`, matching the shipped commit: `--force` option + `_reject_flagged_ledger_write` (R.1), orthogonality to the confirm gate (R.2), mutation-testing both gates (R.3), the `entity-resolution-merge/spec.md` correction (R.4), and the `doctor-command/spec.md` correction (R.5). All prior 34 tasks across PR #1/#2/#3 remain `[x]` and unaffected.

## Summary of Issues

**CRITICAL (0)** — the prior CRITICAL is closed.

**WARNING (1)**
- `privacy-purge/spec.md` Scenario 2 (cross-survivor sidecar content rewrite during purge) remains unimplemented — disclosed, independently justified as currently unreachable via the CLI, but the spec scenario is formally unsatisfied. File as a tracked follow-up.

**SUGGESTION (1)**
- `scan_nesting_violations`'s "skip entries with nothing embedded" rule should be added explicitly to `design.md`'s prose (currently only implied by the code), to avoid re-investigating the same question on a future read.

## Verdict

**PASS.** The CRITICAL from the prior review — `merge`'s missing doctor-flagged-ledger refusal — is now implemented with a genuinely discriminating test suite (three tests, each of which would fail under the specific wrong behavior it targets), matching the exact two scenarios the spec text describes. `--force` is confirmed, by direct code reading and by a TTY-decline/accept test pair, to bypass only the integrity refusal and never the confirm gate. Both spec reconciliations reflect real, already-shipped behavior rather than a bent spec, and `repair` retains zero override flags of any kind, per its own function signature. The remaining WARNING and SUGGESTION are unchanged, pre-existing, disclosed, and non-blocking. Archive is unblocked.
