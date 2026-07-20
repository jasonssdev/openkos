# Design: Merge Edge Rewiring (MVP-2 Slice 2a)

## Technical Approach

Make `merge` edge-aware and fully reversible, replacing the slice-1 REFUSE guard. Three
edit surfaces: (1) OUTBOUND + SELF-LOOP + survivor-side dedupe — computed in
`build_merged_document`, reversible for free via existing `survivor_before`/`absorbed_snapshot`
whole-file bytes; (2) INBOUND third-party retarget — the only genuinely new ledger surface,
handled by a new `bundle/relations.py` trio with whole-file byte-exact snapshots; (3) preview +
guard removal. Ledger bumps v1→v2; decode reads both. See spec (`sdd/merge-edge-rewiring/spec`).

## Architecture Decisions

### D1 — v2 ledger field: whole-file snapshots
**Choice**: `MERGE_LEDGER_SCHEMA_V2 = "openkos.merge_ledger/v2"`; add `relation_rewrites:
list[RelationRewrite]` to `MergeLedgerEntry`, where `RelationRewrite = (file: str, snapshot: str)`
and `snapshot` is the third-party file's FULL bytes BEFORE this merge. `plan_merge` always writes
V2. `decode_merge_ledger_entry` branches on schema: **V1** → `relation_rewrites = []` (absent field
= behaves as today); **V2** → required key (`KeyError`→`ValueError`); anything else →
`unsupported schema`. Strict-fail posture preserved for malformed V2 entries.
**Rejected**: structured per-entry offset records (D5 of explore) — list-of-dicts has no byte-offset
disambiguator; whole-file snapshot is trivially exact and mirrors `survivor_before` ethos.

### D2 — Outbound merge lives in `build_merged_document` (atomic with guard removal)
**Choice**: Add `RELATIONS_KEY` to `_SPECIAL_KEYS` (so the generic list-union can NEVER carry a
dangling `target: absorbed_id` or self-loop) AND add a dedicated relations block via a new pure
helper `okf.merge_relations(survivor, absorbed, *, survivor_id, absorbed_id) -> RelationMergeResult`.
`build_merged_document` gains a `survivor_id` param (needed to detect self-loops). The
`_SPECIAL_KEYS` change and the guard removal are **ONE atomic unit** — never split.
**Rejected**: compute in `merge.py` — you must still add `RELATIONS_KEY` to `_SPECIAL_KEYS` anyway
(else relations get dropped), so co-locating the logic keeps the "no dangling edge / no self-loop"
invariant provable in one function, beside `sensitivity`/`merged_from`.

