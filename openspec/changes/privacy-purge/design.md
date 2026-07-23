# Design: Privacy Purge — Right-to-Be-Forgotten, Slice 1 (Whole-File Expunge)

## Technical Approach

`purge <concept>` mirrors `forget`'s **Phase-A-pure / gate / Phase-B-write** shape but replaces the git-recoverable
delete with an **irreversible in-place git-history rewrite**. Phase A reuses `forget`'s resolution verbatim
(`_resolve_concept_path`, `find_provenance_descendants`, the reference-aware refusal) to build the purge set, then
resolves each source member's raw file and runs ALL fail-closed rails. NOTHING is written until Phase B, whose first
act — the `git filter-repo` rewrite — is the point of no return. The single git subprocess lives behind one new adapter
module so it is the only place `import subprocess` appears and the only place the bandit suppression is justified.
`forget`'s existing behavior is untouched.

## Architecture Decisions

### Decision: New git adapter module `src/openkos/vcs/git.py` (the FIRST subprocess seam)

**Choice**: A new `openkos.vcs` package with `git.py` — a thin git subprocess adapter, the single home of `import
subprocess`. One private `_run(argv, cwd)` wrapper (`subprocess.run(argv, cwd=..., capture_output=True, text=True,
check=False)`, NEVER `shell=True`) carries the sole `# noqa: S603, S607` with a comment; every public function builds a
**fixed argv list**. Public API:

```python
class GitError(RuntimeError): ...        # nonzero rc from a git/filter-repo call (stderr tail attached)
class GitUnavailable(RuntimeError): ...   # git or git-filter-repo not on PATH (FileNotFoundError)
def git_available() -> bool               # shutil.which("git") and `git --version` rc==0
def filter_repo_available() -> bool       # `git filter-repo --version` rc==0
def repo_root(cwd: Path) -> Path | None   # `git rev-parse --show-toplevel`; None when not a repo
def is_clean(cwd: Path) -> bool           # `git status --porcelain` output empty
def has_published_commits(cwd: Path) -> bool  # any ref under refs/remotes exists
def expunge_paths(cwd: Path, rel_paths: Sequence[str]) -> None  # the rewrite + finalize
```

**Exact invocation** (`expunge_paths`): write each workspace-relative POSIX path as a `literal:<p>` line to a
`NamedTemporaryFile`, then run the fixed argv
`["git", "filter-repo", "--force", "--invert-paths", "--paths-from-file", <tmp>]`, cwd=root. `--force` is required to
rewrite in place (filter-repo refuses a non-fresh clone otherwise); the `literal:` prefix guarantees each filename is
matched byte-for-byte with NO regex/glob interpretation, and no user data is ever shell-interpolated. **Finalize** (make
blobs truly unreachable): `git reflog expire --expire=now --all` then `git gc --prune=now`. Exit-code mapping:
`FileNotFoundError` on exec → `GitUnavailable` ("not installed"); nonzero rc → `GitError` carrying the captured stderr
tail (reported on stderr, never a raw traceback).

**Alternatives**: (a) under `state/` — rejected: `state/` is the derived-store layer; a git seam is a distinct adapter
boundary (hexagonal). (b) under `purge/` — rejected: `doctor` also needs the availability probes, so the adapter must be
shared, not verb-local. (c) invoke the `git-filter-repo` script directly — rejected: `git filter-repo` works whether
installed via pip or a system package and reads as a git subcommand.

**Rationale**: One auditable subprocess wrapper concentrates the entire attack surface of the most destructive verb into
~150 reviewable lines; RUF100 keeps the `# noqa` honest.

### Decision: git-filter-repo is a SYSTEM tool (PATH + doctor), NOT a runtime pip dependency

**Choice**: Do not add `git-filter-repo` to `[project].dependencies`. Add it (and it needs `git`) as a PATH requirement
verified by `doctor`, exactly as `ollama` is today (`shutil.which("ollama")`, config.py precedent). Add it to the **dev
group** so CI/tests have it.

**Alternatives**: runtime pip dep — rejected: it is invoked as a git subcommand, not imported; `git` itself can never be
a pip dep, so a doctor/PATH check is already required; and most users never purge, so coupling a niche destructive tool
into every install is wrong. **Rationale**: matches the established `ollama` PATH-probe + graceful-refuse pattern
exactly; keeps the runtime dependency set minimal.

