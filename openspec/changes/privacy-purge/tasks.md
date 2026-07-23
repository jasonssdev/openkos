# Tasks: Privacy Purge — RTBF Slice 1 (Whole-File Expunge)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~760 (PR1 ~400, PR2 ~360) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR1 (adapter+substrate) -> PR2 (purge verb) |
| Delivery strategy | auto-chain |
| Chain strategy | feature-branch-chain |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `openkos.vcs.git` adapter + real-git fixture + doctor checks | PR 1 | `uv run pytest tests/unit/vcs/ tests/**/test_doctor*.py -k git` | real tmp-git-repo fixture (git init, no user repo touched) | revert `src/openkos/vcs/`, doctor git checks, fixture file — no purge verb depends yet |
| 2 | `purge` verb wiring (rails + Phase A/B + docs) | PR 2 | `uv run pytest tests/**/cli/test_purge*.py` | real tmp-git-repo fixture, full rewrite+finalize | revert `purge` command registration in `cli/main.py` + its tests; PR1 adapter stays intact |

Feature-branch-chain bases: PR1 base = `feature/privacy-purge` tracker branch; PR2 base = PR1 branch.

## PR1: Adapter + Safety Substrate

### Phase 1: Fixture Infrastructure

- [x] 1.1 Create `tests/conftest.py` (or `tests/unit/vcs/conftest.py`) fixture `tmp_git_repo`: `git init` in `tmp_path`, pin `GIT_AUTHOR_NAME/EMAIL` + `GIT_COMMITTER_NAME/EMAIL` env, `git config user.*`, init openkos workspace, ingest a Source, commit. — NOTE: shells out to real git.
- [x] 1.2 RED: test fixture produces a clean repo with one commit containing the ingested Source (assert `git status --porcelain` empty, `git log` has 1 commit). — NOTE: real-git fixture.
- [x] 1.3 GREEN: implement fixture until 1.2 passes.

### Phase 2: `openkos.vcs.git` Adapter

- [x] 2.1 RED: `tests/unit/vcs/test_git_adapter.py::test_expunge_paths_argv_shape` — assert fixed argv `["git","filter-repo","--force","--invert-paths","--paths-from-file",<tmp>]`, `literal:<path>` lines in temp file, no shell interpolation (spec req 3 / threat matrix: subprocess argv safety). — NOTE: real-git fixture.
- [x] 2.2 GREEN: create `src/openkos/vcs/__init__.py`, `src/openkos/vcs/git.py` with `_run(argv, cwd)` (sole `# noqa: S603`), `GitError`, `GitUnavailable`, `expunge_paths()`. — NOTE: RUF100 flagged `S607` as unused (argv is a runtime list, not a literal partial-path string ruff can detect), so the suppression is `# noqa: S603` only — same sole call site, narrower honest comment.
- [x] 2.3 RED: `test_git_available`/`test_filter_repo_available` — both present -> True; monkeypatch `shutil.which` -> False for each (doctor req precondition).
- [x] 2.4 GREEN: implement `git_available()`, `filter_repo_available()`.
- [x] 2.5 RED: `test_repo_root_matches_workspace` / `test_repo_root_returns_none_outside_any_git_repo` — non-git-root case (threat matrix: git repository selection). — NOTE: real-git fixture.
- [x] 2.6 GREEN: implement `repo_root(cwd)` via `git rev-parse --show-toplevel`.
- [x] 2.7 RED: `test_is_clean_false_on_dirty_working_tree` / `test_is_clean_false_on_staged_change` (threat matrix: commit state). — NOTE: real-git fixture.
- [x] 2.8 GREEN: implement `is_clean(cwd)` via `git status --porcelain`.
- [x] 2.9 RED: `test_has_published_commits_true_after_push_to_bare_remote` — push to bare remote, assert True; `test_has_published_commits_false_with_no_remote` -> False (threat matrix: push state). — NOTE: real-git fixture.
- [x] 2.10 GREEN: implement `has_published_commits(cwd)` via `git branch --remotes --contains HEAD` (fail-closed: any remote-tracking ref containing HEAD counts as published).
- [x] 2.11 RED: `test_expunge_paths_removes_blobs_from_history` — full rewrite on fixture, assert path gone from `git rev-list --objects --all`, `git reflog` empty, `git cat-file -e <sha>` rc!=0 (spec req 3, req 6). — NOTE: real-git fixture, slow.
- [x] 2.12 GREEN: implement `expunge_paths()` rewrite + finalize (`git reflog expire --expire=now --all`, `git gc --prune=now`).
- [x] 2.13 RED: `test_run_maps_file_not_found_to_git_unavailable` / `test_expunge_paths_maps_nonzero_exit_to_git_error` — `FileNotFoundError` -> `GitUnavailable`; nonzero rc -> `GitError` with stderr tail.
- [x] 2.14 GREEN: implement exit-code mapping in `_run`/callers.
- [x] 2.15 REFACTOR: consolidated adapter docstrings, confirmed RUF100 keeps `# noqa: S603` honest, `uv run ruff check src/openkos/vcs/` passes.

