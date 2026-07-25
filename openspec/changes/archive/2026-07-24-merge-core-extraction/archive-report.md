# Archive Report: merge-core-extraction (#137, Slice 2b-i)

**Change**: merge-core-extraction
**Arc**: #139→#137 curation-output (Slice 2b-i)
**PR**: #163 (squash `4c2d49a`)
**Date**: 2026-07-24
**Verdict**: PASS (0 CRITICAL / 0 WARNING / 0 SUGGESTION)

## What shipped
Behavior-preserving extraction of the `merge` command's inline orchestration
(`src/openkos/cli/main.py`) into two non-interactive, module-level functions so
a future `adjudicate --apply` (Slice 2b-ii) can reuse the exact destructive path:

- `prepare_merge(...) -> PreparedMerge` — Phase A: reads + `plan_merge` +
  preview data + config read. Raises `OSError`/`ValueError`; no writes.
- `merge_core(prepared) -> MergeResult` — Phase B: ordered writes + link/relation
  rewrite + `merged_from` ledger. Raises; **zero VCS side effect** (no autocommit).

The `merge` command keeps `gate → preview → confirm → prepare/core → success echo
→ _autocommit`, byte-identical. `_autocommit` and the confirm gate stay in the
command so the core is safely callable by a batch caller. Error wording
(`refusing`/`preparing`/`writing`) and exit codes stay in the command.

## Design rationale (two functions, not one)
Shared state (plan, index/log text, link/relation rewrites, `now`) is computed
once in Phase A; the confirm gate sits between; Phase B reuses it. A single
reads+plan+writes core would force double-reading (TOCTOU + timestamp drift → not
byte-identical) or absorb the confirm (breaks non-interactive). The split also
fits 2b-ii: `--apply` will `prepare → show what will fuse → confirm → core → commit`.

Minor deviation from design: `survivor_canonical`/`absorbed_canonical` frozen
fields added to `PreparedMerge` because `MergeLedgerEntry` exposes only
`absorbed_id` — same data the command already had, no new I/O, ledger bytes
unchanged.

## Scope
- No delta spec: the `merge` capability contract is unchanged; the existing
  `test_merge.py` + `test_merge_roundtrip.py` suite IS the contract.
- No change to `merge`'s observable behavior, `unmerge`, similarity/verdict logic,
  or dependencies.

## Verification
- Acceptance bar met: **ZERO edits** to `test_merge.py` (~837 lines) and
  `test_merge_roundtrip.py`; both pass unchanged (`git diff` empty).
- New `test_merge_core.py` (5 tests): direct exercises of the extracted functions,
  incl. a VCS-no-side-effect assertion (`.git/HEAD` unchanged, tree dirty) and an
  `unmerge` round-trip.
- Full suite: **2007 passed**; ruff/format/mypy clean.
- Independent verify: PASS. Bounded **resilience** review: 0 findings — write
  ordering (index→log→rewrites→survivor→remove absorbed), `now` placement,
  TOCTOU handoff, partial-failure boundary, and ledger bytes all confirmed
  byte-for-byte preserved.

## Follow-up: Slice 2b-ii (`adjudicate --apply`)
Interactive apply, blocked on maintainer product decisions:
1. Survivor/absorbed rule for a SAME group (`member_ids` is the only zero-cost
   signal — alphabetically sorted, no provenance/connectedness without extra I/O).
2. N>2 HIGH-group handling (merge is pairwise; unmerge is LIFO). Recommended
   first version: skip N>2 groups in `--apply`.
3. `y/N/skip` prompt semantics (default `N` on empty input).
