# Tasks: Purge Transactional Cleanup — Git Lifecycle Slice 3 (final)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~650-700 (production ~250-300, tests ~400) |
| 800-line budget risk | Medium |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | auto-forecast (AUTOMATIC) |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
800-line budget risk: Medium

Design's PR1/PR2 split was forecast against the 400-line default baseline. Re-forecast
against this session's actual 800-line budget: ~650-700 authored lines fits within a
single PR with margin. No maintainer decision required before apply; re-forecast during
`sdd-apply` if actual diff trends above ~750 lines.

### Suggested Work Units (informational — single PR, not a chain)

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | #141 dangling-reference detection + lint/status wiring | PR 1 (only) | `uv run pytest tests/test_lint.py -k dangling` | `CliRunner` temp workspace (lint/status) | `lint.py`/`main.py` dangling additions revertable independently |
| 2 | #142 vectors.db awareness (purge/status/doctor) | PR 1 (only) | `uv run pytest tests/test_cli.py -k vectors` | `CliRunner` temp workspace (status/doctor) | Message/check additions revertable independently |
| 3 | purge auto-commit (`paths_dirty` + `_autocommit` wiring) | PR 1 (only) | `uv run pytest tests/test_vcs_git.py -k paths_dirty` | real temp git repo (Slice 1/2 fixtures) | `paths_dirty` + purge call-site revertable independently |

## Phase 1: #141 Dangling-Reference Core (lint.py)

- [x] 1.1 RED: `tests/test_lint.py` — `LintDoc.relations` populated by `collect_docs`
      via `okf.decode_relations`; corrupt `relations:` raises `ValueError` inside
      `collect_docs` → caught, emits skip notice (read-only-never-fail). Spec: lint
      "Dangling-Reference Scan" (setup for scenarios below).
- [x] 1.2 GREEN: add `relations: tuple[str, ...]` to `LintDoc`; populate in
      `collect_docs` (`src/openkos/lint.py`), catch `ValueError` → skip notice.
- [x] 1.3 RED: `tests/test_lint.py` — `check_dangling_targets`: `relations:` target
      absent flagged; body link (via `normalize_link`) to absent id flagged; existing
      concept not flagged; self-link/external/anchor ignored. Spec: lint scenarios
      "relations: target absent flagged", "Body markdown bundle link ... flagged",
      "Reference to existing concept not flagged".
- [x] 1.4 GREEN: implement `check_dangling_targets(docs)` in `src/openkos/lint.py`
      (mirrors `check_orphans`); `LintFinding.kind` gains `"dangling"`; `LintReport`
      gains `dangling` field. Non-gating (informational only).

## Phase 2: #141 Wiring — lint + status

- [x] 2.1 RED: `tests/test_cli.py` — `openkos lint` renders "Dangling references:"
      section; exits 0; no file mutation. Spec: lint "findings don't change exit
      contract".
- [x] 2.2 GREEN: wire `check_dangling_targets` into `lint` command render in
      `src/openkos/cli/main.py`.
- [x] 2.3 RED: `tests/test_cli.py` — `openkos status` folds dangling findings into
      "Needs attention"; purge-created dangling ref detected post-purge; no dangling
      → no entry. Spec: status "Needs-Attention Surfaces Dangling References"
      (all 3 scenarios).
- [x] 2.4 GREEN: wire `lint.collect_docs` + `check_dangling_targets` into `status`'s
      "Needs attention" section in `src/openkos/cli/main.py`.

## Phase 3: #142 vectors.db Awareness

- [x] 3.1 RED: `tests/test_cli.py` — successful `purge` output includes degraded
      dense-retrieval warning + `openkos reindex` instruction; no interactive prompt.
      Spec: privacy-purge "Deferred-Reembed Warning On Success" (both scenarios).
- [x] 3.2 GREEN: append fixed echo after `_purge_rebuild_indexes` (both scope
      branches) in `purge` (`src/openkos/cli/main.py`).
- [x] 3.3 RED: `tests/test_cli.py` — `status` "Needs attention" shows missing-
      vectors.db line when `layout.vectors_db_path` absent; no entry when present.
      Spec: status "Needs-Attention Surfaces Missing Vector Index" (both scenarios).
- [x] 3.4 GREEN: wire `layout.vectors_db_path.exists()` check into `status`'s
      "Needs attention" section.
- [x] 3.5 RED: `tests/test_cli.py` — `doctor` new workspace-vectors check: present
      passes, absent fails with indented `openkos reindex` remediation, skipped
      outside a workspace. Spec: doctor-command "Workspace Vector Index Presence
      Check" (all 3 scenarios).
- [x] 3.6 GREEN: implement workspace-`vectors.db`-presence check in `doctor`
      (`src/openkos/cli/main.py`), distinct from existing `:memory:`-probe check 7;
      skipped pre-init.

## Phase 4: Purge Auto-Commit

- [x] 4.1 RED: `tests/test_vcs_git.py` — `paths_dirty(cwd, rel_paths)`: clean paths →
      `False`; modified tracked path → `True`; unrelated dirty file outside scope →
      `False`; non-git dir → `GitError`. Real temp git repo (reuse Slice 1/2
      fixtures).
- [x] 4.2 GREEN: implement `paths_dirty(cwd: Path, rel_paths: Sequence[str]) -> bool`
      in `src/openkos/vcs/git.py` via `git status --porcelain -- <rel_paths>`
      (`--` guard, scoped like `commit_paths`). `_autocommit`/`commit_paths` stay
      byte-unchanged.
- [x] 4.3 RED: `tests/test_cli.py` (real temp git repo) — clean post-rewrite cleanup
      → no commit created, no spurious WARNING; non-no-op cleanup → exactly one
      commit `openkos: purge <id>` (`(+N)` cascaded), staging only `bundle/index.md`
      + `bundle/log.md`, clean `git status`; commit failure → non-fatal WARNING to
      stderr, purge exit code unchanged. Spec: privacy-purge "Post-Rewrite Live-Tree
      Auto-Commit" (all 3 scenarios).
- [x] 4.4 GREEN: after `_purge_rebuild_indexes` (main.py, success path), call
      `paths_dirty(root, ["bundle/index.md", "bundle/log.md"])`; if `True` (or probe
      raises `GitError`, fall through), call `_autocommit` with the same paths and
      message `openkos: purge <id>` / `openkos: purge <id> (+N)`.

## Phase 5: Full Verification

- [x] 5.1 Run `uv run pytest` — full suite green, 90% branch coverage maintained.
- [x] 5.2 Run `uv run ruff check . && uv run ruff format --check . && uv run mypy .`
      — quality gate green.
- [x] 5.3 Manual smoke (per e2e testing guide): purge a referenced concept, confirm
      `lint`/`status`/`doctor` all surface the resulting dangling reference and
      missing/degraded vector index consistently.
