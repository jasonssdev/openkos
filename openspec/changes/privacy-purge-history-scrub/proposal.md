# Proposal: Privacy Purge — History Content-Scrub, Slice 2 (Completes RTBF)

## Intent

Slice 1 whole-file-expunged each concept's `raw/<name>` + `bundle/<id>.md` from
all history and cleaned the LIVE `index.md`, but honestly disclosed a residual:
the purged id/title/catalog-bullet + any prior `forget` tombstone still live in
the **HISTORY** of the SHARED surviving files `index.md`/`log.md`, and a prior
tombstone remains in the LIVE `log.md`. `purge` therefore does not yet deliver
complete right-to-be-forgotten. Slice 2 content-scrubs those residuals, folds
the scrub permanently into `purge`, removes the residual warning, and thereby
**completes MVP-2** (roadmap:70) in RTBF spirit, not just letter.

## Scope

### In Scope (Slice 2)
- `purge` ALWAYS content-scrubs (folded in, not a flag). DELETE
  `_PURGE_RESIDUAL_WARNING` (main.py:1265) + usages.
- ONE-PASS history scrub: fold a `git-filter-repo --file-info-callback` into the
  SAME `expunge_paths` run (vcs/git.py:230) alongside `--invert-paths`.
- Scrub = FULL LINE REMOVAL of each purge-set member's catalog bullet / log
  entry / forget-tombstone across all history, scoped to `bundle/index.md` +
  `bundle/log.md` ONLY (filename gate).
- Collision-scoping by markdown link-identity (reuse `_link_identity`) — never
  bare id substring; a prose mention of the id survives untouched.
- Live `log.md` cleanup: new `_purge_clean_live_log` + `remove_log_entry` twin
  of `remove_index_entry`, mirroring the live-index path.

### Out of Scope / Non-Goals
- Scrubbing arbitrary OTHER historical file content — only `index.md`/`log.md`.
- Redaction markers (rejected: leaks the fact-of-erasure).
- `--replace-text`/`--blob-callback` mechanisms (rejected: path-blind).
- Committed-`.openkos` (`fts.db`) leak vector; `forget --hard` alias.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `privacy-purge`: ADD a whole-history content-scrub requirement (index.md/
  log.md line removal + live log cleanup); REMOVE the residual-leak-warning
  requirement (purge now completes RTBF).

## Approach

Extend `expunge_paths` to also emit a file-info-callback that gates on
`filename==bundle/index.md`/`log.md`, reads blob contents, removes lines whose
markdown link-identity matches a purge-set member, and re-inserts. The callback
snippet is passed via a TEMP FILE by fixed argv (like the paths-file); the
sensitive id/title reach it via ENV/sidecar, NEVER interpolated into the source.
`purge` Phase B (main.py:1728/1739) also calls `_purge_clean_live_log`.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/vcs/git.py` | Modified | `expunge_paths` emits/passes file-info-callback |
| `src/openkos/cli/main.py` | Modified | fold scrub in; drop warning; `_purge_clean_live_log` |
| `src/openkos/bundle/log.py` | Modified | new `remove_log_entry` (twin of remove_index_entry) |
| `openspec/specs/privacy-purge/spec.md` | Modified | +scrub req, −warning req |
| `tests/conftest.py` | Modified | multi-commit `tmp_git_repo` fixture |

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Collision-scoping bug corrupts SURVIVING concepts' catalog/log (coarser than Slice 1) | High | Link-identity match (not substring) + filename gate; byte-identical round-trip assertions on sibling/prose lines |
| Callback-snippet injection via id/title | High | Snippet via fixed-argv temp file; id/title via env/sidecar, never interpolated |
| Over-scrub across all history | Med | `--file-info-callback` is path-scoped (verified vs vendored filter-repo) |
| Multi-commit test gap hides residuals | Med | Multi-commit fixture; walk every historical blob |

## Rollback Plan

**None post-execution** — same irreversible `git-filter-repo` rewrite as Slice
1, now coarser (edits shared-file content). Rollback = the unchanged fail-closed
rails that refuse BEFORE any write. A completed scrub cannot be reverted.

## Dependencies

- `git-filter-repo` `--file-info-callback` support (already a Slice 1 dep).

## Success Criteria

- [ ] Purged id/title/tombstone GONE from `index.md`/`log.md` across ALL history.
- [ ] Surviving sibling bullets + prose id-mentions round-trip byte-identical.
- [ ] Live `log.md` tombstone for the purged concept removed.
- [ ] `_PURGE_RESIDUAL_WARNING` + its scenario removed; purge = complete RTBF.
- [ ] Scrub runs in the SAME single filter-repo pass as the expunge.

## Arc Note

Slice 2 completes the RTBF verb and MVP-2. Coarser/destructive: collision-
scoping correctness + callback no-injection are the critical review surfaces
(full 4R at verify).