### Phase 3: Doctor Checks

- [x] 3.1 RED: `tests/unit/cli/test_doctor.py::test_doctor_git_and_filter_repo_pass` — both available -> `[PASS]` lines, independent of Ollama (spec ADDED requirement scenario: both available).
- [x] 3.2 RED: `test_doctor_git_filter_repo_missing` — filter-repo missing -> `[FAIL]` + install remediation, informational (no exit-code effect).
- [x] 3.3 RED: `test_doctor_git_missing` — git itself missing -> `[FAIL]` + install remediation.
- [x] 3.4 RED: `test_doctor_git_checks_run_pre_init_independent_of_ollama` — runs before init, unaffected by Ollama unreachability.
- [x] 3.5 GREEN: added 2 `CheckResult` entries (checks 8/9) to `doctor` in `src/openkos/cli/main.py`, mirroring the ollama-check style, using `openkos.vcs.git.git_available()`/`filter_repo_available()`.
- [x] 3.6 REFACTOR: reviewed — no shared remediation-message formatting exists yet between the ollama check and the new git checks (each names its own install command), so no dedupe was needed; noted as-is rather than forcing a premature abstraction.

### Phase 4: PR1 Wrap-up

- [x] 4.1 Add `git-filter-repo` to `pyproject.toml` dev dependency group (not runtime deps).
- [x] 4.2 CI install step — DEVIATION: no `.github/workflows/ci.yml` edit was made. Instead, `git-filter-repo` was added to the `dev` dependency group and `uv.lock` regenerated (`uv lock`) + verified (`uv sync --locked`); CI's existing `uv sync --locked` step already installs it automatically, so no separate CI install step is needed. Flagging this as an explicit deviation from the literal task wording, per apply-phase instruction to note deviations rather than silently follow/skip.
- [x] 4.3 Full run: `uv run pytest tests/unit/vcs/ tests/unit/cli/test_doctor.py` green (43 tests); whole-tree `uv run pytest` (1726 passed), `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .`, `uv sync --locked` all green.

## Correction batch (post-4R-review, PR1)

Bounded correction batch on `src/openkos/vcs/git.py` addressing 4R review findings
on the irreversible history-rewrite machinery (2 CRITICAL, 4 WARNING). Branch
`feat/privacy-purge-vcs`, commits `647ca5c` (fix) and `d53b577` (test).

- [x] C1 CRITICAL: newline/control-char injection in `--paths-from-file` — a
  `rel_path` containing `\n` re-dispatches a second git-filter-repo directive
  (e.g. `glob:**`), which combined with the always-passed `--invert-paths`
  escalates a single-path purge into a mass deletion (proof: vendored
  `git_filter_repo.py::get_paths_from_file`, ~:2358-2404). Fixed via
  `_validate_rel_paths`: rejects newline, carriage-return, control
  characters, empty/whitespace, absolute paths, and `..` segments —
  fail-closed, no paths file written, no subprocess invoked. RED tests:
  `test_expunge_paths_rejects_newline_in_rel_path`,
  `_rejects_carriage_return_in_rel_path`,
  `_rejects_control_character_in_rel_path`, `_rejects_absolute_rel_path`,
  `_rejects_dotdot_segment_in_rel_path`,
  `_rejects_empty_or_whitespace_rel_path` (all assert `_run` never called
  via a spy, and no stray temp file is left).
