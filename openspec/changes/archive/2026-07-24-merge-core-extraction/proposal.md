# Proposal: merge-core Extraction (Slice 2b-i)

## Intent

The `merge` command's destructive orchestration is inlined in the Typer command
(`src/openkos/cli/main.py:2317-2614`). Slice 2b-ii (`adjudicate --apply`) needs to
run the exact same destructive merge non-interactively, but there is no callable
core to reuse — only the inline command body. Extract a callable `merge_core(...)`
so BOTH `merge` and the future `adjudicate --apply` invoke one shared destructive
path. This slice adds NO user-facing behavior; it is a pure prerequisite refactor.

## Scope

### In Scope
- Introduce a callable `merge_core(...)` performing the destructive steps: file
  reads, `plan_merge`, ordered writes, link/relation rewrite, `merged_from` ledger.
- `merge_core` does NOT do the interactive confirm or human stdout; it returns the
  data the preview needs (plan, dropped self-loops, deduped collisions, rewritten
  files, removed) instead of echoing.
- Refactor `merge` to: gate → preview → confirm → `merge_core(...)` → echo result.
- Thread an injectable `now: datetime` so preview and write share one timestamp.
- Design decides: `_autocommit` and confirm-gate placement (command vs core) and
  where the core lives (resolution/bundle module vs staying in cli). Prefer moving
  pure orchestration out of the CLI IF module structure supports it — no over-engineering.

### Out of Scope
- `adjudicate --apply` interactive mode (Slice 2b-ii).
- Survivor/absorbed heuristic, N>2 group handling, `y/N/skip` prompt (2b-ii, pending
  maintainer product decisions).
- ANY change to `merge`'s observable behavior, similarity/verdict logic, or `unmerge`.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
None. The merge capability contract is unchanged — this is a behavior-preserving
refactor. **Recommend SKIPPING `sdd-spec`**: the existing test suite IS the contract.
Route proposal → design → tasks → apply → verify. The design phase carries the real
content (the exact extraction boundary and `merge_core` signature).

## Approach

The `merge` body is already phase-shaped (reads+plan / preview / confirm / ordered
writes / autocommit). Extract phases "reads+plan+writes+ledger" into `merge_core`,
leaving preview, confirm gate, and stdout echo in the command. The command keeps its
own echo statements calling the SAME data the core returns (not a new formatting path),
and preserves error-message wording exactly (pinned by tests). Exact signature and the
core's module home are deferred to design.

## Acceptance Bar (behavior-preserving)

ZERO edits to `tests/unit/cli/test_merge.py` (~837 lines) and
`tests/unit/cli/test_merge_roundtrip.py`; all pass unchanged. Any test needing
modification is a RED FLAG that the extraction changed behavior — not a fix-forward.
New tests MAY be added for `merge_core` directly.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/cli/main.py:2317-2614` | Modified | `merge` command — extraction site |
| `src/openkos/resolution` or `bundle/merge.py` | New/Modified | possible home for `merge_core` (design decides) |
| `tests/unit/cli/test_merge_core*.py` | New | direct `merge_core` tests |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Behavior drift in most-tested destructive command | Med | Full `test_merge.py` + roundtrip suite unchanged = the gate |
| Autocommit/confirm placement makes core unsafe for batch caller | Med | Design decides placement so core has no surprising side effects |
| Ledger shape drift breaks `unmerge` round-trip parity | Low | `test_merge_roundtrip.py` pins byte parity; core writes identical ledger |
| Moved production LOC approaches 800 review budget | Low | Pure move/extract; flag in tasks if forecast is high, else one PR |

## Rollback Plan

Revert the extraction commit. `merge` returns to its inline form; no schema, ledger,
or on-disk format changed, so no data migration or cleanup is required.

## Dependencies

- None external. Slice 2b-ii (`adjudicate --apply`) depends on THIS slice landing first.

## Success Criteria

- [ ] `merge_core(...)` is callable independent of Typer/stdin/stdout.
- [ ] `merge`'s observable behavior (preview, confirm, exit codes, writes, ledger,
      autocommit, unmerge parity) is byte-identical.
- [ ] `test_merge.py` and `test_merge_roundtrip.py` pass with ZERO edits.
- [ ] New direct `merge_core` tests added and passing.