`merge_relations` rule: union(survivor ∪ absorbed); retarget `target==absorbed_id → survivor_id`;
DROP resulting `target==survivor_id` (self-loop, consistent with `relate`'s self-id refusal);
DEDUPE identical `(target,type)`; re-emit via `encode_relations` (already `(target,type)`-sorted).
Returns `(merged, dropped_self_loops, deduped_collisions)` for the preview.

### D3 — `bundle/relations.py` trio (mirrors `links.py`)
```python
find_inbound_relation_rewrites(files, *, absorbed_id, survivor_id) -> list[RelationRewrite]
apply_relation_rewrites(text, *, file, survivor_id, absorbed_id, rewrites) -> str
reverse_relation_rewrites(text, *, file, rewrites) -> str
```
`find`: for each third-party file whose decoded `relations:` has any `target==absorbed_id`, record
`RelationRewrite(file, snapshot=<original full text>)`; malformed frontmatter → skip (broad
`except`, mirrors old `find_relation_conflicts`/`lint.collect_docs`). `apply`: no-op unless `file`
in `rewrites`; else decode → retarget → drop self-loops → dedupe → `encode_relations` → re-dump.
`reverse`: return the recorded `snapshot` (absolute whole-file restore — no offset math needed).

### D4 — Overlapping multi-merge LIFO (proof)
Whole-file snapshots NEST correctly because each entry snapshots the file exactly as it was
immediately BEFORE that merge, and reverse is an absolute overwrite. Survivor S; file F targets
both A and B; merge A then merge B:

| Step | F on disk | ledger snapshot taken |
|---|---|---|
| start | `[→A, →B]` (F0) | — |
| merge A (A→S) | `[→S, →B]` (F1) | entry_A: F0 |
| merge B (B→S, collide→dedupe) | `[→S]` (F2) | entry_B: F1 |
| unmerge B (tail) restores entry_B | `[→S, →B]` (F1) | — |
| unmerge A (tail) restores entry_A | `[→A, →B]` (F0) | — |

Byte-exact at each step; the dedupe collapse in merge B is fully recovered because unmerge B
restores the whole F1 (which still held distinct `→B`). LIFO-tail enforcement (`plan_unmerge`) plus
absolute restore = no positional ambiguity. **Ordering constraint**: keep the deterministic
`sorted(rglob("*.md"))` third-party scan; `encode_relations`'s `(target,type)` sort makes each
`apply` output canonical/deterministic.

### D5 — Link/relation overlap on the same third-party file
A file may carry both an inbound body-link AND an inbound relation to absorbed. Forward: apply both
(disjoint regions — body vs frontmatter). Reverse: the relation whole-file snapshot restores the
ENTIRE file, so unmerge MUST **skip `reverse_link_rewrites` for any file present in
`relation_rewrites`** (else the offset check fails on already-restored bytes). Rule: relation
snapshot restore takes precedence; link-only files still reverse by offset.

## File Changes
| File | Action | Description |
|---|---|---|
| `model/okf.py` | Modify | `SCHEMA_V2`, `RelationRewrite`, ledger field + encode/decode (v1/v2), `merge_relations`, `build_merged_document` (`survivor_id` param, `RELATIONS_KEY` in `_SPECIAL_KEYS`) |
| `bundle/relations.py` | Create | find/apply/reverse third-party inbound relation rewrites |
| `bundle/merge.py` | Modify | DELETE `RelationConflict`/`find_relation_conflicts`; `plan_merge` takes `relation_rewrites`, writes V2, exposes drop/dedupe report; `plan_unmerge`/`UnmergePlan` carry `relation_rewrites` |
| `cli/main.py` | Modify | REMOVE guard hook; scan+apply inbound retargets; non-silent preview; unmerge reverse + link-skip rule |
| `docs/adr/0005-*.md` | Create | v2 ledger + rewiring-supersedes-guard |
| tests (`test_merge*`, `test_unmerge`, `cli/test_merge`, `test_relations`) | Modify/Create | drop guard tests; add outbound/self-loop/dedupe/roundtrip/overlapping-LIFO/v1-back-compat |

## Preview (non-silent, D2/D5)
Extend the existing `merge` preview block: `  - drop self-loop: {t} ({type})` per dropped
self-loop; `  ~ dedupe collision: {t} ({type})` per deduped collision; `  ~ bundle/{f} (retarget
relation to survivor)` per relation-rewritten file — matching the current `~/-` bullet style.

## Guard Removal
`RelationConflict` + `find_relation_conflicts` DELETED; CLI Phase-A hook (main.py 1281-1296)
removed. No residual refusal path — `merge` always succeeds. `find_relation_conflicts` is NOT
repurposed; the preview drop/dedupe report comes from `merge_relations`'s return, not a detector.

## Testing Strategy
| Layer | What | Approach |
|---|---|---|
| Unit | `merge_relations` (union/retarget/self-loop/dedupe); v1/v2 decode; trio | RED-first table tests |
| Unit | `build_merged_document` never emits `target:absorbed_id` or self-loop | direct assertion |
| Property/Integration | merge→unmerge byte-parity incl. dropped/deduped edges; **overlapping-LIFO** 2-merge; pre-2a v1 entry unmerges | fixture bundles |

## Threat Matrix
N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or
process-integration boundary. Existing fail-closed drift cases (unmerge restore collision,
malformed third-party frontmatter → skip) are retained as test requirements.

## Migration / Rollout
No destructive migration. V2 additive; pre-2a v1 `merged_from` entries decode and unmerge exactly
as today. Rollback = revert the PR chain (restores slice-1 guard); already-merged bundles stay
reversible.

## PR-Slice Plan (auto-chain / feature-branch-chain)
Atomic unit = **tracker→main merge** (human checkpoint), not any child PR. Children stack on
`feature/merge-edge-rewiring`; main never sees a partial change.

| PR | Boundary | ~Lines |
|---|---|---|
| PR1 | ADR-0005 + `build_merged_document` relations special-case + `merge_relations` + DELETE guard + remove CLI hook + unit tests (**mandated atomic pair**) | ~300 |
| PR2 | v2 ledger (`RelationRewrite`, `SCHEMA_V2`, encode/decode v1+v2) + `bundle/relations.py` trio + unit tests | ~350 |
| PR3 | CLI merge/unmerge wiring (inbound apply, reverse, link-skip rule, non-silent preview) + roundtrip/overlapping-LIFO/v1-back-compat tests | ~380 |

**New ADR warranted**: yes — ADR-0005 records the durable v2 on-disk ledger contract and the
refuse→rewire behavior reversal, amending ADR-0004's anticipated "slice 2" and extending ADR-0002.

## Open Questions
- None blocking. (Empty-relations key omission preserves "absent relations key is valid".)
