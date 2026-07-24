# Archive Report: auto-commit-writes (git-lifecycle Slice 2)

**Date**: 2026-07-24
**Change**: `auto-commit-writes` — git-lifecycle Slice 2
**Status**: ARCHIVED

## Executive Summary

Slice 2 of the git-lifecycle "Auto" arc (#145): every mutating verb now
auto-commits its result after Phase B, so the working tree stays clean and the
user never touches git by hand. Slice 1 (#151) set up git in `init`; this wires
a shared best-effort, non-fatal `_autocommit` helper into `ingest`, `forget`,
`relate`, `merge`, `unmerge`, and `reconcile`, reusing Slice 1's git primitives.

**Merged**: PR #153 (squash).
**Arc**: #145 (decided). Slice 3 (`purge-transactional-cleanup`, #141/#142) deferred.
**All tests pass**: 1931 passed (up from 1876 baseline; +55 new tests).
**Verification verdict**: PASS (0 CRITICAL).

## Change Scope

### Summary
- **New capability**: `workspace-autocommit`.
- **Behavior**: after each of the six mutating verbs' Phase B, on the success path
  (after the `--auto`/`review` confirm gate), openkos makes one scoped commit
  (`git add -- <paths>`, never `-A`) of the paths that verb wrote plus
  `bundle/index.md`/`bundle/log.md`, via `_autocommit(root, paths, message)`.
- **Per-verb commit messages**: `openkos: ingest <source> (+N concepts)`,
  `openkos: forget <id>`, `openkos: relate <src> -> <dst> (<type>)`,
  `openkos: merge <src> into <dst>`, `openkos: unmerge <id>`,
  `openkos: reconcile (<summary>)`.
- **Degradation** (mirrors Slice 1): not a git repo / identity unset / commit error
  → non-fatal stderr WARNING; the verb still exits its normal success code.
- **Confidential transparency** (maintainer decision): all content is committed
  (local git only, never a remote); a one-time stderr NOTICE fires when a commit
  includes `sensitivity: confidential` content. Detection is a plain frontmatter
  equality check (`str(meta.get("sensitivity","")).strip() == "confidential"`,
  the top rank of `okf.SENSITIVITY_ORDER`) — transparency, NOT the fail-closed
  `sensitivity.blocks_llm_send` LLM gate; missing/blank/unparseable values never
  false-trigger.
- **Unconditional** — no opt-out flag (`autocommit: false` deferred as a future
  escape hatch).

### Files Modified
1. `src/openkos/cli/main.py` (+141) — `_autocommit(root, paths, message)` and
   `_commit_has_confidential(root, paths)` helpers (cloned from Slice 1's `init`
   git block); wired into the 6 mutating verbs' success paths. `reindex` left
   deliberately unwired (derived `.openkos/*.db` are gitignored).
2. `tests/unit/cli/test_main_autocommit.py` (new, +775) — 55 tests: helper units +
   parametrized per-verb integration tests + confidential-notice + guard tests.
3. `openspec/specs/workspace-autocommit/spec.md` (new, promoted at archive) — the
   `workspace-autocommit` capability: 6 requirements, 13 Given/When/Then scenarios.

### Test Coverage
- Helper unit tests (real temp git repos; identity isolation via
  `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM`; failures via monkeypatch).
- Per-verb integration tests: commit-after-Phase-B + clean tree, scoped-add
  (unrelated dirty file untouched), declined-confirm → no commit, degradation
  matrix (not-a-repo / identity-unset / commit-error → WARN + verb success).
- Confidential detection: single/multiple confidential → one NOTICE; public/private/
  missing/blank/unparseable → no notice; deleted-file paths handled.
- Guards: `reindex` non-wiring; no `git add -A`; canonical layer imports no `vcs`.
- Full suite: 1931 passed; ruff (check + format) and mypy clean over the whole repo;
  CI coverage gate (`fail_under=90`) green on PR #153.

## Notes / Deviations
- The design **corrected** the confidential-detection API the proposal loosely
  assumed (`sensitivity.blocks_llm_send`, a fail-closed LLM gate → false alarms) to
  the frontmatter equality check. The delta spec prose was aligned to the design
  before merge.
- No test in this change reads any `openspec/changes/` path (the coupling that broke
  Slice 1's archive was explicitly avoided).
- Size: production +141, tests +775. Total ~14.5% over the 800-line review budget;
  accepted as a single PR (small symmetric production diff, heavily parametrized
  tests, identical rollback boundary whether split).

## Deferred (follow-on)
- **Slice 3 `purge-transactional-cleanup`**: `purge` auto-commit + fix #141 (dangling
  references after `--force`) and #142 (silent `vectors.db` drop / no reindex prompt).
- `autocommit: false` opt-out flag; per-workspace-once confidential notice (would need
  persisted state).
