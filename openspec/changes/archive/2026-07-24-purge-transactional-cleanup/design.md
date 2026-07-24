# Design: Purge Transactional Cleanup — Git Lifecycle Slice 3 (final)

## Technical Approach

Three additive, non-fatal pieces closing #141/#142 and finishing the Auto model:
(1) a detect-only outbound dangling-reference lint check, surfaced in `lint` and
`status`; (2) message/detection-only awareness of the deliberately-dropped
`vectors.db` across `purge`/`status`/`doctor`; (3) purge auto-commit of its
post-rewrite live-tree cleanup, reusing Slice-2 `_autocommit` with a scoped
empty-diff guard. No canonical/model-layer behavior changes; every new path is
best-effort and never alters an existing exit code or the purge's irreversibility.

## Architecture Decisions

### Decision: ADR gate — no ADR required
**Choice**: Proceed without an ADR. **Alternatives**: full ADR. **Rationale**:
additive only; reuses already-decided patterns (rail-1 inbound orphan model for
#141, Slice-2 `_autocommit` contract, `normalize_link` identity resolver). No new
architectural axis, no rejected-fork worth recording beyond the strip-on-force
exclusion already captured in the proposal.

### Decision: purge empty-diff guard (load-bearing)
`vcs_git.commit_paths` (git.py:472-474) runs `git commit -m` **unconditionally**;
on an empty staged diff git exits non-zero → `GitError` "nothing to commit". So it
does **not** tolerate an empty diff. `expunge_paths` (filter-repo) already checks
out the rewritten `index.md`/`log.md`, so `_purge_clean_live_*` is frequently a
no-op → calling `_autocommit` directly would emit a spurious WARNING on every clean
purge. **Choice**: add a scoped helper `vcs_git.paths_dirty(root, paths)` running
`git status --porcelain -- <paths>` (`--` end-of-options guard, path-scoped like
`commit_paths`); purge calls `_autocommit` **only** when it returns `True`. If the
probe itself raises `GitError`, fall through and attempt `_autocommit` anyway (its
own catch keeps it non-fatal). **Alternatives**: (a) whole-tree `is_clean` — rejected,
a dirty unrelated host file yields a false positive → spurious WARNING; (b) mutate
`commit_paths` to no-op on empty diff — rejected, changes shared Slice-2 behavior
for callers that never hit it. **Rationale**: scoped, leaves `_autocommit`/`commit_paths`
byte-unchanged (proposal wants reuse-as-is), silent on the common no-op path.
- **Staged paths**: `["bundle/index.md", "bundle/log.md"]` (workspace-relative POSIX).
- **Message**: `openkos: purge <canonical_id>` (single) / `openkos: purge <canonical_id> (+N)`
  where N = `len(purge_ids) - 1` (source scope). Called once, after `_purge_rebuild_indexes`
  on the success path (main.py:1918). Both `index.md`/`log.md` are in `_commit_has_confidential`'s
  reserved skip-set → no false confidential NOTICE.

### Decision: #141 reference set (bounded)
**Choice**: outbound refs per doc = `relations:` targets (always) ∪ body bundle links
via `normalize_link`. `Relation.target` (okf.py:494) is already the canonical
`.md`-stripped id — identical to `LintDoc.identity`, no normalization needed. Body
links resolve through `normalize_link(target, doc.rel_dir)`, dropping `None`
(external/anchor/escape) and self-links. Existence set = `{d.identity for d in docs}`.
Flag any ref not in that set. **Alternatives**: relations-only (misses body links);
scan all markdown (scope creep). **Rationale**: symmetric mirror of rail-1's inbound
`check_orphans`, reuses the proven resolver, bounded and testable.

### Decision: status wiring for #141
`status` today renders only `okf.survey_bundle().findings` (conformance), which has
no dangling-reference concept. **Choice**: `status` additionally calls
`lint_check.collect_docs` + `check_dangling_targets` and folds rendered lines into the
existing "Needs attention" section, still read-only, still exit 0. **Alternatives**:
extend `survey_bundle` — rejected, that is OKF-validity vocabulary, dangling refs are
knowledge-health (lint) vocabulary. **Rationale**: keeps the layer separation stated in
lint.py's module docstring; both surfaces call the single lint function.

## Data Flow

    collect_docs(bundle_dir) ──→ [LintDoc(+relations)] ──┬─→ check_dangling_targets ─→ lint render
                                                         └─→ (status) Needs attention
    purge Phase B: expunge_paths ─→ _purge_clean_live_* ─→ _purge_rebuild_indexes(drops vectors.db)
                       └─→ paths_dirty? ──yes──→ _autocommit(index.md,log.md)  └─→ "run reindex" echo

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/lint.py` | Modify | `LintDoc.relations: tuple[str,...]`; populate in `collect_docs` via `okf.decode_relations` (catch `ValueError`→skip notice); `check_dangling_targets(docs)`; `LintReport.dangling`; `LintFinding.kind` gains `"dangling"` |
| `src/openkos/vcs/git.py` | Modify | Add scoped `paths_dirty(cwd, rel_paths) -> bool` (`git status --porcelain -- <paths>`) |
| `src/openkos/cli/main.py` `lint` | Modify | Compute + render new "Dangling references:" section |
| `src/openkos/cli/main.py` `status` | Modify | Fold dangling findings + absent-`vectors.db` line into "Needs attention" |
| `src/openkos/cli/main.py` `purge` | Modify | Post-cleanup `paths_dirty`-gated `_autocommit`; append degraded-dense-retrieval echo |
| `src/openkos/cli/main.py` `doctor` | Modify | New workspace-`vectors.db`-presence check (informational, workspace-only, SKIP outside) |

## Interfaces / Contracts

```python
# lint.py
def check_dangling_targets(docs: list[LintDoc]) -> list[LintFinding]:
    """Flag each outbound reference (relations target or body bundle link)
    naming a concept id absent from `docs`. kind="dangling"."""

# vcs/git.py
def paths_dirty(cwd: Path, rel_paths: Sequence[str]) -> bool:
    """True iff `git status --porcelain -- <rel_paths>` reports any change."""
```

## #142 messaging (exact)
- **purge**: after `_purge_rebuild_indexes` (both scope branches), echo
  `openkos purge: dense retrieval degraded (vectors.db dropped) — run \`openkos reindex\` to restore it.`
- **status**: if `not layout.vectors_db_path.exists()` → "Needs attention" line
  `Dense retrieval unavailable — run \`openkos reindex\` (vectors.db missing).`
- **doctor**: new check "Workspace vector index present" — `layout.vectors_db_path.exists()`;
  absent → informational fail, remediation `openkos reindex`; SKIP outside workspace.
  Distinct from check 7 (`probe_vec_loadable()` `:memory:` probe). Staleness deferred.

## Testing Strategy (Strict TDD — RED first)

| Layer | What | Approach |
|-------|------|----------|
| Unit (lint) | `check_dangling_targets`: dangling `relations:` target; dangling body link; valid ref present; link-form equivalence; self-link ignored; external/anchor ignored; corrupt `relations:` → skip notice | `LintDoc` fixtures, deterministic (no clock) |
| Unit (git) | `paths_dirty`: clean paths→False, modified path→True, unrelated dirty file→False (scoped), missing repo→GitError | real temp git repo (reuse Slice 1/2 fixtures) |
| Integration (lint/status) | `lint` renders "Dangling references"; `status` shows dangling + absent-vectors lines; both exit 0 | Typer `CliRunner`, temp workspace |
| Integration (purge) | clean cleanup→no commit, no WARNING (empty-diff tolerance); non-no-op cleanup→one commit staging only index/log; git failure→WARNING, purge still exit 0/irreversible; #142 echo present | real temp git repo |
| Integration (doctor) | absent vectors.db→informational fail; present→pass; outside workspace→SKIP | `CliRunner` |

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED tests |
|---|---|---|---|
| Documentation-like paths | N/A — no file-classification/execution added | — | — |
| Git repository selection | Applicable | `paths_dirty`/`_autocommit` use `cwd=root` (workspace root via `repo_root`), never `git -C`/relative | test: helper runs against workspace root repo |
| Commit state | Applicable | Empty index → `paths_dirty` False → skip commit (no spurious WARN); staged scoped via `git add -- <paths>` (in `commit_paths`); never `-a`/`-A` | tests: empty-diff skip; non-no-op single commit; scoped staging excludes unrelated dirty file |
| Push state | N/A — local-only, purge never pushes (AGENTS.md non-negotiable) | — | — |
| PR commands | N/A — no PR automation | — | — |

## Migration / Rollout
No migration. Additive functions/echo lines; revert = delete them. `vectors.db` stays
derived/gitignored (#142 is UX, not commit-the-binary). Purge's six fail-closed rails
and irreversibility unchanged.

## Sizing / Delivery guard
Estimated production ≈250-300 lines, tests ≈400. Combined authored diff risks the
400-line review budget but is likely under 800. **Recommend chaining two autonomous PRs**:
- **PR1 (#141)**: `check_dangling_targets` + `LintDoc.relations`/`collect_docs` +
  `lint` render + `status` dangling wiring (+ tests). Self-contained, no git changes.
- **PR2 (#142 + auto-commit)**: `paths_dirty` helper + purge auto-commit + purge echo +
  `status` absent-vectors line + `doctor` check (+ tests). Self-contained VCS/UX slice.

`sdd-tasks` MUST forecast against the 400-line budget and emit the guard lines.

## Open Questions
- None blocking.
