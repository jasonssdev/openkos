# Verify Report: merge-core Extraction (Slice 2b-i, #137)

**Change**: merge-core-extraction
**Branch**: feat/merge-core-extraction (uncommitted working tree)
**Mode**: Strict TDD, behavior-preserving refactor
**Contract**: No delta spec — existing `tests/unit/cli/test_merge.py` +
`tests/unit/cli/test_merge_roundtrip.py` ARE the contract (zero-edit gate).

## Verdict: PASS

## 1. Quality Gate (independently executed)

| Command | Result |
|---|---|
| `uv run pytest -q` | 2007 passed in 102.31s |
| `uv run ruff check .` | All checks passed! |
| `uv run ruff format --check .` | 133 files already formatted |
| `uv run mypy .` | Success: no issues found in 133 source files |

## 2. Acceptance Bar — Behavior Preservation (PRIMARY GATE)

- `git merge-base HEAD origin/main` → `942ed43a0b4b19d453f82005645d26e06e74cbc9`
- `git diff --stat origin/main` → only `src/openkos/cli/main.py` (241
  insertions / 113 deletions). No other tracked file touched.
- `git diff origin/main -- tests/unit/cli/test_merge.py tests/unit/cli/test_merge_roundtrip.py`
  → **EMPTY** (confirmed byte-for-byte, zero changes to either file).
- `uv run pytest tests/unit/cli/test_merge.py tests/unit/cli/test_merge_roundtrip.py -q`
  → **28 passed**.

Zero-edit gate: **CONFIRMED PASS**. This is the make-or-break check for a
behavior-preserving refactor and it holds.

## 3. Structural Conformance to Design

| Item | Evidence | Status |
|---|---|---|
| `PreparedMerge` frozen dataclass, module-level | `main.py:2317-2342` | PASS |
| `MergeResult` frozen dataclass, module-level | `main.py:2345-2355` | PASS |
| `prepare_merge(bundle_dir, index_path, log_path, survivor_path, absorbed_path, survivor_canonical, absorbed_canonical, root, *, now) -> PreparedMerge` | `main.py:2358-2469`, matches design interface exactly | PASS |
| `merge_core(bundle_dir, index_path, log_path, prepared) -> MergeResult` | `main.py:2471-2534`, matches design interface exactly | PASS |
| Non-interactive, raises `OSError`/`ValueError` | Confirmed via docstrings + `test_merge_core.py::test_prepare_merge_raises_oserror_on_missing_absorbed_file` and `::test_prepare_merge_raises_value_error_when_already_merged` | PASS |
| `merge` command retains gate → preview → CONFIRM gate → prepare/core → success echo → `_autocommit` shape | `main.py:2642-2742` read in full; sequence unchanged | PASS |
| `_autocommit` stays in the COMMAND only | `grep -n "_autocommit" main.py` shows call sites only in commands (lines 1013, 1495, 2025, 2232, 2732, 3034, 3506); `merge_core`'s body (2471-2534) contains zero calls — only docstring prose and the `committed_paths` field name reference it | PASS |
| Error wording `refusing`/`preparing`/`writing` still emitted by command | `main.py:2651` (refusing), `2687` (preparing), `2723` (writing) — all in `merge()`, none in the extracted core functions | PASS |
| `_apply_link_rewrite_idempotently` remains importable | `main.py:2239` module-level; imported by `test_merge.py:17` | PASS |
| `_resolve_concept_path` remains importable | `main.py:1049` module-level; imported by `test_merge_core.py:18` | PASS |

## 4. Judged Deviation from Design

Design's `PreparedMerge` field list assumed `plan.ledger_entry` exposed
`survivor_id`, but `MergeLedgerEntry` only carries `absorbed_id`. Apply added
two extra frozen fields, `survivor_canonical`/`absorbed_canonical`, to carry
values the command already had in scope.

**Judgment**: Behavior-preserving. No new I/O, no new read, same strings the
command computed via `_resolve_concept_path` before this refactor existed.
Confirmed these fields are pass-through only (used to build `MergeResult`
paths and address `merge_core`'s own bundle writes) — they do not alter
`plan.merged_survivor` or any ledger byte. **WARNING-tier at most, does not
break the zero-edit gate or ledger invariance.**

## 5. Ledger / Roundtrip Invariance

- `test_merge_roundtrip.py` passes unchanged (28/28 combined with
  `test_merge.py`) — strong runtime evidence of ledger byte-identity.
- Spot-checked `merge_core` (main.py:2520): `fsio.write_atomic(survivor_path, prepared.plan.merged_survivor)` —
  writes the plan's merged survivor verbatim, same as former inline code at
  `main.py:2592` pre-refactor (confirmed via diff: this line only changed
  `plan.merged_survivor` → `prepared.plan.merged_survivor`, a pure rename).
- `test_merge_core.py::test_merge_core_writes_index_log_touched_files_survivor_last_and_ledger`
  asserts `merged_from` and the injected `now.isoformat()` appear verbatim in
  the survivor text.

**PASS** — no ledger byte drift.

## 6. Scope Guard

`git diff --stat origin/main`: only `src/openkos/cli/main.py` modified.
`git status --porcelain`: new untracked `tests/unit/cli/test_merge_core.py`
and `openspec/changes/merge-core-extraction/` (SDD artifacts). No change to
`unmerge`, similarity/verdict logic, `pyproject.toml`, or `uv.lock`.

**PASS**.

## 7. New Test Value (`test_merge_core.py`, 5 tests)

Reviewed the full file. Tests call `prepare_merge`/`merge_core` directly (no
`CliRunner`, no confirm gate) and assert genuine outcomes, not tautologies:

1. `prepare_merge` returns expected `PreparedMerge` (plan, sensitivity
   before/after, touched files, `now`, `review`) and writes nothing to disk
   (asserts absorbed file still exists, survivor lacks `merged_from`, index
   still contains the absorbed entry).
2. `prepare_merge` raises `OSError` on a missing absorbed file.
3. `prepare_merge` raises `ValueError` (`"already merged"`) on a second call
   after a completed merge — proves it propagates `plan_merge`'s guard.
4. `merge_core` writes index/log/touched-files/survivor-last, removes
   absorbed, and embeds `merged_from` + injected `now` — matches design's
   documented Phase B output.
5. `merge_core` makes **zero VCS side effect** (asserts `.git/HEAD` bytes
   unchanged and `vcs_git.is_clean()` is False after the call — working tree
   is dirty, no commit made) **and** is unmerge-reversible (drives a real
   `unmerge --auto` through the CLI afterward and asserts full restoration).

All 5 tests exercise real behavior with meaningful assertions.

## 8. Task Completion

`openspec/changes/merge-core-extraction/tasks.md`: 16/16 checkbox items
marked `[x]`. Matches apply-progress self-report (15 numbered tasks + 1
Phase-3 full-suite run, all complete).

## Issues

- **CRITICAL**: None.
- **WARNING**: None (the `survivor_canonical`/`absorbed_canonical` deviation
  from design is judged behavior-preserving, see §4 — not risk-bearing enough
  to warrant a WARNING).
- **SUGGESTION**: None.

## Final Verdict: PASS

The zero-edit gate (the primary acceptance bar for this behavior-preserving
refactor) holds with independently-confirmed empty diffs and 28/28 passing
regression tests. Full suite (2007 tests), ruff, and mypy are all clean.
Structural conformance to design is exact, including the phase-split
rationale (`prepare_merge`/`merge_core`), the `_autocommit`
command-only boundary, and error-wording placement. Scope is contained to
`src/openkos/cli/main.py` plus the new direct-test file. Ready for archive.
