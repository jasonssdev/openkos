# Design: adjudicate --apply-same (guarded batch merge) — CLOSES #137

## Technical Approach

Add a third `adjudicate` execution mode, `--apply-same`, that fuses every eligible
SAME 2-member group in one guarded batch. It reuses the shipped destructive machinery
verbatim (`_resolve_concept_path`, `prepare_merge`, `merge_core`, `_autocommit` +
ledger — `main.py:516-631`, `2698`, `2811`). To avoid duplicating the destructive
write ordering, three small shared helpers are extracted from the current
`_run_adjudicate_apply` monolith so both the interactive walk and the batch call the
SAME code. The batch is two-pass: build+print ONE aggregate preview, gate on a typed
exact-count confirmation, then execute sequentially. No new merge-core work (#163
already extracted it); no new LLM field (#147 was prompt-only — `verdict==SAME` +
`len(member_ids)==2` remains the only trustworthy programmatic gate).

## Architecture Decisions

### Decision: Share the per-pair body via extracted helpers (no duplicated write ordering)

**Choice**: Extract from `_run_adjudicate_apply` three helpers both modes call:
`_prepare_one_merge(root, layout, index_path, log_path, group)` (resolve both ids →
skip already-merged; `prepare_merge` → raise on bad input); `_format_merge_preview_line(prepared)`
(the existing "merge X into Y (sensitivity A->B, N rewrite(s), removes bundle/X.md)"
line, `587-592`); `_commit_one_merge(root, layout, index_path, log_path, prepared)`
(the `merge_core` + `_autocommit` write+commit unit, `602-622`). `_run_adjudicate_apply`
is refactored to call them with its `[y/N/skip]` prompt between prepare and commit.

**Alternatives considered**: Copy the loop body into `_run_adjudicate_apply_same`.

**Rationale**: A copied destructive path can silently diverge (write ordering, commit
path list, ledger). One shared `_commit_one_merge` guarantees byte-identical behavior;
`--apply` regression tests protect the refactor.

### Decision: Typed exact-count gate, mirroring `purge --confirm-phrase`

**Choice**: After the full preview, gate on typing the exact eligible count. Add a
companion option `--confirm-count <str>` (mirrors `purge`'s `--confirm-phrase`,
`2148-2169`). Resolution: if `--confirm-count` given → use it; elif `sys.stdin.isatty()`
→ `typer.prompt("Type N to proceed")`; else REFUSE (`stdin is not a TTY; re-run with
--confirm-count`, exit 1, zero writes). One comparison `typed.strip() == str(count)`;
empty Enter, non-numeric, or mismatch all fall through to abort (exit 1, zero writes).

**Alternatives considered**: Pure `isatty` gate + `typer.prompt` (like `--apply`).

**Rationale**: `purge` is the codebase's precedent for typed destructive confirmation;
its companion flag serves both scripted use and tests cleanly (CliRunner `input=` +
`isatty` is fragile for an accept-path test). Unattended application is possible ONLY
when the operator explicitly types the exact count on the command line — deliberate,
not blind — honoring "must not blindly merge." A bare non-TTY run refuses.

### Decision: Structural count up front; re-verify per pair at apply time

**Choice**: The preview count = number of eligible SAME 2-member groups (structural).
The typed count matches THAT. Pass 2 re-resolves each pair (`_resolve_concept_path`);
a member already absorbed by an earlier chained merge → skip (already-merged), so
`applied` may be < previewed. The summary reports both.

**Rationale**: Mirrors `--apply` re-resolution (`552-564`); the count cannot depend on
runtime chaining. Preview pass sees one pre-batch snapshot, so it is internally
consistent; chaining only reduces actual applied merges, never adds surprises.

