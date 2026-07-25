# Proposal: adjudicate --apply (interactive merge path)

## Intent

`adjudicate` is read-only: it prints SAME/DIFFERENT/UNCERTAIN verdicts but leaves
every fix as manual `merge` calls. Closing issue #137's "path to merge" (final
slice 2b-ii) means letting a human act on SAME verdicts in-place, guided and
reversible. Deliver `adjudicate --apply`: an INTERACTIVE, DESTRUCTIVE-but-
unmerge-reversible mode that walks the same verdicts and, per eligible group,
previews then prompts `Merge <absorbed> into <survivor>? [y/N/skip]`, executing
accepted merges via the 2b-i building blocks `prepare_merge`→`merge_core`.

## Scope

### In Scope
- `--apply` flag on `adjudicate` (new interactive mode).
- Eligibility filter: `verdict == SAME` AND exactly 2 members.
- Per-group preview (reuse `prepare_merge` data) shown BEFORE the prompt (#137).
- `y/N/skip` prompt (empty=N, skip=N, only `y` merges).
- Accepted merge → `merge_core` → per-merge `_autocommit` (independently reversible).
- Stale-id guard: verify both member paths still exist before each group.
- End summary: `applied X, skipped Y` with breakdown.

### Out of Scope (explicit)
- Guarded unattended batch (`--apply-same` no-confirm) — deferred, gated on #138.
- N>2 group merging (pairwise/LIFO) — skipped with message in v1.
- Any survivor/absorbed heuristic beyond alphabetical-first.
- Changing verdict/similarity logic or `merge`/`unmerge`.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `entity-resolution-adjudication`: add interactive `--apply` mode that executes
  SAME + 2-member merges via preview→prompt→apply, with a per-run stale-id guard.

## Approach

Add `--apply` to `adjudicate`. After the existing adjudication run (reuse Ollama
error tiers: exit 1/stderr), iterate `results` in order. Per SAME group: N>2 →
print `skipped (N>2, merge manually)`; else survivor=`member_ids[0]`,
absorbed=`member_ids[1]`. Before acting, re-verify both member paths exist; if a
member was absorbed by an earlier merge this run → `skipped (member already
merged)`. Call `prepare_merge` (no writes) to render a concise preview, then
prompt `Merge <absorbed> into <survivor>? [y/N/skip]` via `typer.prompt` with
manual parse. On `y`: `merge_core(prepared)` then `_autocommit` for that merge.
On mid-run `merge_core`/OSError: stop with a clear message (prior per-merge
commits stay reversible); do NOT silently continue after a destructive failure.

### LOCKED decisions
- `--apply` + `--json` → contradictory (interactive vs machine): reject, exit 2.
- `--apply` + `--same-only` → harmless no-op (apply is inherently SAME-only).
- Commit granularity: per applied merge (matches single-`merge` UX, granular unmerge).
- Preview shows what `prepare_merge` would fuse (survivor, absorbed, rewrites, removed).

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/cli/main.py` (`adjudicate` ~3790) | Modified | `--apply` flag + interactive walk |
| `src/openkos/cli/main.py` (`prepare_merge`/`merge_core`/`_autocommit`) | Reused | building blocks, unchanged |
| `tests/unit/cli/` | New | `--apply` prompt/eligibility/stale-id/summary tests |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Destructive command | Med | SAME+2-member only; preview-before-prompt; unmerge-reversible |
| Stale ids across sequential merges | Med | Re-verify member paths per group; skip-with-message |
| Prompt parsing (y/N/skip/empty) | Med | Dedicated tests; empty=N default |
| Mid-run write failure | Low | Stop on first failure; per-merge commits reversible |
| `--apply` + `--json` misuse | Low | Reject early, exit 2 |
| Empty/zero-eligible results | Low | Summary `applied 0, skipped …`; no crash |

## Rollback Plan

Each applied merge is a standalone commit reversible via `unmerge` (same
`merged_from` ledger). Revert the feature by dropping the `--apply` branch; the
building blocks and read-only `adjudicate` are untouched.

## Dependencies

- 2b-i (`merge-core-extraction`, PR #163, merged): `prepare_merge`/`merge_core`.

## Success Criteria

- [ ] `adjudicate --apply` previews then prompts per SAME 2-member group.
- [ ] `y` merges + commits; N/skip/empty continue.
- [ ] N>2 and already-merged groups skip with a message, never crash.
- [ ] `--apply --json` rejected (exit 2); `--apply --same-only` no-op.
- [ ] End summary tallies applied/skipped with breakdown.
- [ ] Existing `merge`/`unmerge`/`adjudicate` behavior unchanged.