- [x] C2 CRITICAL: partial-failure window in finalize (reflog-expire/gc)
  after a successful rewrite was silent and indistinguishable from a clean
  failure — purged blobs remain recoverable via the un-expired reflog if
  finalize fails post-rewrite. Fixed via new `GitFinalizeError(GitError)`,
  raised ONLY when filter-repo already succeeded but reflog-expire or gc
  failed, with the exact remediation command
  (`git reflog expire --expire=now --all && git gc --prune=now`) in the
  message. Pre-rewrite filter-repo failures stay plain `GitError`. RED
  tests: `test_expunge_paths_reflog_expire_failure_raises_git_finalize_error`,
  `_gc_failure_raises_git_finalize_error`,
  `_pre_rewrite_failure_is_plain_git_error_not_finalize_error` (regression
  guard).
- [x] W1 WARNING: empty `rel_paths` is a destructive no-op-filter —
  git-filter-repo's `sanity_check_args` sets `inclusive=False` with no
  filter paths, rewriting all of history while purging nothing (proof:
  `git_filter_repo.py::sanity_check_args`, ~:2263-2273). Fixed: guarded in
  `_validate_rel_paths`, raises before any subprocess call. RED test:
  `test_expunge_paths_rejects_empty_rel_paths_list`.
- [x] W2 WARNING: `_run` only caught `FileNotFoundError`, letting a
  present-but-broken binary (e.g. `PermissionError` from a TOCTOU race after
  `shutil.which`) escape as a raw `OSError`. Fixed: `_run` now catches
  `OSError` broadly, mapping to `GitError` (FileNotFoundError still maps to
  `GitUnavailable`). RED test: `test_run_maps_permission_error_to_git_error`.
- [x] W3 WARNING: `repo_root` swallowed ANY non-zero exit as `None`,
  conflating "not a git repo" with a broken-git error and surfacing no
  stderr. Fixed: `None` only for the genuine "not a git repository" stderr
  match; any other non-zero exit raises `GitError` with stderr. RED test:
  `test_repo_root_raises_git_error_on_unexpected_failure` +
  `_still_returns_none_for_genuine_not_a_repo` (regression guard).
- [x] W4 WARNING (test-only): added
  `test_expunge_paths_removes_blobs_from_all_refs` (second branch, real
  multi-ref proof via `git rev-list --objects --all`) and pinned
  `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE` in the `tmp_git_repo` fixture's git
  env for reproducible commit SHAs (`tests/unit/vcs/conftest.py`).

Deliberate non-fix: no `subprocess.run(timeout=...)` was added to `_run` — a
mis-calibrated timeout could kill an in-flight `git filter-repo` rewrite
mid-write, which is the exact partial-failure catastrophe `GitFinalizeError`
exists to detect and report. Documented as a code comment in `_run`'s
docstring.

Spec check: these changes strengthen the adapter toward the existing
fail-closed + irreversibility-safety contract in
`openspec/changes/privacy-purge/specs/privacy-purge/spec.md` (req 3, req 6).
No spec requirement needed to change — the residual-warning UX and rail
ordering (referenced by these requirements) live in PR2 (`purge` verb, not
yet built); this batch only hardens the PR1 adapter primitive the verb will
call.

Whole-tree verification (all green): `uv run pytest` -> 1740 passed (was
1726 before this batch, +14 new tests); `uv run mypy .` -> no issues;
`uv run ruff check .` -> all checks passed (sole `# noqa: S603` suppression
confirmed, in `_run`); `uv run ruff format --check .` -> 127 files already
formatted; `uv sync --locked` -> resolves cleanly.

