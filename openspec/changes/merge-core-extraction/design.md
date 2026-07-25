# Design: merge-core Extraction (Slice 2b-i)

## Technical Approach

Behavior-preserving extraction of `merge`'s inline orchestration
(`cli/main.py:2317-2614`) into reusable, non-interactive callables. The confirm
gate sits BETWEEN the preview (needs plan data) and the writes (reuse the SAME
in-memory data). A single reads+plan+writes `merge_core` cannot pause mid-call
for the command's confirm without either (a) re-reading/re-planning (TOCTOU +
timestamp drift, not byte-identical) or (b) swallowing the confirm (violates
non-interactive). So we split Phase A and Phase B into two functions; both are
non-interactive, raise plain exceptions, and emit no stdout. Command owns
gate/resolve, preview echo, confirm, success echo, and `_autocommit`.

## Phase inventory of `merge` (contract to preserve)

| Lines | Step | Destination |
|-------|------|-------------|
| 2422-2449 | root/layout, `require_workspace`, `_resolve_concept_path` x2, same-id check; error `refusing to merge` exit 1 | **stays in command** |
| 2451 | `now = datetime.now(UTC)` | command computes, passes to `prepare_merge` |
| 2453-2519 | read cfg + 4 texts + `other_files`; link/relation finders; `plan_merge`; recompute `merge_relations` for preview; `remove_index_entry`; `insert_log_entry`; error `failed while preparing` exit 1 | **→ `prepare_merge`** (raises; command wraps wording) |
| 2526-2546 | preview echoes (sensitivity, dropped/deduped, rewritten files, index/log/survivor/absorbed) | **stays in command** (reads `PreparedMerge`) |
| 2548-2557 | confirm gate: `--auto` skip / `cfg.review` skip / TTY `typer.confirm(abort)` / non-TTY refuse exit 1 | **stays in command** |
| 2559-2596 | ordered writes: index, log, `rewritten_texts` per touched file, survivor LAST, remove absorbed; error `failed while writing` exit 1 | **→ `merge_core`** (raises; command wraps wording) |
| 2598-2602 | success echo | **stays in command** |
| 2604-2614 | `_autocommit` | **stays in command** |

## Architecture Decisions

### Decision: Two functions, not one `merge_core`
**Choice**: `prepare_merge()` (Phase A) + `merge_core()` (Phase B writes).
**Alternatives**: single reads+plan+writes core.
**Rationale**: confirm gate is between preview and writes over shared in-memory
state read ONCE. One core would double the reads/plan (TOCTOU + timestamp drift)
or absorb the confirm. Split preserves single-read/single-timestamp semantics and
serves the batch caller (preview→own confirm→apply).

### Decision: `_autocommit` stays in the COMMAND
**Choice**: core does workspace mutations only; NO VCS side effect.
**Alternatives**: fold `_autocommit` into core.
**Rationale**: lets `adjudicate --apply` commit once after N merges (or per-merge)
and keeps core decoupled from `vcs_git`. Command still calls `_autocommit` with the
identical path list + message → single-`merge` behavior byte-identical.

### Decision: Confirm gate stays in the command
**Choice**: core is non-interactive; command previews from `PreparedMerge`, then
runs the unchanged gate before `merge_core`. UX byte-identical.

### Decision: Live in `cli/main.py` as module-level functions
**Choice**: keep in `main.py`, not `bundle/merge.py`.
**Rationale**: `bundle/merge.py` is documented PURE (no I/O). Orchestration needs
`config`/`fsio`/index/log and reuses CLI-local `_resolve_concept_path` and
`_apply_link_rewrite_idempotently` (the latter imported by `test_merge.py`).
A new module = circular-import risk + relocations for zero benefit. `adjudicate`
imports from the same module. No over-engineering.

### Decision: Error wording stays in the command
Core functions raise `OSError`/`ValueError`; command catches and formats the exact
pinned strings (`refusing`/`preparing`/`writing`). `cfg` is read inside
`prepare_merge` and returned so a config failure still routes through `preparing`.

## Interfaces / Contracts

```python
@dataclass(frozen=True)
class PreparedMerge:
    survivor_path: Path; absorbed_path: Path
    survivor_canonical: str; absorbed_canonical: str
    plan: bundle_merge.MergePlan
    new_index_text: str; new_log_text: str
    other_files: dict[str, str]
    link_rewrites: ...; relation_rewrites: ...
    rewritten_files: list[str]; relation_rewritten_files: list[str]
    touched_files: list[str]
    removed: int
    dropped_self_loops: ...; deduped_collisions: ...
    sensitivity_before: str; sensitivity_after: str
    review: bool  # cfg.review, consumed only by the command's confirm gate
    now: datetime

@dataclass(frozen=True)
class MergeResult:
    survivor_canonical: str; absorbed_canonical: str
    touched_files: list[str]
    committed_paths: list[str]  # index/log/touched/survivor/absorbed

def prepare_merge(bundle_dir, index_path, log_path, survivor_path, absorbed_path,
                  survivor_canonical, absorbed_canonical, root, *, now) -> PreparedMerge
def merge_core(bundle_dir, index_path, log_path, prepared) -> MergeResult
```

## Ledger invariance

`merge_core` writes `prepared.plan.merged_survivor` verbatim (current line 2592);
the `merged_from` ledger is embedded in that text, built by `plan_merge(merged_at=
now.isoformat())`. No shape/byte change → `test_merge_roundtrip.py` parity holds.

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| Regression | `test_merge.py` (837) + `test_merge_roundtrip.py` | ZERO edits; run unchanged (the gate). `_apply_link_rewrite_idempotently` stays importable from `cli.main`. |
| Unit (new) | `test_merge_core.py`: `prepare_merge` + `merge_core` called directly, no CliRunner/confirm | assert identical writes + ledger bytes; injected `now` reflected in ledger+log; `prepare_merge` writes nothing (`_snapshot` untouched); core makes NO git commit; raises on bad input. |

## Threat Matrix

N/A — behavior-preserving refactor adds no routing, shell, subprocess, or
executable-file surface. It REMOVES VCS coupling from the core (autocommit stays
in the command), a safety property, not a new threat. Existing `_autocommit`
behavior for single `merge` is unchanged.

## Migration / Rollout

No migration. Pure move/extract; no schema, ledger, or on-disk format change.
Rollback = revert the commit.

## Open Questions

- [ ] None blocking. Confirm during apply that `cfg.read_config` failure has no
  dedicated test pinning it to the `preparing` wording (design keeps it there
  regardless).
