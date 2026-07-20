# Tasks: Merge Edge Rewiring (MVP-2 Slice 2a)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1030 total (PR1 ~300, PR2 ~350, PR3 ~380) |
| 400-line budget risk | Medium (PR2/PR3 approach budget; likely `size:exception` candidates) |
| Chained PRs recommended | Yes |
| Suggested split | PR1 → PR2 → PR3 (feature-branch-chain) |
| Delivery strategy | auto-chain |
| Chain strategy | feature-branch-chain |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: Medium

**Merge-readiness gate**: tracker `feat/merge-edge-rewiring` only offers merge-to-main after PR3 lands on it — the full feature (outbound fix, inbound trio, v2 ledger, unmerge reverse, D5 precedence) must be complete first; intermediate PR1/PR2 branch states never reach main.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Guard removal + outbound/self-loop/dedupe merge, atomic pair | PR1 (base: `feat/merge-edge-rewiring`) | `uv run pytest tests/unit/model/test_okf.py tests/unit/bundle/test_merge.py -k "relations or merged_document"` | N/A — pure unit-level model logic, no CLI/IO harness needed | Revert PR1; restores slice-1 guard, no downstream dependents yet |
| 2 | v2 ledger + `bundle/relations.py` trio | PR2 (base: PR1 branch) | `uv run pytest tests/unit/model/test_okf.py tests/unit/bundle/test_relations.py -k "ledger or relation_rewrite"` | N/A — pure codec/pure-function trio, no CLI wiring yet | Revert PR2 only; PR1's outbound fix stays intact, trio unused until PR3 |
| 3 | CLI merge/unmerge wiring, preview, round-trip/LIFO/v1-compat | PR3 (base: PR2 branch) | `uv run pytest tests/unit/cli/test_merge.py tests/unit/cli/test_merge_roundtrip.py tests/unit/cli/test_unmerge.py` | `uv run openkos merge <survivor> <absorbed>` against a fixture bundle with overlapping third-party relations | Revert PR3 only; PR1+PR2 remain inert (unwired) until re-applied |

## Phase 1: PR1 — Outbound Merge Fix + Guard Removal (ATOMIC)

- [x] 1.1 RED: `tests/unit/model/test_okf.py` — `merge_relations` union/retarget/self-loop-drop/collision-dedupe table test (spec: "Outbound relations move to the survivor", "Resulting self-loop is dropped, non-silently", "Duplicate edge is deduped, non-silently").
- [x] 1.2 RED: `tests/unit/model/test_okf.py` — `build_merged_document` never emits `target:absorbed_id` or a self-loop when absorbed/survivor both carry `relations:` (spec: "Merge of an edge-bearing object always succeeds").
- [x] 1.3 GREEN: `src/openkos/model/okf.py` — add `merge_relations(survivor, absorbed, *, survivor_id, absorbed_id) -> (merged, dropped_self_loops, deduped_collisions)`.
- [x] 1.4 GREEN: `src/openkos/model/okf.py` — add `RELATIONS_KEY` to `_SPECIAL_KEYS`; add `survivor_id` param to `build_merged_document`; call `merge_relations` and assign result onto merged metadata.
- [x] 1.5 RED: `tests/unit/bundle/test_merge.py` — assert `RelationConflict`/`find_relation_conflicts` no longer exist / no residual refusal path (spec: "Merge of an edge-bearing object always succeeds").
- [x] 1.6 GREEN: `src/openkos/bundle/merge.py` — DELETE `RelationConflict` and `find_relation_conflicts`.
- [x] 1.7 GREEN: `src/openkos/cli/main.py` — remove the Phase-A refuse-guard hook in `merge` (former lines ~1281-1296).
- [x] 1.8 GREEN: remove/replace obsolete guard-refusal tests in `tests/unit/cli/test_merge.py`.
- [x] 1.9 DOCS: create `docs/adr/0005-merge-edge-rewiring.md` (v2 ledger contract + refuse→rewire reversal; amends ADR-0004, extends ADR-0002).
- [x] 1.10 VERIFY: `uv run pytest tests/unit/model/test_okf.py tests/unit/bundle/test_merge.py` green; `ruff check` + `mypy --strict` clean on touched files.

## Phase 2: PR2 — v2 Ledger + `bundle/relations.py` Trio

