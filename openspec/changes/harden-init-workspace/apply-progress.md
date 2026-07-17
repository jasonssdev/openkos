# Apply Progress: Harden `openkos init` Workspace Creation

**Mode**: Strict TDD
**Status**: 17/17 tasks complete. Ready for verify.

## Completed Tasks

- [x] 1.1 Hardened `_snapshot` in `test_init.py` to record every entry (files, dirs, symlinks — not only `is_file()`), via a new `_snapshot_entry` helper that checks `is_symlink()` before `is_dir()`/`read_bytes()`.
- [x] 1.2 Added `RefusalCondition(NamedTuple)` (`marks_workspace`, `reason`) to `config.py`; `_refusal_conditions` retyped `Iterator[RefusalCondition]`; `is_workspace`/`refusal_reason` tuple-unpacking unchanged.
- [x] 1.3 Created `src/openkos/fsio.py::write_exclusive(path, content)` — leaf module, no imports from `config`/`bundle`.
- [x] 2.1 RED — `test_refuses_when_dir_is_a_symlink` (parametrized `raw`/`bundle` x {to_dir, to_file, broken}), skipped on non-POSIX. Confirmed failing for the right reason (production refusal missing) after first fixing a `_snapshot` bug it exposed (broken-symlink `read_bytes()` crash).
- [x] 2.2 GREEN — added `path.is_symlink()` branch in `_refusal_conditions`, ordered before `exists()/is_dir()`, yielding `RefusalCondition(False, "'{name}' is a symlink")`.
- [x] 2.3 REFACTOR — corrected the `_refusal_conditions` docstring: true rationale for both the plain-file and symlink non-workspace conditions (Phase B catches `FileExistsError` as `OSError` but with a generic message; a symlink would let a write escape through/to the link target). Dropped the false "uncaught FileExistsError" claim.
- [x] 3.1 RED — `test_check_conformance_raises_oserror_on_unreadable_file` (chmod 000, skipped non-POSIX/root). Confirmed failing (old code swallowed it as a violation).
- [x] 3.2 RED — `test_check_conformance_raises_unicode_decode_error_on_bad_encoding`. Confirmed failing.
- [x] 3.3 GREEN — moved `path.read_text(...)` out of the `try` in `check_conformance`; `try` now wraps only `frontmatter.loads(text)`. Existing malformed-frontmatter/malformed-YAML cases still report violations.
- [x] 4.1 RED — `test_refuses_stray_bundle_names_crashed_init_cause`, own `tmp_path` fixture distinct from `test_refuses_when_dir_non_empty`. Confirmed failing; confirmed the existing generic test still passes independently.
- [x] 4.2 GREEN — special-cased `layout.bundle_dir` in `_refusal_conditions`: non-empty `bundle/` now yields the crashed-init/remediation message; `raw/`'s message unchanged.
- [x] 5.1 `bundle.py::create` now calls `fsio.write_exclusive` for `index.md`/`log.md`.
- [x] 5.2 `config.py::write_agents`/`write_config` now call `fsio.write_exclusive`. Byte-identity tests unchanged and passing.
- [x] 6.1 `uv run pytest --cov`: 60 passed, 100% branch coverage (>= 90% required).
- [x] 6.2 `uv run ruff check .` and `uv run ruff format --check .`: clean (fixed `PTH115`/`PT011` findings during the pass).
- [x] 6.3 `uv run mypy .`: clean, strict mode, 18 source files.
- [x] 6.4 `uv build` succeeded; wheel installed into a scratch venv; `openkos init` ran successfully against a scratch dir and produced all 5 artifacts.

## TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 1.1 | `tests/unit/cli/test_init.py` | Unit (test infra) | ✅ 24/24 (baseline) | N/A — test-helper hardening, no new assertion | ✅ 15/15 `test_init.py` still pass after change | ➖ N/A (helper, not a behavior) | ➖ None needed |
| 1.2 | `src/openkos/config.py` | Unit | ✅ 51/51 after 1.1 | N/A — behavior-neutral type rename (approval-style) | ✅ 51/51 pass unchanged | ➖ N/A (no new behavior) | ➖ None needed |
| 1.3 | `src/openkos/fsio.py` | Unit | N/A (new file) | Triangulation skipped: purely structural, single straight-line body, no branching; verified indirectly by Phase-5 byte-identity tests once adopted | ✅ (adopted + exercised in Phase 5, 60/60 pass) | ➖ Single | ➖ None needed |
| 2.1 | `tests/unit/cli/test_init.py` | Unit | ✅ 15/15 | ✅ Written (3x2=6 cases) | ✅ `2.2` made all 6 pass | ✅ 6 cases (2 dirs x 3 target kinds) | ✅ extracted `_snapshot_entry` helper |
| 2.2 | `src/openkos/config.py` | Unit | ✅ (2.1 RED baseline) | — | ✅ 60/60 full suite green | ✅ covered by 2.1's 6 cases | ➖ None needed |
| 2.3 | `src/openkos/config.py` (docstring) | N/A | N/A | N/A — doc-only | N/A | N/A | ✅ docstring rewritten, no behavior change, 60/60 still pass |
| 3.1 | `tests/unit/model/test_okf.py` | Unit | ✅ 9/9 | ✅ Written, confirmed `DID NOT RAISE OSError` | ✅ `3.3` made it pass | — | — |
| 3.2 | `tests/unit/model/test_okf.py` | Unit | ✅ (3.1 RED baseline) | ✅ Written, confirmed `DID NOT RAISE UnicodeDecodeError` | ✅ `3.3` made it pass | ✅ 2 distinct failure modes (permission vs. encoding) | — |
| 3.3 | `src/openkos/model/okf.py` | Unit | ✅ (3.1+3.2 RED) | — | ✅ 60/60 full suite green, malformed-frontmatter case still a violation | covered by 3.1+3.2 | ➖ None needed |
| 4.1 | `tests/unit/cli/test_init.py` | Unit | ✅ 22/22 (pre-4.1) | ✅ Written, confirmed message-substring assertion failed while generic test passed independently | ✅ `4.2` made it pass | ➖ Single (one message shape) | — |
| 4.2 | `src/openkos/config.py` | Unit | ✅ (4.1 RED baseline) | — | ✅ 60/60 full suite green | covered by 4.1 | ➖ None needed |
| 5.1 | `src/openkos/bundle/bundle.py` | Unit (approval) | ✅ existing byte-identity tests (`test_bundle.py`) | N/A — refactor, no new behavior | ✅ 60/60 pass, byte-identity preserved | ➖ N/A | ➖ None needed |
| 5.2 | `src/openkos/config.py` | Unit (approval) | ✅ existing byte-identity tests (`test_config.py`) | N/A — refactor, no new behavior | ✅ 60/60 pass, byte-identity preserved | ➖ N/A | ➖ None needed |

