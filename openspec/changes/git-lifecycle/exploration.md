# Exploration: git-lifecycle (openkos owns the git lifecycle — Auto model)

> SDD exploration artifact. Mirrors Engram topic `sdd/git-lifecycle/explore`.
> Decision context: the maintainer signed off (2026-07-24) on the "Auto" model —
> openkos owns the git lifecycle. This is settled; the proposal must not relitigate it.

## Current State

### 1. Git usage today: none at init, real (probe-only) usage at purge

- `openkos init` (`src/openkos/cli/main.py:116-209`) never touches git. Phase B writes, in order:
  `raw/` (mkdir), `bundle/index.md` + `bundle/log.md` (`bundle.create`, `src/openkos/bundle/bundle.py:15-28`),
  `AGENTS.md`, `openkos.yaml` (marker written last). No `.gitignore` is scaffolded anywhere
  (`grep -r gitignore src` returns nothing) and no `git init`/`subprocess` call exists in `init`.
- `src/openkos/vcs/git.py` is the sole `subprocess` adapter over `git`/`git-filter-repo`
  (module docstring, lines 1-17). `_run()` (line 52) is the ONE subprocess call site in the whole
  codebase, carrying the sole `# noqa: S603`. It exposes only PROBES + the history-rewrite primitive:
  `git_available`, `filter_repo_available`, `repo_root` (`git rev-parse --show-toplevel`),
  `is_clean` (`git status --porcelain`), `has_published_commits` (`git for-each-ref refs/remotes/`),
  and `expunge_paths` (`git filter-repo`). **There is no `commit`, `add`, or `git init` function anywhere
  in this module** — it is purely read-side probing plus the one destructive rewrite used by `purge`.
- `purge` (`cli/main.py:1400-1774`) is the only consumer of `vcs_git` today, via rails 2-5
  (lines 1636-1706): git/git-filter-repo availability, workspace-root-must-be-git-root, clean-tree,
  no-published-commits. **Because `init` never runs `git init`, `purge` is unusable out of the box** —
  a fresh workspace fails rail 3 (`repo_root` returns `None`) until the user manually runs `git init` +
  an initial commit. This is the exact prerequisite gap issue #143 reports.
- `doctor` reports `git_available()`/`filter_repo_available()` as informational checks only; it does not
  warn on missing `.gitignore`, uncommitted state, or non-repo workspace (only `purge` treats those as
  hard rails).

### 2. Naming collision to avoid

`src/openkos/lifecycle.py` already exists and is unrelated — it is the canonical-layer "effective-status"
predicate module (`deprecated_concept_ids`, `filter_hits`, MVP-3 status-aware-retrieval). A new
git-commit module MUST NOT be named `lifecycle.py` / `openkos.lifecycle`. `src/openkos/vcs/` (where
`git.py` already lives) is the natural home; extend `git.py` (`init_repo`/`commit_all`) or add a sibling
`src/openkos/vcs/commit.py`.

### 3. WorkspaceLayout already distinguishes canonical vs. derived paths

`config.py:86-148`: `config_path`, `agents_path`, `raw_dir`, `bundle_dir` are the four init-owned
canonical paths. `openkos_dir` (`.openkos/`) plus `vectors_db_path`, `fts_db_path`, `graph_db_path` are
documented as the engine's own cache paths, never written by `init`, created lazily on first open.
This is the authoritative canonical/derived boundary a `.gitignore` must encode.

## Write Surface (every mutating verb + the common Phase A / confirm-gate / Phase B pattern)

All mutating verbs in `cli/main.py` share one shape: **Phase A** (pure, builds full result in memory,
no writes) → **confirm gate** → **Phase B** (writes, catalog `index.md`/`log.md` LAST). This is the
single consistent hook point for auto-commit: after Phase B's last write succeeds, immediately before
the command's final success `typer.echo`.

