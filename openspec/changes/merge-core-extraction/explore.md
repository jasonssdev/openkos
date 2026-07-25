# Exploration: merge-core-extraction (#137, Slice 2b-i)

Behavior-preserving refactor: extract `merge`'s inline destructive orchestration
into a callable `merge_core(...)` so BOTH the existing `merge` command and the
future `adjudicate --apply` (Slice 2b-ii) can invoke it. NO behavior change, NO
new capability. Prerequisite for 2b-ii.

Full 2b exploration (covering 2b-ii apply + product decisions) is in Engram
`sdd/adjudicate-apply/explore`. This file captures the 2b-i subset.

## Current State
- `merge` command: `src/openkos/cli/main.py:2317-2614`. Its destructive logic is
  cleanly split into phases: argument/workspace/config gate → file reads →
  `plan_merge` (pure) → PREVIEW output → CONFIRM gate → ordered WRITES →
  link/relation rewrite → `merged_from` LEDGER write → `_autocommit`.
- Reusable pieces today: `bundle_merge.plan_merge` and the link/relation-rewrite
  finders. NO callable merge core — the orchestration is inline in the Typer
  command.
- `unmerge` round-trips to byte parity (verified in
  `tests/unit/cli/test_merge_roundtrip.py`). LIFO-tail enforcement on unmerge.

## Extraction boundary (for design to lock)
A callable `merge_core(root, survivor_id, absorbed_id, ...) -> <result>` that
performs the full destructive merge (reads, plan, writes, link rewrite, ledger)
but does NOT do the interactive CONFIRM or the human stdout. The `merge` command
becomes: gate → preview → confirm → `merge_core(...)` → echo result → autocommit
(or fold autocommit into the command, not the core — design decides). Goal:
`merge`'s observable behavior is BYTE-IDENTICAL after extraction.

## Safety net (the acceptance bar)
- `tests/unit/cli/test_merge.py` (~837 lines) + `tests/unit/cli/test_merge_roundtrip.py`
  pin `merge` behavior and the unmerge round-trip parity.
- ACCEPTANCE: extraction is behavior-preserving iff those files need ZERO edits
  and all pass unchanged. New tests may be added for `merge_core` directly.

## Scope
- 2b-i ONLY: pure extraction. No `adjudicate --apply`, no survivor/absorbed rule,
  no N>2 handling, no new user-facing behavior.
- No delta spec: the `privacy-purge`/merge capability contract is unchanged; the
  existing tests are the contract. (Design + tasks carry the real content.)

## Risks
- Touches the most heavily-tested destructive command — zero test edits to
  test_merge.py/test_merge_roundtrip.py is the behavior-preserving bar.
- Autocommit and confirm-gate placement (command vs core) must be decided so the
  core is reusable by a non-interactive/batch caller later without side effects.

## Product decisions DEFERRED to 2b-ii (not this slice)
1. Survivor/absorbed rule for a SAME group.
2. N>2 HIGH-group handling (merge is pairwise; unmerge is LIFO).
3. `y/N/skip` prompt semantics.
