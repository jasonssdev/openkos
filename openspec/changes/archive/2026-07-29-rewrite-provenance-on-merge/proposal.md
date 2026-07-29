# Proposal: Rewrite inbound provenance on merge (issue #230)

## Intent

`merge` retargets every inbound pointer to the absorbed id — body links and typed `relations:` — but never `provenance:`. Third-party concepts derived from the absorbed object keep a pointer to an id that is no longer addressable, so `forget --scope source` under-expands, `set-sensitivity` propagation skips them, and `set-sensitivity` only prints a stderr WARNING. Provenance is an AGENTS.md non-negotiable; a dangling provenance edge silently breaks it.

## Decisions

**A. Retargeting is correct (endorsed).** Merge already makes the absorbed id non-addressable, and `merged_from` + `unmerge` — not the orphaned pointer — is the audit trail. A pointer to a non-existent id is not a better record of "derived from X"; it is a broken one. Leaving links and relations retargeted while provenance dangles is an inconsistency, not fidelity.

**B. Retarget-then-dedupe.** Replace `absorbed_id` with `survivor_id` in place, then dedupe first-occurrence-wins. A list already naming both collapses to one `survivor_id` entry at the EARLIER of the two positions; all other entries keep relative order. Mirrors `build_merged_document`'s union rule. No naive substring replacement.

**C. Unmerge precedence: provenance > relations > links.** A file in `provenance_rewrites` is restored EXCLUSIVELY from its provenance snapshot; a file in `relation_rewrites` but not `provenance_rewrites` keeps today's rule; links reverse only for files in neither. Safe because all three scanners read the SAME pre-merge `other_files` snapshot, so the two whole-file snapshots are byte-identical — asserted by test, not assumed.

**D. Third-party sensitivity restore: out of scope.** Verified: merge never writes third-party sensitivity (propagation lives in `set-sensitivity`, ADR-0009), so unmerge has nothing to restore. Lowering is also gated one-way (ADR-0008/0010). Consequence to note in design: after retargeting, a later raise on the survivor now reaches these files — intended.

**E. Delivery: 2 stacked PRs** (~750 lines vs. 800 budget). PR1 primitives + ledger v3 (~350). PR2 CLI wiring + unmerge + docs (~400).

## Scope

### In Scope
- `bundle/provenance.py`: `find_inbound_provenance_rewrites` / `apply_provenance_rewrites` / `reverse_provenance_rewrites` — whole-file snapshot, drift-checked, shaped like `relations.py` (NOT offset-exact).
- **Scanner is UNGATED by `type`.** `query --save` writes `provenance=[cited concept ids]`, so any absorbed concept can orphan provenance. Named requirement with its own test.
- `prepare_merge`: third scanner over the SAME `other_files` snapshot (zero extra bundle walks, per #195/#216); `merge_core`: third link in the per-file transform chain.
- Ledger `MERGE_LEDGER_SCHEMA_V3` + `okf.ProvenanceRewrite`; `MergePlan`/`UnmergePlan` gain `provenance_rewrites`. Reader still accepts v1 and v2.
- `unmerge`: symmetric drift-checked reversal under decision C.
- Ships as ONE change: ledger and plan shapes are shared by both directions; splitting would leave merges that cannot round-trip.

### Out of Scope / Non-Goals
- No `lint check_dangling_targets` provenance axis — detection, not repair. Follow-up.
- No change to `set-sensitivity`'s whole-bundle warning scope (**#232 — do not conflate**).
- No change to `find_provenance_descendants` closure semantics, nor to `bundle/references.py` (detect-only, `forget`-owned).

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `entity-resolution-merge`: new requirement "Third-party inbound provenance retargets to the survivor" (ungated by type, retarget-then-dedupe); Reversibility Ledger gains v3 + `provenance_rewrites` with v1/v2 back-compat; Unmerge gains the three-way precedence rule.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/bundle/provenance.py` | Modified | find/apply/reverse trio; docstring scope widened |
| `src/openkos/model/okf.py` | Modified | `ProvenanceRewrite`, v3 schema, encode/decode |
| `src/openkos/bundle/merge.py` | Modified | `provenance_rewrites` through plan/unplan |
| `src/openkos/cli/main.py` | Modified | `prepare_merge`, `merge_core`, `unmerge` |
| `docs/cli.md` | Modified | merge/unmerge: provenance is retargeted too |
| `tests/unit/{bundle,cli}` | Modified | trio, ledger v1/v2/v3 decode, non-Source target, round-trip |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Source-gating the scanner (misses `query --save` provenance) | Med | Explicit requirement + dedicated test |
| Duplicate survivor entry after retarget | Med | Dedupe test (decision B) |
| Ledger v3 rollback asymmetry | Med | See rollback plan |
| Unmerge corrupts a file touched by 2–3 rewrite kinds | Med | Decision C + byte-identity assertion test |

## Rollback Plan

Reverting the code is `git revert` of both PRs. It is NOT purely additive: a **v3 ledger entry written before the revert is unreadable by restored v2 code**, which fails closed on the unknown schema rather than silently reinterpreting it. Recovery: unmerge affected pairs BEFORE reverting, or hand-edit the entry's `schema` to v2 and drop `provenance_rewrites` (the retarget then stays applied and must be reversed manually). Design MUST state the v3 decode failure mode explicitly.

## Dependencies

None. Builds on the v1→v2 relations work (ADR-0005).

## Success Criteria

- [x] Merging away a concept leaves zero dangling provenance entries anywhere in the bundle.
- [x] A non-Source absorbed concept's inbound provenance retargets identically.
- [x] A third-party file naming both ids ends with one survivor entry, no duplicate, order preserved.
- [x] merge → unmerge restores byte-identical pre-merge bundle, including files touched by all three rewrite kinds.
- [x] v1 and v2 ledger entries still unmerge exactly.
- [x] `uv run pytest`, `ruff`, `mypy --strict` clean; coverage ≥ 90.