## Data Flow

    adjudicate --apply-same
      → mutual-exclusion (apply|json → exit 2)  [before workspace gate]
      → require_workspace → read_config → find_candidates → adjudicate_candidates
      → _run_adjudicate_apply_same:
          Pass 1: filter SAME & len==2; _prepare_one_merge each (raise→exit 1);
                  echo _format_merge_preview_line each; echo "Total: N"
          Gate:   resolve typed count → == N ? proceed : abort (exit 1, ZERO writes)
          Pass 2: per pair re-resolve (skip already-merged) → _prepare_one_merge
                  → _commit_one_merge (OSError/ValueError → stop, keep prior, exit 1)
          Summary: applied N, skipped M (N>2, already-merged)

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/cli/main.py` | Modify | Extract 3 helpers; refactor `_run_adjudicate_apply`; add `_run_adjudicate_apply_same`; add `--apply-same`/`--confirm-count` options + mutual-exclusion + dispatch in `adjudicate()`. ~150-190 lines |
| `tests/unit/cli/test_adjudicate.py` | Modify | Batch tests mirroring the `--apply` fixture/monkeypatch matrix. ~250-350 lines |
| `openspec/specs/entity-resolution-adjudication/spec.md` | Modify | Delta: `--apply-same` requirements |

## Interfaces / Contracts

```python
def _prepare_one_merge(root, layout, index_path, log_path, group) -> PreparedMerge | None  # None = skip already-merged
def _format_merge_preview_line(prepared: PreparedMerge) -> str
def _commit_one_merge(root, layout, index_path, log_path, prepared: PreparedMerge) -> None  # merge_core + _autocommit
def _run_adjudicate_apply_same(root, layout, index_path, log_path, results, *, confirm_count: str | None) -> None
```

## Testing Strategy (strict TDD — RED first)

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | `--apply-same` + `--apply` → exit 2; `--apply-same` + `--json` → exit 2, no adjudicate call | CliRunner, assert stderr/exit |
| Unit | Eligibility: only SAME 2-member previewed; N>2 echoed skipped; DIFFERENT/UNCERTAIN absent | monkeypatch fakes |
| Unit | Aggregate preview: one rich line per pair + "Total: N" BEFORE any prompt/write | stdout assert |
| Unit | Gate exact count → applies all; commit count == N | `--confirm-count` = str(N), ledger/commit assert |
| Unit | Gate Enter/wrong/non-numeric → abort, exit 1, snapshot byte-identical, 0 commits | `--confirm-count` "", "9", "x" |
| Unit | Non-TTY, no `--confirm-count` → refuse exit 1, zero writes | default (CliRunner not a TTY) |
| Unit | Chained shared member → applied < previewed; summary already-merged count | overlapping groups fixture |
| Unit | Mid-batch `merge_core`/`prepare_merge` failure → stop, prior commits kept, exit 1 | monkeypatch raise |
| Unit | Reversibility: batch → N sequential LIFO `unmerge` round-trips | `_init_apply_workspace` git-backed |
| Unit | `--apply` regression (extracted helpers) unchanged | existing suite green |

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED test |
|---|---|---|---|
| Documentation-like paths | N/A: no path classification/execution |—|—|
| Git repository selection | N/A: reuses `_autocommit` root=`Path.cwd()`, no new selector |—|—|
| Commit state | Applicable: one commit per merge via shared `_commit_one_merge`; mid-batch failure keeps prior commits | Reuse `--apply` ordering verbatim | mid-batch-failure keeps-prior-commits test |
| Push state | N/A: no push |—|—|
| PR commands | N/A: no PR automation |—|—|

Destructive write gate (not a shell row, tracked here): typed exact-count over full
preview; empty/non-numeric/mismatch and bare non-TTY all abort with zero writes.
RED tests: gate-abort-byte-identical, non-TTY-refusal.

## Migration / Rollout

No migration. Reversible via N sequential LIFO `unmerge <survivor> <absorbed>` (newest
first per survivor chain; `main.py:3086-3115`). Code rollback: drop the `--apply-same`
branch; `--apply` path unaffected once the shared-helper refactor is retained.

## Open Questions

- [ ] None blocking. Batch-undo command remains an explicit non-goal (documented: N
      sequential unmerge).