| Verb | Confirm gate | Last Phase-B write | Line |
|---|---|---|---|
| `init` | none (workspace doesn't exist yet) | `openkos.yaml` (marker, last) | 116 |
| `ingest` | `--auto` / `review: false` / TTY confirm / non-TTY refuse | `index.md` then `log.md` | 446 |
| `forget` | same pattern | `index.md` then `log.md` | 851 |
| `purge` | typed confirmation phrase only, NO `--auto` (irreversible) | `expunge_paths` → live cleanup → index rebuild | 1400 |
| `relate` | same `--auto` pattern | `index.md`/`log.md` | 1778 |
| `merge` | same `--auto` pattern | source concept file + `index.md`/`log.md` | 2042 |
| `unmerge` | same `--auto` pattern | `index.md` then `log.md` | 2330 |
| `reconcile` | same module's helper set | bundle files + `index.md`/`log.md` | 2752 |
| `reindex` | derived-only (writes `.openkos/*.db`) — should NOT auto-commit | `.openkos/{fts,vectors,graph}.db` | 4415 |

`ingest`'s own docstring (lines 560-562) already says recovery is `git status` + `git checkout`/`git clean`
— the codebase already assumes git is set up, though nothing sets it up today. Strong internal evidence
the Auto model is the intended trajectory.

## Purge Bugs (#141, #142) — exact mechanics

**#142 — vectors.db dropped, never rebuilt, never prompted.** `_purge_rebuild_indexes`
(`cli/main.py:1352-1396`) deletes all three `.openkos/*.db`, then rebuilds ONLY `fts.db` and `graph.db`.
`vectors.db` is deliberately left deleted (rebuilding needs a running Ollama embedder, which `purge` must
never hard-depend on). The bug is not the deferral — it is that the success message (lines 1765-1774)
never mentions the degraded dense retrieval. Also flagged (verify in design): `query` doesn't warn on a
missing `vectors.db`, and `doctor`'s vector check may probe an in-memory connection rather than the
workspace's actual `vectors.db`.

**#141 — `--force` leaves dangling references lint/status never catch.** Rail 1 (lines 1610-1634) detects
inbound references and refuses UNLESS `--force`. With `--force`, Phase B removes the target and its own
catalog/log rows, but nothing visits the REFERRING documents to strip/flag the now-dangling `relations:`
entries or markdown links. Root cause: `lint.py`'s `check_orphans` (line 416) only detects the inbound
case; there is no outbound-target-existence check anywhere in `lint.py`, and `status` surfaces only §9
conformance findings. Both report "clean" on a bundle with broken relations.

**Transactional model**: purge's Phase B is already non-atomic by design (rewrite → best-effort cleanup →
best-effort rebuild, each independently caught/warned). Do NOT wrap the whole sequence in one git
transaction — `expunge_paths` (git filter-repo) IS the destructive op and rewrites history itself. Instead:
(a) pre-rewrite state must already be committed (rail 4 `is_clean` enforces this); (b) post-rewrite live-tree
cleanup should conclude with ONE auto-commit of the final consistent state; (c) that step is the natural
place for the #142 message ("run `openkos reindex`") and the #141 remedy (dangling-reference report or strip).

## .gitignore Contents

- **Canonical (commit)**: `openkos.yaml`, `AGENTS.md`, `raw/**` (immutable sources), `bundle/**` (markdown).
- **Derived (ignore)**: `.openkos/` (covers `fts.db`, `vectors.db`, `graph.db` uniformly), `.DS_Store`
  (named in #143 and #145). Consider `.codegraph/` defensively.

**vectors.db tension resolved**: the principle "everything reconstructible from canonical files (markdown +
SQLite + git)" makes `vectors.db` DERIVED — `state/reindex.py` rebuilds it from `bundle/**/*.md` (+ Ollama).
So `.openkos/` is unambiguously gitignored; #142 is a UX bug (silent capability loss), not a "commit the
binary index" question.

## Transactional Write + Auto-Commit — layering placement

Canonical layer (model/bundle/state) MUST stay git-agnostic — `bundle/*` must not import `vcs`. `vcs/git.py`
already sits as a sibling package. Auto-commit logic belongs at the **CLI orchestration layer**
(`cli/main.py`), called AFTER Phase B, mirroring where purge's post-rewrite cleanup already runs. Dependency
direction stays `cli → vcs` (peer to `cli → bundle`/`state`), never `bundle`/`state` → `vcs`.

**Confirm-gate interaction**: auto-commit fires strictly AFTER the existing gate and AFTER Phase B's writes
land — never in Phase A. A declined/refused confirm never reaches the commit step; no new gate needed.

