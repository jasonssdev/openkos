# Proposal: Harden `openkos init` Workspace Creation

## Intent

Three bounded 4R review passes over the shipped `add-init-command` (final receipt `allow`) surfaced 8 accepted, non-blocking follow-ups (Engram #815). None block; all are real. Each fix invalidates a content-bound receipt and costs a full 4R sweep, so we batch all remaining work into one hardening change instead of looping ("batch, don't loop"). One finding is already closed by later work and is dropped, not reworked.

## Scope

### In Scope

| # | Finding | Location | Type |
|---|---------|----------|------|
| 2 | `_snapshot` records only files, blind to stray dir creation | `tests/unit/cli/test_init.py` | test-only |
| 3 | `bundle_dir.mkdir(exist_ok=True)` follows a `bundle` symlink, writing outside root; pre-flight passes a symlink-to-dir | `bundle/bundle.py`, `config.py` | behavioral |
| 4 | `check_conformance` broad `except Exception` conflates I/O failure (PermissionError/encoding) with a conformance violation | `model/okf.py` | behavioral |
| 5 | Exclusive-create `open(...,"x")` duplicated at 4 sites | `bundle.py`, `config.py` | refactor |
| 6 | `_refusal_conditions` yields a bare `(bool, str)`; meaning only in docstring | `config.py` | refactor |
| 7 | Rotted docstring: claims `mkdir` "would raise an uncaught `FileExistsError`" — FALSE, `main.py` catches `OSError` | `config.py` | doc-only |
| 8 | Stray non-empty `bundle/` after a crashed init retries with a generic message, no remediation | `config.py` | behavioral |

### Out of Scope (non-goals)

- **Finding 1 (`#`-prefixed dir name → `name: None`): CLOSED.** `write_config` is now a byte copy of the template with no `name` field (D5 revert, commits c2ead2b/a239bff). No directory-derived scalar exists to corrupt. Verified against HEAD; dropped.
- No new features, no model-selection/pull flow, no `git init`, no `refresh-model-guidance` docs, no OKF §9 rule-3 mechanical check.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `workspace-init`: Refusal Idempotency gains a symlinked-target refusal (#3) and a clearer stray-`bundle/` retry message (#8); OKF Conformance distinguishes I/O failure from a conformance violation (#4).

## Approach

- **#3**: Refuse in pre-flight when `raw`/`bundle` is a symlink (extend `_refusal_conditions`); reuse `mkdir` without following symlinks. Behavioral → spec scenario.
- **#4**: Let I/O errors (`OSError`) surface distinctly from conformance results. Behavioral → spec scenario.
- **#8**: Reword the non-empty-`bundle/` reason to hint at a crashed init and manual recovery. Behavioral → spec scenario.
- **#2**: Snapshot directories too, hardening every existing refusal test.
- **#5/#6/#7**: Shared exclusive-create helper; named refusal-condition type; correct the rotted docstring. Pure refactor/doc, no behavior change, no spec.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/config.py` | Modified | #3, #5, #6, #7 |
| `src/openkos/bundle/bundle.py` | Modified | #3, #5 |
| `src/openkos/model/okf.py` | Modified | #4 |
| `tests/unit/cli/test_init.py` | Modified | #2 + new behavioral scenarios |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Refactor (#5/#6) shifts behavior | Low | Pure structural; existing suite guards |
| New symlink/message wording churns tests | Med | Assert on stable substrings |
| Correction round introduces new findings (#815 lesson) | Med | Single batched 4R sweep |

## Rollback Plan

Independent per finding; revert the change commit(s). No migration, no persisted state, no data touched.

## Dependencies

- None beyond current `main` (HEAD a239bff, clean).

## Testing Expectations

Strict TDD is active (`strict_tdd: true`): RED-GREEN-REFACTOR, `uv run pytest`, branch coverage `fail_under: 90`. Behavioral findings (#3, #4, #8) land test-first with new scenarios; #2 strengthens the shared snapshot helper; refactors (#5/#6) and the doc fix (#7) stay behavior-neutral under the existing suite.

## Success Criteria

- [ ] Symlinked `raw`/`bundle` target is refused in pre-flight, nothing written outside root (#3).
- [ ] `check_conformance` reports I/O failure distinctly from a conformance violation (#4).
- [ ] Stray-`bundle/` retry message names the likely crashed-init cause and remediation (#8).
- [ ] `_snapshot` detects stray directory creation (#2).
- [ ] Single exclusive-create helper; named refusal-condition type; corrected docstring (#5/#6/#7).
- [ ] `uv run pytest` green, branch coverage ≥ 90%.