### Test Summary
- **Total tests written**: 9 new test functions (2.1 parametrized to 6 cases, 3.1, 3.2, 4.1)
- **Total tests passing**: 60/60 (`uv run pytest`)
- **Layers used**: Unit (60)
- **Approval tests** (refactoring): 2 (Phase 5 byte-identity tests for `bundle.py`/`config.py`, pre-existing, re-verified green)
- **Pure functions created**: 2 (`fsio.write_exclusive`, `_snapshot_entry`)

## Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/cli/test_init.py tests/unit/model/test_okf.py -q` → 30 passed |
| Runtime harness command/scenario and exact result | `uv build` then installed the wheel into a scratch venv and ran `openkos init` in a fresh scratch dir → exit 0, all 5 artifacts (`raw/`, `bundle/index.md`, `bundle/log.md`, `AGENTS.md`, `openkos.yaml`) created |
| Rollback boundary | `git revert` the single commit range covering `src/openkos/{config.py,fsio.py,bundle/bundle.py,model/okf.py}` and `tests/unit/{cli/test_init.py,model/test_okf.py}`; no persisted state, no migration |

## Phase 6 Gate Results (final, on the full tree)

- `uv run pytest --cov`: **60 passed**, branch coverage **100.00%** (fail_under 90) — PASS
- `uv run ruff check .`: **All checks passed** — PASS
- `uv run ruff format --check .`: **18 files already formatted** — PASS
- `uv run mypy .`: **Success: no issues found in 18 source files** — PASS
- `uv build` + wheel smoke: **Successfully built** `openkos-0.1.0.tar.gz`/`.whl`; installed into scratch venv; `openkos init` ran successfully — PASS

## Files Changed

| File | Action | What Was Done |
|------|--------|----------------|
| `src/openkos/fsio.py` | Created | New leaf module, `write_exclusive(path, content)` |
| `src/openkos/config.py` | Modified | `RefusalCondition` NamedTuple; symlink refusal branch; stray-`bundle/` message; docstring fix; adopted `write_exclusive` |
| `src/openkos/bundle/bundle.py` | Modified | Adopted `write_exclusive` for `index.md`/`log.md` |
| `src/openkos/model/okf.py` | Modified | Moved `read_text` out of `try` in `check_conformance`; narrowed `try` to `frontmatter.loads` only |
| `tests/unit/cli/test_init.py` | Modified | `_snapshot`/`_snapshot_entry` hardened for dirs+symlinks; added symlink-refusal and stray-bundle-message RED tests |
| `tests/unit/model/test_okf.py` | Modified | Added OSError/UnicodeDecodeError RED tests for `check_conformance` |

## Deviations from Design

None — implementation matches design.md exactly (symlink pre-flight branch ordered before `exists()/is_dir()`, `check_conformance` boundary narrowed to `frontmatter.loads` only, `RefusalCondition` as `NamedTuple`, `fsio.py` as a dependency-free leaf module, stray-`bundle/` message still `yield True`).

One implementation-only addition beyond the letter of the tasks: `_snapshot` in `test_init.py` needed a symlink-aware branch (`_snapshot_entry`, checking `is_symlink()` before `is_dir()`/`read_bytes()`) to avoid crashing on a broken-symlink scenario introduced by task 2.1's RED test. This is a natural extension of task 1.1's "record directory entries too" intent (design's #2 finding) — not a deviation from the design's contracts.

## Issues Found

None.

## Review Workload / PR Boundary

- Mode: single PR (Review Workload Forecast: Low risk, no chaining)
- Current work unit: Unit 1 — "Land all 7 findings (#2-#8) as one hardening batch"
- Boundary: starts and ends this single apply batch; 17/17 tasks complete
- Actual changed lines: 242 (190 insertions + 33 deletions across 5 modified files, +19 new `fsio.py`) — within the ~250-320 forecast and the 400-line budget

## Status

17/17 tasks complete. Ready for verify. Working tree left uncommitted per instructions.
