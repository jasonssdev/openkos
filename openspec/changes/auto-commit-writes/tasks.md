# Tasks: Auto-Commit Writes (Git Lifecycle Slice 2)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 550-750 (production ~150; tests dominate) |
| 800-line budget risk | Low (design's own baseline was 400; against our 800 budget the same estimate is comfortably under) |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | auto-forecast |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
800-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `_autocommit` + `_commit_has_confidential` helpers, wired into all 6 verbs | PR 1 (single) | `uv run pytest tests/unit/cli/test_main_autocommit.py tests/unit/cli -k autocommit` | Real temp git repo via `tmp_path` + Slice 1's `isolate_git_identity`/`init_repo` fixtures (`tests/unit/vcs/conftest.py`) | Delete `_autocommit`, `_commit_has_confidential`, and the 6 call sites in `src/openkos/cli/main.py`; no schema/on-disk change |

Note: the design's own sizing note flagged Medium-High risk against a 400-line baseline and suggested a 2-PR split (helper+NOTICE+ingest/forget/relate, then merge/unmerge/reconcile). Against OUR 800-line budget the same 550-750 estimate is Low risk, so a single PR is recommended. If actual diff approaches or exceeds 800 during apply, fall back to that same PR-A/PR-B split (design.md "Sizing Note").

## Phase 1: Helper Foundation

- [x] 1.1 RED: `tests/unit/cli/test_main_autocommit.py` — `_autocommit`: not-a-repo → WARNING to stderr, returns, no exception (spec "Not a git repository")
- [x] 1.2 RED: identity unset → WARNING to stderr, no commit attempted (spec "Git identity unset")
- [x] 1.3 RED: `commit_paths` raising `GitError`/`OSError` → caught, WARNING to stderr, no raise (spec "Commit step raises a git error")
- [x] 1.4 RED: success path → exactly one commit via scoped `git add -- <paths>` (never `-A`/`-a`) with the given message (spec "Post-Phase-B Commit", "Scoped Staging Only")
- [x] 1.5 RED: `_commit_has_confidential` — single staged concept file with `sensitivity: confidential` → `True`; skips `bundle/index.md`/`bundle/log.md`/`raw/**`/missing paths
- [x] 1.6 RED: multiple confidential files staged → helper still emits exactly ONE NOTICE per invocation (spec "Multiple confidential files still emit only one notice")
- [x] 1.7 RED: no confidential-ranked content staged → no NOTICE emitted (spec "No confidential content, no notice")
- [x] 1.8 GREEN: implement `_autocommit(root, paths, message)` in `src/openkos/cli/main.py`, cloned from `init`'s git block (`main.py:203-244`), per design's Helper Contract — never raises, never alters exit code
- [x] 1.9 GREEN: implement `_commit_has_confidential(root, paths)` reading frontmatter `sensitivity` and comparing `str(meta.get("sensitivity","")).strip() == "confidential"` (NOT `blocks_llm_send`), per design's corrected-detection decision
- [x] 1.10 REFACTOR: confirm all 1.1-1.7 RED tests are GREEN; no behavior drift from the helper contract

## Phase 2: Per-Verb Wiring — `ingest`, `forget`, `relate`

- [x] 2.1 RED: `ingest` — successful Phase B → one commit `openkos: ingest <name> (+N concepts)` containing `imported_paths` + `bundle/index.md` + `bundle/log.md`; clean tree after (spec "Ingest commits new concept files and log/index")
- [x] 2.2 RED: `forget` — one commit `openkos: forget <canonical_id>` (append `(+<n-1> descendants)` when cascade) containing removed concept file(s) + index/log (spec "Forget commits the removed concept file")
- [x] 2.3 RED: `relate` — one commit `openkos: relate <src> -> <dst> (<type>)` containing `bundle/<source_canonical>.md` + `bundle/log.md` (spec "Remaining mutating verbs...")
- [x] 2.4 RED: for each of the 3 verbs — declined/refused confirm gate → Phase B skipped, no commit exists (spec "Declined confirm gate makes no commit")
- [x] 2.5 RED: for each of the 3 verbs — unrelated pre-existing dirty file elsewhere stays uncommitted/modified after success (spec "Unrelated dirty file is left untouched")
- [x] 2.6 RED: for each of the 3 verbs — not-a-repo / identity-unset / commit-error → stderr WARNING, verb still exits its normal success code (spec "Non-Fatal Degradation")
- [x] 2.7 GREEN: wire `_autocommit` call after `ingest`'s final success `typer.echo` (`main.py:838-845` writes), outside the Phase-B `try`, with `imported_paths` per design table
- [x] 2.8 GREEN: wire `_autocommit` call after `forget`'s final success echo (`main.py:1021-1032` writes) with `purge_ids`-derived paths
- [x] 2.9 GREEN: wire `_autocommit` call after `relate`'s final success echo (`main.py:2003-2005` writes)

## Phase 3: Per-Verb Wiring — `merge`, `unmerge`, `reconcile`

- [x] 3.1 RED: `merge` — one commit `openkos: merge <absorbed> into <survivor>` containing `index.md`, `log.md`, `touched_files`, survivor + absorbed-deletion (spec "Remaining mutating verbs...")
- [x] 3.2 RED: `unmerge` — one commit `openkos: unmerge <absorbed_canonical>` containing `index.md`, `log.md`, `rewritten_files`/`relation_rewrite_files`, recreated absorbed + survivor
- [x] 3.3 RED: `reconcile` — one commit, symmetric `openkos: reconcile <a> <-> <b>` or directional `openkos: reconcile <winner> supersedes <loser>`, containing both canonical files + `log.md`
- [x] 3.4 RED: for each of the 3 verbs — declined confirm gate → no commit (reuse pattern from 2.4)
- [x] 3.5 RED: for each of the 3 verbs — unrelated dirty file untouched (reuse pattern from 2.5)
- [x] 3.6 RED: for each of the 3 verbs — degradation paths (not-a-repo / no identity / commit error) → WARNING + normal exit code (reuse pattern from 2.6)
- [x] 3.7 GREEN: wire `_autocommit` call after `merge`'s final success echo (`main.py:2339-2373` writes)
- [x] 3.8 GREEN: wire `_autocommit` call after `unmerge`'s final success echo (`main.py:2635-2661` writes)
- [x] 3.9 GREEN: wire `_autocommit` call after `reconcile`'s final success echo (`main.py:3105-3108` writes), building symmetric/directional message

## Phase 4: Cross-Cutting Guards

- [x] 4.1 RED: `reindex` MUST NOT call `_autocommit` — assert no new commit and `.openkos/*.db` absent from any auto-commit `git show --stat HEAD` across all 6 verb runs (spec "Exclusions and Unconditional Behavior", "Derived index database is never committed")
- [x] 4.2 RED: grep/AST guard — no `git add -A` / `git add -a` anywhere in `_autocommit` or `commit_paths` call sites
- [x] 4.3 RED (extend Slice 1's layering guard test) — canonical layer (`src/openkos/model/`, canonical write modules) imports no `openkos.vcs`
- [x] 4.4 RED: confirm no CLI flag/config option exists to disable auto-commit (spec "No opt-out exists")
- [x] 4.5 GREEN: fix any guard failures surfaced by 4.1-4.4

## Phase 5: Final Verification

- [x] 5.1 Run full suite: `uv run pytest` — all RED tests now GREEN, no regressions
- [x] 5.2 Run quality gate: `uv run ruff check . && uv run ruff format --check . && uv run mypy .`
- [x] 5.3 Confirm 90% branch coverage maintained for `src/openkos/cli/main.py` and new helper code
