# Proposal: Privacy Purge — Right-to-Be-Forgotten, Slice 1 (Whole-File Expunge)

## Intent

`forget` deletes a concept's catalog rows + bundle file but leaves `raw/` and
all git history intact — **content stays git-recoverable by design**. Privacy
purge is the opposite contract: the destructive, **irreversible** true-erasure
verb that expunges a concept and its source raw file from **all git history**.
This is the highest-stakes operation in openkos — an in-place `git-filter-repo`
history rewrite with **NO git-undo**, no reflog, no backup — and the **last
MVP-2 deliverable** (roadmap:70). Slice 1 ships honest whole-file erasure with a
named residual; it must NOT claim complete right-to-be-forgotten.

## Scope

### In Scope (Slice 1)
- New `purge` verb reusing `forget` Phase-A resolution (`_resolve_concept_path`,
  `find_provenance_descendants`, reference-aware refuse-unless-`--force`).
- Expunge `raw/<name>` (from each purge-set source's `resource:` frontmatter) +
  each purge-set `bundle/<id>.md` from **all git history + working tree** via
  `git-filter-repo` subprocess (first git/subprocess dependency in `src/`).
- Fail-closed rails (refuse before any write): dirty tree; workspace root ≠ git
  root; commits on ANY remote; typed confirmation phrase; reference-aware refuse
  runs first. **No** pre-purge backup (would preserve what is erased).
- Index cleanup: delete `.openkos/{fts,vectors,graph}.db` + rebuild (not
  row-DELETE — SQLite freelist is recoverable). Purge writes **no** tombstone.
- `doctor` check for `git` + `git-filter-repo`; graceful "not installed → clear
  error, refuse". Bandit S603/S607 justification for the subprocess call.
- CLI **WARNS** of the documented residual leak.

### Out of Scope / Non-Goals (Slice 2)
- **Content-scrub** of `index.md`/`log.md` **history blobs** — the purged
  id/title/bullet + prior forget tombstone REMAIN there until Slice 2.
- Prior-tombstone scrub; committed-`.openkos` (fts.db) leak vector.
- `forget --hard` alias (separate verb prevents fat-finger escalation).

## Capabilities

### New Capabilities
- `privacy-purge`: irreversible whole-file history expunge of a purge-set's raw
  + bundle files, fail-closed safety contract, index nuke+rebuild.

### Modified Capabilities
- `doctor-command`: **ADD** — `git` and `git-filter-repo` availability check.

## Approach

`purge <concept>` runs forget's pure Phase-A (resolve → purge-set → orphan
closure → reference-aware refuse). New git-rewrite module collects target paths
(each source's `raw/<name>` + each `bundle/<id>.md`), enforces all fail-closed
rails, requires the typed phrase, then invokes `git-filter-repo --invert-paths
--path ...` via subprocess, deletes `.openkos/*.db`, and rebuilds indexes.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/cli/main.py` | New | `purge` verb; reuses forget Phase-A |
| new git-rewrite module | New | First subprocess/git usage; filter-repo call |
| `doctor` | Modified | git + git-filter-repo availability check |
| `state/reindex.py` + `.openkos/*.db` | Modified | file-delete + rebuild |
| `pyproject.toml` | Modified | git-filter-repo dependency |
| `docs/cli.md` | Modified | document purge + residual warning |

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Irreversible over-purge (wrong path set) | Med | Reference-aware refuse + full preview + typed confirm |
| Rewrite published history | Med | Refuse if commits on ANY remote |
| Corrupt/partial rewrite | Low | Refuse dirty tree; refuse root≠git-root; atomic filter-repo |
| git-filter-repo absent | Med | doctor check + graceful refuse |
| Residual leak misread as full RTBF | High | Mandatory CLI warning; Slice 2 named non-goal |
| Subprocess flagged by bandit | Low | S603/S607 justification with fixed argv |

## Rollback Plan

**None post-execution** — the rewrite is irreversible by design (no git-undo).
Rollback = the fail-closed rails that refuse **before** any write. Pre-execution
abort is fully safe; a completed purge cannot be reverted.

## Dependencies

- `git-filter-repo` (new runtime dep) + a `git` binary on PATH.

## Success Criteria

- [ ] `purge <concept>` removes `raw/<name>` + `bundle/<id>.md` blobs from
      `git rev-list --objects --all`, reflog, and `git cat-file`.
- [ ] Refuses on dirty tree / root≠git-root / any-remote / wrong typed phrase /
      unforced referenced concept.
- [ ] `.openkos/*.db` deleted + rebuilt; no tombstone written.
- [ ] `doctor` reports missing git-filter-repo; purge refuses when absent.
- [ ] CLI warns residual id/title/bullet remains in shared-file history.

## Arc Note

Slice 1 of the RTBF verb — completes MVP-2. Slice 2 = content-scrub of
`index.md`/`log.md` history + tombstone scrub + committed-`.openkos` leak. Most
destructive verb in openkos: git-rewrite correctness + safety rails are the
critical review surface (full 4R at verify).
