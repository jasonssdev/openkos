# Archive Report: purge-transactional-cleanup (git-lifecycle Slice 3, final)

**Date**: 2026-07-24
**Change**: `purge-transactional-cleanup` — git-lifecycle Slice 3 (final)
**Status**: ARCHIVED

## Executive Summary

Slice 3 of the git-lifecycle "Auto" arc (#145) — and its final slice: closes
two long-deferred purge bugs, #141 (dangling references after `--force`
purge go undetected) and #142 (silent `vectors.db` drop with no reindex
prompt), and wires `purge` into the shared `_autocommit` convention so every
mutating verb now leaves a clean working tree after Phase B. Slice 1 (#151)
set up git in `init`; Slice 2 (#153) wired `_autocommit` into the other six
mutating verbs; Slice 3 closes the loop on `purge`, the one verb Slice 2
deliberately left unwired pending this transactional-cleanup work.

**Merged**: PR #155 (single PR, `size:exception` accepted — see Notes).
**Arc**: #145 — COMPLETE. All three slices shipped: Slice 1 (#151), Slice 2
(#153), Slice 3 (#155).
**All tests pass**: 1963 passed.
**Verification verdict**: PASS WITH WARNINGS (0 CRITICAL, 2 WARNING, 1
SUGGESTION — no functional defects; see Notes).

## Change Scope

### Summary
- **#141 — dangling-reference detection (detect-only)**: new
  `check_dangling_targets(docs)` in `lint.py`, run beside `check_orphans`.
  Flags any concept whose `relations:` frontmatter target or body markdown
  bundle link (resolved via the existing `normalize_link`) names a concept
  id absent from disk. Surfaced in both `openkos lint` (new "Dangling
  references:" section) and `openkos status` ("Needs attention"). Purely
  informational — no gating, no frontmatter stripping, no bundle mutation.
- **#142 — vectors.db awareness (message-only)**: after a successful purge
  drops `.openkos/vectors.db` (existing behavior, unchanged), `purge`'s
  success output now warns that dense retrieval is degraded and instructs
  the user to run `openkos reindex` (no prompt, no auto-reindex). `status`
  surfaces a missing `layout.vectors_db_path` under "Needs attention".
  `doctor` gains a new workspace-`vectors.db`-presence check, distinct from
  the existing `:memory:`-probe check (check 7, code path byte-unchanged —
  only its docstring renumbered nine → ten checks).
- **Purge auto-commit**: after `purge`'s live-tree cleanup
  (`_purge_clean_live_index`/`_purge_clean_live_log`), a new
  `paths_dirty(cwd, rel_paths)` helper in `vcs/git.py` gates whether to
  call the shared `_autocommit(root, paths, message)` helper (reused
  byte-unchanged from Slice 2) on `bundle/index.md` + `bundle/log.md`, with
  message `openkos: purge <id>` (`(+N)` for cascaded scope). Non-fatal on
  git failure (matches purge's existing degrade convention — the rewrite is
  already irreversible), and tolerant of the frequent case where
  `git-filter-repo`'s own rewrite already left the live tree clean (empty
  diff → no error, no spurious commit/warning).

### Files Modified
1. `src/openkos/lint.py` — `LintDoc.relations` field, `check_dangling_targets`,
   `LintFinding.kind` gains `"dangling"`, `LintReport.dangling`.
2. `src/openkos/cli/main.py` — dangling-reference rendering in `lint` and
   `status`; purge deferred-reembed warning echo; `status` missing-vectors.db
   entry; `doctor` new workspace-vectors check; purge auto-commit call site
   after `_purge_rebuild_indexes`/live cleanup.
3. `src/openkos/vcs/git.py` — new `paths_dirty(cwd, rel_paths) -> bool`
   (`git status --porcelain -- <rel_paths>`, `--` guard). `commit_paths`/
   `_autocommit` confirmed byte-unchanged (verified via `git show`).
4. `tests/unit/test_lint.py`, `tests/unit/cli/{test_lint,test_status,
   test_purge,test_doctor}.py`, `tests/unit/vcs/test_git_adapter.py` — new
   and extended test coverage for all three pieces.
5. `openspec/specs/{lint,status,privacy-purge,doctor-command}/spec.md` —
   delta requirements merged in at archive (this report); no new
   capabilities introduced.

### Test Coverage
- Full suite: 1963 passed (up from 1931 at Slice 2 baseline).
- `ruff check` / `ruff format --check` / `mypy` all clean.
- Coverage 97.79% (≥90% required); `lint.py` 100%, `vcs/git.py` 98% (misses
  pre-existing/unrelated; new `paths_dirty` 100% covered), `main.py` 96%.
- Scenario conformance: every spec scenario across all 4 capabilities has a
  passing covering test, with one documented gap (see Notes).
- Guards independently verified: no test reads `openspec/changes/` paths
  (zero matches across all 6 touched test files); layering clean —
  `lint.py` imports only config/model, `vcs/git.py` has zero internal
  `openkos` imports, no reverse `cli` imports.

## Verification Detail (from verify-report, engram #1834)

- **4a dangling-ref detection**: `relations:` and body-link targets both
  flagged; valid references not flagged; corrupt `relations:` raises inside
  `collect_docs`, caught, emits a skip notice (never crashes); surfaces
  consistently in both `lint` and `status`.
- **4b vectors.db awareness**: purge warning message confirmed; `status`
  surfaces absent `vectors_db_path`; `doctor`'s new check is workspace-only
  (`[SKIP]` pre-init) and distinct from the unchanged `:memory:`-probe check.
- **4c purge auto-commit**: `paths_dirty` correctly gates the commit — a
  no-op self-scope purge produces no commit and no spurious warning; a
  non-no-op purge produces exactly one commit; a `GitError` from
  `paths_dirty` falls through non-fatally; `commit_paths`/`_autocommit`
  confirmed byte-unchanged via `git show`; commit fires strictly after the
  typed-confirmation phrase, Phase B, and live cleanup.

## Notes / Deviations

- **Verify verdict**: PASS (0 CRITICAL). Two WARNINGs were raised and
  accepted as non-blocking:
  1. RESOLVED before merge: verify flagged that the "purge leaves a
     referring document detectably dangling" scenario had no literal
     purge→lint/status integration test. An end-to-end acceptance test
     (`test_purge_force_leaves_dangling_reference_detected_by_lint_and_status`
     in `tests/unit/cli/test_purge.py`) was added in PR #155, proving the
     full chain: `purge --force` erases a referenced concept, the referrer's
     relation is left dangling, and both `lint` and `status` surface it.
  2. The actual diff (949+44 = 993 changed lines across 10 files) exceeded
     both the 800-line review budget and design.md's own upper estimate.
     design.md had recommended chaining two self-contained PRs (#141
     dangling-ref core; #142 + auto-commit) specifically to manage this
     risk; tasks.md's forecast (~650-700 lines, "Chained PRs recommended:
     No") undershot the actual size, and the single-PR decision was not
     revisited once the real diff size was known. Accepted as a
     `size:exception` single PR (#155) — a process/delivery-guard
     observation, not a functional defect — rather than splitting
     post-hoc.
- The `doctor` workspace-vectors check's `[FAIL]` remediation names
  `openkos reindex`, consistent with the purge-side warning message —
  keeping remediation wording symmetric across `purge`, `status`, and
  `doctor`.

## Arc Status: #145 COMPLETE

All three git-lifecycle "Auto" slices have shipped:
- **Slice 1** (`#151`): `init` sets up local git (repo, identity check,
  ignore rules).
- **Slice 2** (`#153`): `_autocommit`/`_commit_has_confidential` wired into
  `ingest`, `forget`, `relate`, `merge`, `unmerge`, `reconcile`.
- **Slice 3** (`#155`, this change): `#141`/`#142` purge bugs closed;
  `purge` wired into the same auto-commit convention via `paths_dirty`.

Every mutating verb now leaves a clean working tree after its Phase B, and
the two previously-silent purge failure modes (dangling references,
unwarned vector-index staleness) are now detectable by `lint`/`status`/
`doctor`.

## Deferred (follow-on, none currently scheduled)
- **Strip-on-force cleanup**: rewriting a referring document's dangling
  `relations:`/body link automatically after `--force` purge (rejected in
  proposal — detect-only was the deliberate scope for this slice).
- **`autocommit: false` opt-out flag** (deferred since Slice 2).
- **mtime-based `vectors.db` staleness detection** — this slice's checks
  are bound to absent/present only, not staleness.

## Traceability (Engram observation IDs)
- Proposal: `sdd/purge-transactional-cleanup/proposal` — obs #1828
- Spec (delta specs): `sdd/purge-transactional-cleanup/spec` — obs #1829
- Design: `sdd/purge-transactional-cleanup/design` — obs #1830
- Tasks: `sdd/purge-transactional-cleanup/tasks` — obs #1831
- Verify report: `sdd/purge-transactional-cleanup/verify-report` — obs #1834
- Archive report (this document): `sdd/purge-transactional-cleanup/archive-report`

## Source of Truth Updated
The following main specs now reflect the merged Slice 3 behavior:
- `openspec/specs/lint/spec.md` — added "Dangling-Reference Scan"
- `openspec/specs/status/spec.md` — added "Needs-Attention Surfaces
  Dangling References" and "Needs-Attention Surfaces Missing Vector Index"
- `openspec/specs/privacy-purge/spec.md` — added "Deferred-Reembed Warning
  On Success" and "Post-Rewrite Live-Tree Auto-Commit"
- `openspec/specs/doctor-command/spec.md` — added "Workspace Vector Index
  Presence Check"
