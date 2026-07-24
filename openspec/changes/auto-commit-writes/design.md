# Design: Auto-Commit Writes — Git Lifecycle Slice 2

## Technical Approach

Add ONE CLI-orchestration helper `_autocommit(root, paths, message)` in
`cli/main.py`, structurally cloned from `init`'s best-effort git block
(`cli/main.py:203-244`). Each of the six mutating verbs calls it exactly
once, on the success path, AFTER its Phase-B writes and final success
`typer.echo`. It reuses Slice 1's `repo_root`, `has_git_identity`, and
`commit_paths` (`vcs/git.py:375,444,460`) unchanged. Dependency direction
stays `cli → vcs`; the canonical layer imports no `vcs`.

## Architecture Decisions

### Decision: No new ADR

**Choice**: Ship without an ADR. **Alternatives**: author an ADR for the
auto-commit policy. **Rationale**: purely additive, reversible (remove
helper + 6 call sites, no schema/on-disk change), and it reuses the pattern
Slice 1 already decided and shipped (`init`'s non-fatal scoped-commit block).
The two policy calls (commit-everything + one NOTICE; no opt-out) were
already resolved by the maintainer in the proposal. Nothing crosses a new
architecture boundary.

### Decision: Confidential detection reads frontmatter, not `blocks_llm_send`

**Choice**: Detect confidential content by reading each staged concept
file's `sensitivity` frontmatter and testing equality against the canonical
top rank `okf.SENSITIVITY_ORDER[-1]` (`"confidential"`,
`model/okf.py:39`): `str(meta.get("sensitivity","")).strip() ==
"confidential"`. **Alternatives**: the proposal's loosely-referenced
`sensitivity.blocks_llm_send(value, threshold="confidential")`.
**Rationale**: `blocks_llm_send` (`sensitivity.py:60`) is a *fail-closed
LLM-send gate*: it returns `True` for missing/blank/unreadable/unrecognized
values (treating absence as confidential). This NOTICE is *transparency*,
not a security gate — a source with no `sensitivity` field must NOT raise a
false "confidential committed" alarm. The correct signal is an explicit
`confidential` value, so we compare the parsed value directly against the
canonical vocabulary rather than borrow the fail-closed predicate.

## Helper Contract

```python
def _autocommit(root: Path, paths: Sequence[str], message: str) -> None:
    repo = vcs_git.repo_root(root)
    if repo is None:
        typer.echo("openkos: WARNING -- not a git repository; skipped "
                   "auto-commit (writes are on disk).", err=True); return
    if not vcs_git.has_git_identity(root):
        typer.echo("openkos: WARNING -- git identity unset; skipped "
                   "auto-commit (writes are on disk).", err=True); return
    try:
        vcs_git.commit_paths(root, paths, message)
    except (vcs_git.GitError, OSError) as exc:
        typer.echo(f"openkos: WARNING -- auto-commit did not complete "
                   f"({exc}); run `git status` to inspect.", err=True); return
    if _commit_has_confidential(root, paths):
        typer.echo("openkos: NOTICE -- this commit includes content marked "
                   "'sensitivity: confidential'. openkos commits to LOCAL git "
                   "only and never pushes to a remote.", err=True)
```

Never raises; never alters the verb's exit code. `_run`'s
`UnicodeDecodeError→GitError` mapping (`vcs/git.py:352`) is already covered
by the `except`. The NOTICE fires only after a *successful* commit and is
naturally at-most-once per invocation (each verb calls `_autocommit` once) —
no persisted flag.

`_commit_has_confidential(root, paths)` iterates `paths`, skips
`bundle/index.md`/`bundle/log.md`, `raw/**`, and any path missing on disk
(deletions), reads the rest, and returns `True` on the first frontmatter
`sensitivity == "confidential"`.

## Per-Verb Wiring

Call placed after each verb's final success `typer.echo`, outside the Phase-B
`try`. `imported`/canonical ids below are the same values the verb already
computed.

| Verb | `paths` (rel, POSIX) | Message |
|---|---|---|
| `ingest` (`main.py:838-845`) | `imported_paths` (`raw/<name>`, `bundle/sources/<slug>.md`, each `bundle/<link_dir>/<slug>.md`) + `bundle/index.md`, `bundle/log.md` | `openkos: ingest <name> (+<len(derived_plans)> concepts)` |
| `forget` (`main.py:1021-1032` Phase B) | `bundle/index.md`, `bundle/log.md`, + `bundle/<member>.md` for `member in purge_ids` (deletions stage via `git add`) | `openkos: forget <canonical_id>` (append ` (+<n-1> descendants)` when `len(purge_ids)>1`) |
| `relate` (`main.py:2003-2005`) | `bundle/<source_canonical>.md`, `bundle/log.md` | `openkos: relate <source_canonical> -> <target_canonical> (<rel_type>)` |
| `merge` (`main.py:2339-2373`) | `bundle/index.md`, `bundle/log.md`, each `bundle/<rel>` in `touched_files`, `bundle/<survivor_canonical>.md`, `bundle/<absorbed_canonical>.md` (deletion) | `openkos: merge <absorbed_canonical> into <survivor_canonical>` |
| `unmerge` (`main.py:2635-2661`) | `bundle/index.md`, `bundle/log.md`, each `bundle/<rel>` in `rewritten_files` and `relation_rewrite_files`, `bundle/<absorbed_canonical>.md` (recreated), `bundle/<survivor_canonical>.md` | `openkos: unmerge <absorbed_canonical>` |
| `reconcile` (`main.py:3105-3108`) | `bundle/<canonical_a>.md`, `bundle/<canonical_b>.md`, `bundle/log.md` | symmetric: `openkos: reconcile <canonical_a> <-> <canonical_b>`; directional: `openkos: reconcile <winner_canonical> supersedes <loser_canonical>` |

## Confirm-Gate Interaction

Every verb's confirm gate (`typer.confirm(abort=True)` / non-TTY refusal)
raises `typer.Exit` BEFORE Phase B; a Phase-B failure raises `typer.Exit`
too. Reaching the trailing `_autocommit(...)` therefore proves the gate
passed and Phase B landed. Declined confirm ⇒ no write, no commit.

## Data Flow

    verb Phase A ─▶ confirm gate ─▶ Phase B writes ─▶ success echo ─▶ _autocommit
                        │ decline                                          │
                        ▼ Exit(1) (no commit)               repo_root / identity? ──no──▶ WARN, return
                                                                           │ yes
                                                        commit_paths (scoped add) ──err──▶ WARN, return
                                                                           │ ok
                                                        confidential? ──▶ one NOTICE

## Testing Strategy (Strict TDD, reuse Slice 1 harness)

| Layer | What | Approach |
|---|---|---|
| Unit (helper) | not-a-repo → WARN+return; identity unset → WARN+return; `GitError`/`OSError` from `commit_paths` → WARN, no raise; success commits; confidential → one NOTICE; non-confidential/missing → no NOTICE | monkeypatch `vcs_git.commit_paths`/`repo_root`/`has_git_identity`; capture `capsys` stderr |
| Integration (per verb ×6) | verb leaves a clean tree via one scoped commit with the pinned message; declined confirm → no commit; not-a-repo/no-identity/commit-error → WARN + verb exit 0 | real temp repo via `tmp_path`+`init_repo`; isolate identity with `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` env; assert `is_clean(root)` + `git log -1` subject |
| Integration (scope) | an unrelated dirty file outside `paths` stays uncommitted after the verb | seed a dirty untracked/modified file; assert it is still dirty (scoped `add`, never `-A`) |
| Integration (derived) | `reindex`/`.openkos/*.db` never appear in any auto-commit | run a verb, assert `.openkos/` absent from `git show --stat HEAD` |

RED tests written before production per the threat-matrix rows below.

## Threat Matrix (VCS automation — applicable)

| Boundary | Applicability | Design response | Planned RED test |
|---|---|---|---|
| Documentation-like paths | N/A: no path classification/execution added | — | — |
| Git repository selection | Applicable | `repo_root(root)` resolves the real toplevel; `None` ⇒ non-fatal skip, never `git -C`/relative guessing | not-a-repo → WARN + verb success, no commit |
| Commit state | Applicable | scoped `git add -- <paths>` only (`commit_paths`, never `-a`/`-A`); unrelated dirt untouched; commit failure non-fatal | dirty-unrelated-file-untouched; commit-error → WARN + exit 0 |
| Push state | N/A: local-only, no push/remote ever (non-negotiable #145) | — | — |
| PR commands | N/A: no PR automation | — | — |

## Migration / Rollout

No migration. Additive; existing workspaces unaffected. Rollback = delete
`_autocommit` + 6 call sites.

## Sizing Note (400/800 budget)

Production ≈150 lines (helper ~25, `_commit_has_confidential` ~15, six
~5-line call sites, message construction). Tests dominate: helper unit +
6 verbs × ~4 scenarios ≈ 450-650 authored lines even with parametrization.
Total likely **550-750**, with real **Medium-High** risk of exceeding the
400-line review budget in one PR. Recommend `sdd-tasks` chain **two slices**:
PR A = helper + `_commit_has_confidential` + NOTICE + wire `ingest`/`forget`/
`relate` (+ their tests); PR B = wire `merge`/`unmerge`/`reconcile` (+ tests).
Each slice is independently shippable with a clean tree and rollback.

## Open Questions

- None blocking. (Cascade-forget message suffix `(+N descendants)` is a
  cosmetic choice `sdd-tasks` may finalize.)