### Decision: Index cleanup = physical DELETE (mandatory) + best-effort Ollama-free rebuild

**Choice**: Phase B `unlink(missing_ok=True)` on `.openkos/{fts,vectors,graph}.db` (row-DELETE leaves freelist-recoverable
pages — file-delete is the true-erasure requirement), then rebuild **FTS + graph only** via the existing
`state.reindex._reindex_fts`/`fts.write_fts_index` + `sqlite_graph.reindex_graph` (force=True) — neither needs Ollama.
`vectors.db` stays deleted for the next `openkos reindex` to re-embed lazily.

**Alternatives**: full `reindex` — rejected: it hard-depends on a running Ollama, which purge must not. **Rationale**: the
DELETE is the security-critical erasure (it physically removes purged concept text from `fts.db`); the rebuild is a
convenience over survivors. A rebuild failure does NOT fail the purge — the irreversible act already succeeded; print
that `openkos reindex` will restore search. The `bundle_manifest_hash` gate makes the forced FTS rebuild honest, and the
working tree no longer contains the purged concepts (filter-repo checks out the rewritten HEAD), so the rebuild reflects
the post-purge bundle.

## Phase Structure & Rail Order (ALL run before ANY write)

1. `require_workspace` (shared gate).
2. **Phase A (pure)**: `_resolve_concept_path` → purge set via `find_provenance_descendants` (`--scope self|source`,
   reused unchanged) → resolve raw paths (below) → build preview.