**Partial failures / rollback**: every verb's Phase B is already documented as non-atomic across its own
writes. Auto-commit follows suit: attempt `git add -A && git commit` after Phase B; if the COMMIT fails
(no git identity, disk full), report a non-fatal WARNING on stderr — the file write already succeeded and is
recoverable via `git status`. Failing the whole command because the commit step failed would be a regression.
The proposal needs an explicit rollback-plan line (openspec config rule).

## Sensitivity Interaction (public|private|confidential)

`sensitivity.py` governs only LLM-send, not git. Auto-committing means confidential markdown lands in git
history — a strictly different exposure surface than an LLM call (durable, diffable, copyable, push-able if
the user later adds a remote). Genuine, not-yet-decided scope question: exclude confidential content from
auto-commit, or treat "committed to your own local git" as no worse than "written to disk in bundle/" (which
already happens unconditionally today)? Flag as an explicit open question.

## Workspace vs. source-repo distinction

The `openkos` SOURCE repo is separate from any user WORKSPACE. `openkos init` operates on `Path.cwd()` where
the user runs it (expected: an empty/new dir). Real risk (per #145): running `init` INSIDE an existing repo
must not create a nested repo or commit into the parent's history — `git init` must only fire when
`repo_root(cwd)` returns `None`. Purge's rail 3 (`found_root != root.resolve()`, line 1670) is a template.

## Scope / Slicing Recommendation (dependency-ordered)

1. **Slice 1 (first vertical, resolves #143)**: `init` gains git-setup — `git init` (only if not already
   inside a repo), scaffold `.gitignore` (`.openkos/`, `.DS_Store`), one initial commit. Small, isolated to
   `init` + `vcs/git.py`, easy to test (subprocess mock pattern exists via `_run`). Unblocks `purge` entirely.
2. **Slice 2 (resolves #145's "Auto", the bulk)**: auto-commit after every mutating verb's Phase B (ingest,
   forget, relate, merge, unmerge, reconcile — NOT reindex). One shared helper `vcs.git.commit_all(root, msg)`
   called from `cli/main.py`, with per-verb structured commit messages. Many symmetric call sites — forecast
   the diff carefully in sdd-tasks (7 verbs may exceed budget → chained per-verb PRs).
3. **Slice 3 (resolves #141 + #142, purge-specific, depends on Slice 1)**: explicit post-purge messaging when
   `vectors.db` was dropped + `status`/`doctor` awareness of a missing vector index (#142); and either a
   dangling-reference cleanup on `--force` OR a new `lint` outbound-target check (#141) — pick ONE.

`purge` stays OUT of Slice 2's generic auto-commit loop; its git handling belongs to Slice 3.

## Open Questions for the Proposal Phase

1. **Auto-commit stance**: unconditional, or an opt-out escape hatch (`autocommit: false` in `openkos.yaml`,
   mirroring `review:`)? "Auto" was chosen over manual/opt-in, but the escape-hatch question is unresolved.
2. **Commit message format & authorship**: exact structured format, and which git identity (rely on the
   user's global `git config`, or set a bot identity?). Missing identity is a common failure to degrade on.
3. **Existing-git workspace**: decision matrix for repo exists/doesn't × gitignore exists/doesn't ×
   tree clean/dirty. Respect an existing `.gitignore` rather than overwriting (#145 guardrail).
4. **`state/` derived stores commit status**: resolved (always gitignored) but pin as an explicit requirement.
5. **Reindex-after-purge (#142)**: message-only vs. interactive prompt vs. auto-reindex. Recommend
   message-only (a prompt breaks scripted purge; auto-reindex reintroduces the Ollama hard-dependency).
6. **Dangling-reference remedy (#141)**: strip-on-force vs. new lint check. Lint check is lower-risk
   (informational, composes with `status`); stripping is invasive (multi-file frontmatter rewrite).
7. **Confidential sensitivity vs. git history**: explicit in/out-of-scope decision, not a silent default.
8. **Sequencing**: Slice 2 is genuinely blocked on Slice 1. Make the dependency explicit.

## Ready for Proposal

Yes. Recommend the proposal open with **Slice 1** (`init` git setup, resolves #143) as its primary scope,
explicitly deferring Slices 2/3 to follow-on changes. A single proposal covering all three slices would
likely blow the review budget and should be split at the proposal stage, not discovered as an overrun in
sdd-tasks.
