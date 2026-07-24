# Proposal: Purge Transactional Cleanup — Git Lifecycle Slice 3 (final)

## Intent

Close the two open `purge` bugs and finish the "Auto" git model (arc #145; Slices 1-2 shipped in #151/#153). **#141**: a `--force` purge leaves referring docs' `relations:`/body links pointing at a now-absent concept, and neither `lint` nor `status` ever detect it. **#142**: `purge` drops `vectors.db` (deliberate deferral to `reindex`) with no message and no `status`/`doctor` awareness, so dense retrieval silently degrades. Also: `purge` is the one mutating verb still not auto-committing its post-rewrite live-tree cleanup.

## Scope

### In Scope
- **#141 — detect-only lint check**: new outbound dangling-target check in `lint.py`, surfaced in `lint` AND `status` "Needs attention". No frontmatter rewriting/stripping.
- **#142 — message-only**: `purge` success output tells the user dense retrieval is degraded (`run openkos reindex`); `status` (and `doctor`) surface a missing/absent workspace `vectors.db`. No prompt, no auto-reindex.
- **purge auto-commit**: commit the final consistent live tree after Phase B (post-rewrite `index.md`/`log.md` cleanup).

### Out of Scope
- Strip-on-force reference cleanup (rejected — invasive multi-file rewrite).
- `autocommit: false` opt-out flag; per-workspace-once confidential notice.
- mtime-based `vectors.db` *staleness* (bound to absent/missing this slice).

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `lint`: new requirement — detect outbound dangling references.
- `status`: surface dangling-reference findings and a missing vector index under "Needs attention".
- `privacy-purge`: post-purge auto-commit + degraded-dense-retrieval message.
- `doctor-command`: check the workspace's actual `vectors.db`, not only the `:memory:` extension probe.

## Approach

- **#141**: define a *reference* symmetrically with rail-1's inbound model — a `relations:` target id OR a body markdown bundle link (resolved via `lint.normalize_link`, `lint.py:376`) that names a concept id absent from disk. New `check_dangling_targets(docs)` beside `check_orphans` (`lint.py:416`); render in `lint` (`main.py:3443`) and fold into `status` "Needs attention" (`main.py:3360`).
- **#142**: after `_purge_rebuild_indexes` (`main.py:1507`) drops `vectors.db`, the success echo (`main.py:1920-1929`) adds a `run openkos reindex` line. `status` checks `layout.vectors_db_path` absence; `doctor` gains a workspace-vectors check distinct from check 7's `:memory:` probe (`main.py:4903`).
- **purge auto-commit**: reuse Slice 2's `_autocommit(root, paths, message)` (`main.py:149`) after `_purge_clean_live_*`, staging `bundle/index.md` + `bundle/log.md`, message `openkos: purge <id>` (+N). Design MUST confirm `commit_paths` tolerates an empty diff — `expunge_paths` (filter-repo) already scrubbed and checked out the rewritten `index.md`/`log.md`, so the live cleanup is frequently a no-op; otherwise add a purge-specific empty-diff guard.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/lint.py` | Modified | Add `check_dangling_targets` (outbound existence) |
| `src/openkos/cli/main.py` `lint`/`status` | Modified | Render dangling findings; status vector-index check |
| `src/openkos/cli/main.py` `purge` | Modified | #142 message + `_autocommit` after live cleanup |
| `src/openkos/cli/main.py` `doctor` | Modified | Workspace `vectors.db` presence check |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Empty-diff commit fails purge | Med | Design verifies `commit_paths` no-op tolerance; non-fatal WARN either way |
| Dangling check false positives (link forms) | Low | Reuse proven `normalize_link` identity resolution |
| Body-link scope creep | Low | Bounded testable definition mirrors rail-1 inbound model |
| Combined diff exceeds 800 budget | Med | tasks phase forecasts; chain #141(lint+status) vs #142+commit if over |

## Rollback Plan

All changes are additive and non-fatal. The lint check is informational (no gate, no writes). #142 is output/detection only. The purge auto-commit reuses `_autocommit`'s existing best-effort contract: a commit failure emits a stderr WARNING and never fails the already-irreversible purge. Revert = drop the added functions/echo lines; no data migration, no schema change. Purge's existing irreversibility is unchanged and not worsened.

## Dependencies

- Slice 1 (`init` git setup, #151) — purge requires a git-root workspace.
- Slice 2 `_autocommit`/`_commit_has_confidential` helpers (#153) — reused as-is.

## Non-Negotiables (AGENTS.md)

- Local-only git; never pushes. Everything reconstructible from canonical files (`vectors.db` stays derived/gitignored — #142 is UX, not a commit-the-binary question). Purge keeps its six fail-closed rails; auto-commit fires only after all rails pass and Phase B lands.

## Success Criteria

- [ ] **#141 closed**: after a `--force` purge that leaves a dangling `relations:`/body link, `lint` AND `status` both report it.
- [ ] **#142 closed**: `purge` warns dense retrieval is degraded and to run `openkos reindex`; `status` (and `doctor`) surface the missing `vectors.db`.
- [ ] `purge` auto-commits its post-rewrite live-tree cleanup; a commit failure never fails the purge.
- [ ] Quality green: `uv run pytest`, `ruff check`/`format --check`, `mypy .`.