## Correction batch 2 (post-re-validation, PR1)

Second bounded correction on `src/openkos/vcs/git.py`, closing one residual
CRITICAL injection vector found by re-validating the first correction batch
against the vendored git-filter-repo parser. Scope: `src/openkos/vcs/git.py`
+ `tests/unit/vcs/**` only.

- [x] C3 CRITICAL: `==>` rename-delimiter injection in `--paths-from-file` —
  `_validate_rel_paths` rejected `\n`/`\r`/control chars/absolute/`..`/empty
  but NOT the byte sequence `==>`. `get_paths_from_file` (vendored
  `git_filter_repo.py` ~:2373-2374) splits each line on `==>` ANYWHERE it
  occurs, BEFORE dispatching on the `literal:`/`glob:`/`regex:` prefix. A
  `rel_path` like `"bundle/target.md==>x"` therefore wrote
  `literal:bundle/target.md==>x`, which filter-repo parsed as a RENAME
  directive (`match="bundle/target.md"`, `repl="x"`), not a filter/delete.
  With no other filter entry present, `sanity_check_args` sets
  `inclusive=False` (~:2263-2273), which keeps EVERY file
  (`newname()`, ~:3856-3874) — the purge target was silently NEVER removed
  from history, while `git filter-repo` still exited 0 and `expunge_paths`
  reported success. Catastrophic for an irreversible privacy operation: it
  confirms success while doing nothing. FIX: `_validate_rel_paths` now also
  rejects any `rel_path` containing `==>`, fail-closed, before any temp-file
  write or subprocess call, raising the same `ValueError` type as the other
  rejections. RED test: `test_expunge_paths_rejects_rename_delimiter_in_rel_path`
  (spy `_run` asserted never called). Companion test confirming lone `=`/`>`
  (never the full `==>` sequence) remain allowed as legitimate filename
  characters: `test_expunge_paths_allows_lone_equals_and_gt_in_rel_path`.

Audit conclusion: re-read `get_paths_from_file` end-to-end
(vendored `git_filter_repo.py` ~:2358-2405). Confirmed the ONLY line-level
special handling beyond what was already covered is: (a) `line.rstrip(b'\r\n')`
— only strips a TRAILING `\r`/`\n`, already rejected as embedded control
chars by C1; (b) skip fully-blank lines — unreachable since our code always
writes a non-empty `literal:<path>` line and empty/whitespace `rel_path` is
already rejected; (c) skip lines starting with `#` — unreachable because
every line is hardcoded to start with the literal prefix `literal:`, which
never starts with `#`; (d) the `literal:`/`glob:`/`regex:` prefix dispatch —
already neutralized by our hardcoded `literal:` prefix; (e) the `==>` rename
split — the vector fixed by C3. No quote/backslash interpretation, no other
delimiter, and no additional special token exists in the parser. `==>` was
the LAST vector: with it rejected, the `literal:<rel_path>` line can now only
ever be parsed as a single filter/delete entry — the literal-match contract
is complete.

Whole-tree verification (all green): `uv run pytest` -> 1742 passed (was
1740 before this batch, +2 new tests); `uv run mypy .` -> no issues found in
127 source files; `uv run ruff check .` -> all checks passed (sole
`# noqa: S603` suppression unchanged, in `_run`); `uv run ruff format --check .`
-> 127 files already formatted; `uv sync --locked` -> resolves cleanly.

## PR2: `purge` Verb

### Phase 5: Phase-A Reuse + Rail Tests (RED)

