# Tasks: Harden `openkos init` Workspace Creation

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~250-320 (7 small edits across 5 files + new tests) |
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
| 1 | Land all 7 findings (#2-#8) as one hardening batch | PR 1 | `uv run pytest tests/unit/cli/test_init.py tests/unit/model/test_okf.py` | `uv run openkos init` in a scratch dir (symlinked `raw`, then a fresh dir) | `git revert` the single commit range; no persisted state, no migration |

## Phase 1: Foundation (test infra + shared types)

- [x] 1.1 Harden `_snapshot` in `tests/unit/cli/test_init.py` (line ~29) to record directory entries too, not only `is_file()`, so refusal tests catch stray dir creation (#2).
- [x] 1.2 Add `class RefusalCondition(NamedTuple): marks_workspace: bool; reason: str` to `src/openkos/config.py`; retype `_refusal_conditions` as `Iterator[RefusalCondition]`; run full suite — `is_workspace`/`refusal_reason` tuple-unpacking stays unchanged (#6).
- [x] 1.3 Create `src/openkos/fsio.py` with `write_exclusive(path: Path, content: str) -> None` (`with path.open("x", encoding="utf-8", newline="") as f: f.write(content)`) (#5 helper).

## Phase 2: Symlink refusal (#3) + docstring fix (#7)

- [x] 2.1 RED — add `test_refuses_when_dir_is_a_symlink` to `test_init.py`, parametrized over `raw`/`bundle` x {symlink-to-dir, symlink-to-file, broken symlink}: assert exit 1, stderr names the path as a symlink, snapshot (incl. dirs, #2) unchanged, nothing written through the link or its target. Confirm it fails.
- [x] 2.2 GREEN — in `config.py` `_refusal_conditions` (line ~62), add a `path.is_symlink()` branch for `raw`/`bundle`, ordered before the existing `exists()/is_dir()` checks, yielding `RefusalCondition(False, "'{name}' is a symlink")`. Confirm 2.1 passes.
- [x] 2.3 REFACTOR — same commit: correct the `config.py` docstring (lines 50-56) to give the true rationale for both the plain-file and new symlink non-workspace conditions; drop the false "uncaught `FileExistsError`" claim.

## Phase 3: I/O-vs-conformance split (#4)

- [x] 3.1 RED — in `tests/unit/model/test_okf.py`, add a test asserting `check_conformance` raises `OSError` (not appended as a violation) for a non-reserved `.md` under `bundle_dir` that cannot be read (e.g. `chmod 000`). Confirm it fails.
- [x] 3.2 RED — add a test asserting `UnicodeDecodeError` propagates for a non-reserved `.md` with bytes invalid as utf-8. Confirm it fails.
- [x] 3.3 GREEN — in `src/openkos/model/okf.py` `check_conformance` (lines 48-52), move `path.read_text(encoding="utf-8")` out of the `try`; narrow `try` to wrap only `frontmatter.loads(text)`. Confirm 3.1-3.2 pass and the existing malformed-frontmatter case still reports a rule-1 violation.

## Phase 4: Stray-`bundle/` message (#8)

- [x] 4.1 RED — add `test_refuses_stray_bundle_names_crashed_init_cause` to `test_init.py` using its own tmp_path fixture (distinct from the existing `test_refuses_when_dir_non_empty` parametrized fixture): non-empty `bundle/`, assert stderr identifies it as a likely remnant of an interrupted init with a remediation hint; assert the existing generic non-empty scenario (`raw/` and `bundle/`, "not empty" substring) still holds independently. Confirm the new assertion fails.
- [x] 4.2 GREEN — in `config.py` `_refusal_conditions` (line ~66), special-case `layout.bundle_dir`: when non-empty, yield `RefusalCondition(True, "'{name}/' already exists and is not empty; a previous init may have crashed mid-write — inspect and remove it before retrying")`; leave the `raw/` non-empty message unchanged. Confirm 4.1 passes and no prior test regresses.

## Phase 5: Adopt `write_exclusive` (#5)

- [x] 5.1 Replace the two `open("x", ...)` blocks in `src/openkos/bundle/bundle.py` (lines 26-31) with `fsio.write_exclusive(...)` calls.
- [x] 5.2 Replace the two `open("x", ...)` blocks in `src/openkos/config.py` (`write_agents` line 108, `write_config` line 123) with `fsio.write_exclusive(...)` calls. Run the pinned byte-identity tests — output must be unchanged.

## Phase 6: Verification Gate

- [x] 6.1 `uv run pytest --cov` — full suite green, branch coverage ≥90% (`fail_under 90`).
- [x] 6.2 `uv run ruff check .` and `uv run ruff format --check .` — clean.
- [x] 6.3 `uv run mypy .` — clean (strict mode).
- [x] 6.4 `uv build` + wheel smoke test, matching the repo's existing changes.