- [ ] 2.1 RED: `tests/unit/model/test_okf.py` — `decode_merge_ledger_entry` accepts a V1 entry (no `relation_rewrites` key) and returns `relation_rewrites=[]` (spec: "Pre-slice-2a v1 ledger entry still unmerges exactly").
- [ ] 2.2 RED: `tests/unit/model/test_okf.py` — `decode_merge_ledger_entry` requires `relation_rewrites` on a V2-schema entry, raising `ValueError` on a malformed/missing V2 entry.
- [ ] 2.3 RED: `tests/unit/model/test_okf.py` — `encode_merge_ledger_entry`/`encode_merged_from` round-trip a `RelationRewrite(file, snapshot)` list.
- [ ] 2.4 GREEN: `src/openkos/model/okf.py` — add `MERGE_LEDGER_SCHEMA_V2`, `RelationRewrite` dataclass, `relation_rewrites: list[RelationRewrite]` field on `MergeLedgerEntry`; update encode/decode to branch V1 (absent→`[]`) vs V2 (required key) vs unsupported schema.
- [ ] 2.5 RED: `tests/unit/bundle/test_relations.py` (new) — `find_inbound_relation_rewrites` records one `RelationRewrite` per third-party file whose `relations:` targets `absorbed_id`, snapshot = original full text (spec: "Third-party inbound relations retarget to the survivor").
- [ ] 2.6 RED: `tests/unit/bundle/test_relations.py` — `find_inbound_relation_rewrites` skips a file with malformed frontmatter (mirrors old `find_relation_conflicts` skip behavior).
- [ ] 2.7 RED: `tests/unit/bundle/test_relations.py` — `apply_relation_rewrites` retargets, drops self-loops, dedupes collisions, re-emits via `encode_relations`; no-op on a file not in `rewrites`.
- [ ] 2.8 RED: `tests/unit/bundle/test_relations.py` — `reverse_relation_rewrites` restores the recorded whole-file snapshot exactly.
- [ ] 2.9 GREEN: `src/openkos/bundle/relations.py` (new) — implement `find_inbound_relation_rewrites`, `apply_relation_rewrites`, `reverse_relation_rewrites` per D3.
- [ ] 2.10 GREEN: `src/openkos/bundle/merge.py` — `plan_merge` accepts/produces `relation_rewrites`, always writes `MERGE_LEDGER_SCHEMA_V2`; `plan_unmerge`/`UnmergePlan` carry `relation_rewrites`.
- [ ] 2.11 VERIFY: `uv run pytest tests/unit/model/test_okf.py tests/unit/bundle/test_relations.py tests/unit/bundle/test_merge.py` green; `ruff check` + `mypy --strict` clean.

## Phase 3: PR3 — CLI Wiring, Preview, Round-Trip/LIFO/D5

- [ ] 3.1 RED: `tests/unit/cli/test_merge_roundtrip.py` — merge→unmerge byte-parity for a survivor with outbound relations (re-materializes dropped self-loop and deduped collision) (spec: "Unmerge restores every touched file, including drops/dedupes").
- [ ] 3.2 RED: `tests/unit/cli/test_merge_roundtrip.py` — **overlapping-LIFO**: two sequential merges (`merge A→S`, then `merge B→S`) both retarget the SAME third-party file `F`; unmerge B then unmerge A restores `F` to each exact intermediate byte state (F1, then F0) (spec: "LIFO unmerge across overlapping third-party files"; design D4 proof).
- [ ] 3.3 RED: `tests/unit/cli/test_unmerge.py` — **D5 double-reverse trap**: a third-party file with BOTH an inbound body-link and an inbound relation to absorbed; unmerge SKIPS `reverse_link_rewrites` for that file (relation whole-file snapshot takes precedence) — assert no offset-mismatch failure and byte-exact restore via snapshot only.
- [ ] 3.4 RED: `tests/unit/cli/test_unmerge.py` — pre-slice-2a v1 `merged_from` entry (no `relation_rewrites` key) still decodes and unmerges survivor/absorbed/catalog exactly as before (spec: "Pre-slice-2a v1 ledger entry still unmerges exactly").
- [ ] 3.5 RED: `tests/unit/cli/test_merge.py` — confirm preview shows `- drop self-loop: {t} ({type})`, `~ dedupe collision: {t} ({type})`, and `~ bundle/{f} (retarget relation to survivor)` non-silently before any write (spec: "Resulting self-loop is dropped, non-silently", "Duplicate edge is deduped, non-silently").
- [ ] 3.6 GREEN: `src/openkos/cli/main.py::merge` — scan third-party files via `find_inbound_relation_rewrites`, apply via `apply_relation_rewrites`, extend confirm preview with drop/dedupe/retarget bullets.
- [ ] 3.7 GREEN: `src/openkos/cli/main.py::unmerge` — reverse `relation_rewrites` (whole-file restore) and apply the link-skip rule: skip `reverse_link_rewrites` for any file present in `relation_rewrites`.
- [ ] 3.8 REGRESSION: run existing merge/unmerge byte-parity tests (ADR-0002) unmodified — confirm they stay green (no relation surface touched).
- [ ] 3.9 VERIFY: `uv run pytest` (full suite) green; `ruff check` + `mypy --strict` clean; coverage held at 90% branch.
- [ ] 3.10 MERGE-READINESS GATE: confirm PR1+PR2+PR3 all merged onto `feat/merge-edge-rewiring` before offering the tracker for human merge-to-main.

## Phase 4: Cleanup

- [ ] 4.1 Update `docs/adr/README.md` index with ADR-0005 entry.
- [ ] 4.2 Remove any leftover guard-era comments/dead imports (`RelationConflict` references) across `src/openkos/`.
