# Tasks: merge-core Extraction (Slice 2b-i, #137)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~350-450 (net-neutral move of ~150 lines out of `merge`, into 2 new functions + dataclass, plus ~150-200 lines of new direct tests) |
| 400-line budget risk | Medium |
| Chained PRs recommended | No |
| Suggested split | Single PR (extraction is one atomic, behavior-preserving unit — splitting prepare_merge/merge_core across PRs would leave an uncompilable intermediate state) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Medium

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | New `test_merge_core.py` (RED) + `PreparedMerge`/`prepare_merge`/`merge_core` (GREEN) + `merge` command refactor | PR 1 | `uv run pytest tests/unit/cli/test_merge_core.py tests/unit/cli/test_merge.py tests/unit/cli/test_merge_roundtrip.py -q` | N/A — no CLI-visible behavior change; existing `merge` scenarios in test_merge.py are the runtime harness | Revert single commit; no schema/ledger/on-disk change (per design Migration/Rollout) |

## Phase 1: New Direct Tests (RED)

- [x] 1.1 Create `tests/unit/cli/test_merge_core.py`: test `prepare_merge(...)` called directly (no CliRunner) returns a `PreparedMerge` with expected `plan`, `sensitivity_before/after`, `touched_files`, `review`; asserts it writes nothing to disk.
- [x] 1.2 Add test: `prepare_merge` raises `OSError`/`ValueError` on bad input (missing survivor/absorbed file, unreadable config).
- [x] 1.3 Add test: `merge_core(bundle_dir, index_path, log_path, prepared)` writes index/log/touched files/survivor last/removes absorbed, and produces ledger bytes identical to current `main.py:2559-2596` output; injected `now` reflected in ledger `merged_at` and log entry.
- [x] 1.4 Add test: `merge_core` performs zero VCS side effects (no git commit) and is unmerge-reversible (roundtrip parity, mirroring `test_merge_roundtrip.py`).
- [x] 1.5 Run new tests — confirm RED (functions do not exist yet).

## Phase 2: Extraction (GREEN)

- [x] 2.1 In `cli/main.py`, define `@dataclass(frozen=True) PreparedMerge` per design Interfaces/Contracts (plan, new_index_text, new_log_text, link_rewrites, relation_rewrites, other_files, now, review, sensitivity fields, touched_files).
- [x] 2.2 Extract `prepare_merge(...)` from `main.py:2453-2519` (config read, 4-text reads, link/relation finders, `plan_merge`, preview `merge_relations` recompute, `remove_index_entry`, `insert_log_entry`) — non-interactive, raises `OSError`/`ValueError`, no logic change.
- [x] 2.3 Extract `merge_core(bundle_dir, index_path, log_path, prepared) -> MergeResult` from `main.py:2559-2596` (ordered writes: index, log, rewritten_texts, survivor last, remove absorbed) — no autocommit, no logic change.
- [x] 2.4 Refactor `merge` command body (`main.py:2317-2614`): keep gate/resolve/`_resolve_concept_path`/same-id check, call `prepare_merge`, keep preview echoes reading from `PreparedMerge`, keep confirm gate unchanged, call `merge_core`, keep success echo and `_autocommit` call verbatim; wrap prepare/write exceptions with pinned `preparing`/`writing` wording.
- [x] 2.5 Confirm `_apply_link_rewrite_idempotently` (`main.py:2239`) remains importable from `cli.main` unchanged.
- [x] 2.6 Run Phase 1 tests — confirm GREEN.

## Phase 3: Behavior-Preservation Gate

- [x] 3.1 Run `uv run pytest tests/unit/cli/test_merge.py tests/unit/cli/test_merge_roundtrip.py -q` — MUST pass with ZERO edits to either file.
- [x] 3.2 Run full suite: `uv run pytest -q`.

## Phase 4: Quality Gate

- [x] 4.1 `ruff check .`
- [x] 4.2 `ruff format --check .`
- [x] 4.3 `mypy .` (or configured mypy target) — no new type errors from `PreparedMerge`/`MergeResult` dataclasses.