- [ ] 5.1 RED: `test_purge_self_scope_resolves_single_concept` — reuses `_resolve_concept_path`, default `--scope self` (spec req 1).
- [ ] 5.2 RED: `test_purge_source_scope_cascades_descendants` — `--scope source` via `find_provenance_descendants` (spec req 1). — NOTE: real-git fixture.
- [ ] 5.3 RED: `test_purge_reference_aware_refuses_without_force` — rail 1, referenced concept, no `--force` (spec req 2, scenario 1).
- [ ] 5.4 RED: `test_purge_non_git_root_refuses` — rail 2, workspace not at git root (spec req 2, scenario 2). — NOTE: real-git fixture.
- [ ] 5.5 RED: `test_purge_dirty_tree_refuses` — rail 3 (spec req 2, scenario 3). — NOTE: real-git fixture.
- [ ] 5.6 RED: `test_purge_remote_present_refuses` — rail 4, bare repo + push (spec req 2, scenario 4). — NOTE: real-git fixture.
- [ ] 5.7 RED: `test_purge_tool_missing_refuses` — rail 5, monkeypatch `filter_repo_available`->False (spec req 2, scenario 5).
- [ ] 5.8 RED: `test_purge_confirmation_mismatch_no_write` — rail 6, wrong `--confirm-phrase`, assert zero writes/rewrite occurred (spec req 2, scenario 6, req 6 no-write-before-all-pass).
- [ ] 5.9 RED: `test_purge_all_rails_pass_rewrite_proceeds` — happy path precondition check before Phase B (spec req 2, scenario 7).

### Phase 6: Preview, Confirmation Phrase, Residual Warning

- [ ] 6.1 Settle exact `--confirm-phrase` string: `purge <canonical_id>` (self), `purge <root_id> (<N> concepts)` (cascade) — resolves design open question.
- [ ] 6.2 RED: `test_purge_preview_prints_residual_leak_warning` — exact warning text present at preview stage (spec req 5).
- [ ] 6.3 RED: `test_purge_success_echoes_residual_leak_warning` — warning re-printed on success (spec req 5).
- [ ] 6.4 GREEN: implement raw-path resolution (per member `okf.load_frontmatter`, `resource` validation: starts with `raw/`, no `..`, resolves under `layout.raw_dir`; warn not refuse if absent/malformed) + preview + confirmation-phrase logic in `src/openkos/cli/main.py`.

### Phase 7: Phase-B Irreversible Write + Index Rebuild

- [ ] 7.1 RED: `test_purge_self_scope_removes_blobs_from_history` — raw+concept blobs gone via `git rev-list --objects --all`, reflog empty, `git cat-file -e` fails, worktree files gone (spec req 3, scenario 1). — NOTE: real-git fixture, slow.
- [ ] 7.2 RED: `test_purge_source_scope_cascade_removes_all_blobs` — same assertions across cascade set (spec req 3, scenario 2). — NOTE: real-git fixture.
- [ ] 7.3 RED: `test_purge_deletes_and_rebuilds_index_no_tombstone` — `.openkos/{fts,vectors,graph}.db` deleted, fts+graph rebuilt via `state/reindex.py` (no Ollama), no `log.md` tombstone written (spec req 4).
- [ ] 7.4 RED: `test_purge_rebuild_failure_does_not_fail_purge` — best-effort rebuild failure still reports purge success (design: rebuild failure must not fail irreversible act).
- [ ] 7.5 GREEN: implement `purge` CLI verb Phase B in `src/openkos/cli/main.py`: call `expunge_paths` -> finalize -> `unlink(missing_ok=True)` on the 3 db files -> best-effort `_reindex_fts`/`fts.write_fts_index` + `sqlite_graph.reindex_graph(force=True)`.
- [ ] 7.6 REFACTOR: extract shared rail-checking helper if `purge` and `forget` logic duplicates; keep `forget` untouched.

### Phase 8: Wrap-up

- [ ] 8.1 Update `docs/cli.md`: document `purge`, `--scope`, irreversibility, rail order, residual-leak warning.
- [ ] 8.2 Full run: `uv run pytest tests/**/cli/test_purge*.py` green; `uv run ruff check src/openkos/cli/main.py`.
- [ ] 8.3 Confirm no regression: `uv run pytest tests/**/cli/test_forget*.py` still green (forget untouched).