3. **Reference-aware refusal** (reuse `forget`'s gate 1: external/unverifiable inbound refs refuse unless `--force`) — FIRST per lock.
4. **Tool availability**: `git_available()` and `filter_repo_available()` — cheap, deterministic, no repo assumption; graceful refuse if absent.
5. **Git-root == workspace-root**: `repo_root(root)` realpath-compared to `root`; refuse on mismatch (nested/ancestor repo with unrelated content).
6. **Clean tree**: `is_clean(root)`; refuse if `git status --porcelain` non-empty.
7. **No published commits**: `has_published_commits(root)`; refuse if any `refs/remotes` ref exists.
8. **Typed confirmation**: print preview + residual warning; require typing the exact phrase `purge <canonical_id>`
   (cascade: `purge <root_id> (<N> concepts)`) via `typer.prompt`, or a matching `--confirm-phrase <phrase>` for
   non-TTY/tests; mismatch → abort exit 1, no write. NO `--auto` bypass of the phrase (irreversible).
9. **Phase B (irreversible)**: `expunge_paths(root, paths)` → finalize → delete `.openkos/*.db` → best-effort FTS+graph
   rebuild. No pre-purge backup (it would preserve what we erase).

Reference-aware first (lock), then cheapest-safest deterministic rails, then the human typed phrase LAST so a doomed
purge never reaches the confirmation step.

### Raw-path resolution

For each purge-set member, `okf.load_frontmatter`. A **Source** (has a `resource` key) contributes `resource`'s value
(`raw/<name>`) AND its own `bundle/<id>.md`. Validate `resource`: must start with `raw/`, no `..`, resolve under
`layout.raw_dir` — else refuse (`ValueError`). A **derived** concept (no `resource`) contributes only `bundle/<id>.md`.
Collect the `resource` path even if the raw file is already gone from the worktree — filter-repo matches historical
paths, so a raw file deleted from HEAD but present in history is still expunged; warn (not refuse) if a Source's
`resource` is absent/malformed.

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/vcs/__init__.py`, `src/openkos/vcs/git.py` | Create | Git subprocess adapter (probes + `expunge_paths` + finalize); sole `import subprocess` / `# noqa: S603, S607`. |
| `src/openkos/cli/main.py` | Modify | New `purge` verb (Phase A reuse + rails + typed confirm + Phase B); 2 new `doctor` checks (git, git-filter-repo). |
| `pyproject.toml` | Modify | Add `git-filter-repo` to the **dev group** (NOT runtime deps). |
| `docs/cli.md` | Modify | Document `purge`, irreversibility, rails, residual leak. |
| `tests/unit/vcs/`, `tests/**/cli/test_purge*.py` | Create | Real-git fixture infra + adapter + verb tests. |

## Residual-leak warning (exact copy)

```
WARNING: purge is IRREVERSIBLE. It rewrites ALL git history in place -- there is no
git-undo, no reflog, no backup. The raw source file(s) and concept file(s) listed
above will be permanently expunged from every commit.

This is NOT complete right-to-be-forgotten yet. The purged concept's id, title, and
catalog bullet -- and any prior forget tombstone -- REMAIN in the historical blobs of
index.md and log.md (shared files that must survive). Those identifiers stay
recoverable from git history until Slice 2 (content-scrub) closes this residual.
```

Printed at preview (before the typed phrase) and echoed on success.

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED tests |
|---|---|---|---|
| Documentation-like paths | N/A — no executable-file classification; paths derived from validated frontmatter, never PATH lookup | — | — |
| Git repository selection | Applicable | Authority = `git rev-parse --show-toplevel` realpath-compared to workspace root; always run in cwd, never `git -C <userpath>`; refuse on mismatch | non-git-root refuse; nested/ancestor-repo refuse |
| Commit state | Applicable | Refuse unless `git status --porcelain` empty (clean index + worktree) | dirty worktree refuse; staged-change refuse |
| Push state | Applicable | Refuse if any `refs/remotes` ref exists (published history) | remote-present refuse (bare repo + push) |
| PR commands | N/A — no PR automation | — | — |
| Subprocess argv safety (added) | Applicable | Fixed argv list, no `shell=True`; paths passed as `literal:`-prefixed `--paths-from-file` temp-file lines; `# noqa: S603, S607` | filename with shell metacharacters / leading `-` is expunged literally, no shell interpretation |

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Fixture infra | Real tmp git repo | `git init` in `tmp_path`; pin `GIT_AUTHOR_NAME/EMAIL`, `GIT_COMMITTER_NAME/EMAIL` (env) + `git config user.*`; init an openkos workspace, ingest a Source, commit. |
| Integration (happy) | Blobs truly gone | Capture raw + concept blob SHAs pre-purge; run purge (`--confirm-phrase`); assert paths absent from `git rev-list --objects --all`, `git reflog` empty, `git cat-file -e <sha>` rc≠0; worktree files gone; fts.db rebuilt without purged text. |
| Integration (rails) | Each fail-closed refuse | dirty tree; non-git-root (git init at parent, workspace in subdir); remote present (bare repo + push → `refs/remotes/origin/*`); tool missing (monkeypatch `filter_repo_available`→False); confirmation mismatch (wrong phrase); reference-aware (referenced concept, no `--force`). |
| Unit (adapter) | `vcs/git.py` probes + argv | `repo_root`/`is_clean`/`has_published_commits` against fixtures; `expunge_paths` argv shape (fixed list, `literal:` file). |
| Unit (doctor) | git / git-filter-repo checks | `[PASS]/[FAIL]` lines against monkeypatched probes. |

**Runner**: `uv run pytest`. These tests SHELL OUT to real `git` + `git-filter-repo` (slower, heavier than the existing
pure-text CLI tests). **CI must have `git` (present — checkout uses it) AND `git-filter-repo` (add an install step:
`pip install git-filter-repo` or `apt-get install git-filter-repo`).** Building/running purge in auto mode is safe —
every rewrite runs only on a throwaway tmp fixture; NEVER on a real user repo.

## Migration / Rollout

No data migration. **No post-execution rollback — irreversible by design.** Rollback = the fail-closed rails that refuse
BEFORE any write; a pre-execution abort is fully safe.

## Slice / PR Structure

Slice 1 only. Forecast ~430 prod + ~330 test ≈ **760 authored lines** — near the 800 budget (risk: **Medium-High**).
Keep ONE change, deliver as **two stacked PRs**:

- **PR1 — adapter + safety substrate (~400 lines)**: `vcs/git.py` (probes + `expunge_paths` + finalize), real-git
  fixture infra, adapter unit tests, `doctor` git/filter-repo checks. NO user-facing destructive verb — safe to merge
  alone.
- **PR2 — purge verb (~360 lines)**: `purge` wiring (Phase A reuse, rails, typed confirm, Phase B index nuke+rebuild),
  verb integration tests, `docs/cli.md`.

Each PR is independently reviewable and under the 400-line reviewer budget.

## Open Questions

- [ ] Exact `--confirm-phrase` string for cascade (`purge <root> (<N> concepts)`) — settle in tasks.
- [ ] Whether the best-effort FTS/graph rebuild should also run `git gc` verification — deferred; delete is the erasure.
