# Verify Report: Rewrite inbound provenance on merge (issue #230)

**Change**: rewrite-provenance-on-merge
**Mode**: Full artifacts (proposal/spec/design/tasks) + Strict TDD verify
**Scope verified**: WHOLE change — PR1 (merged into `main` @ `3f26c98`) + PR2 (this branch, `feat/merge-retargets-provenance`)
**Verdict**: **PASS**

## Completeness

- Tasks: 46/46 marked `[x]` (22 PR1, 24 PR2) — confirmed against actual code state, not just checkbox trust.
- PR1 primitives confirmed **unmodified** by PR2: `git diff main...HEAD -- src/openkos/bundle/provenance.py src/openkos/model/okf.py src/openkos/bundle/merge.py` → 0 lines.

## Command Evidence

| Command | Result |
|---|---|
| `uv run pytest -q` | **2565 passed**, exit 0, 91.47s |
| `uv run pytest --cov=openkos --cov-branch -q` | **97.62% total** branch coverage (gate 90%), exit 0 |
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 146 files already formatted |
| `uv run mypy .` | Success: no issues found in 146 source files |
| `git diff --stat main...HEAD` | 7 files, +930/-38 (968 total) |

## Hard-Checks (per launch prompt)

1. **Scanner NOT type-gated** — PASS. `find_inbound_provenance_rewrites` (`src/openkos/bundle/provenance.py:142`) has no `type` branch; docstring explicitly states it. CLI-level proof `test_merge_absorbing_non_source_concept_still_retargets_third_party_provenance` (`tests/unit/cli/test_merge.py:879`) merges a `type: Decision` absorbed concept and asserts `derived_metadata["provenance"] == ["concepts/survivor"]`. Judged non-vacuous: if a `type == "Source"` filter were added, retarget would not happen and the absorbed concept (`type: Decision`) would leave `provenance` unchanged — assertion would fail. Primitive-level ungated proof also exists in PR1 (T2, `test_provenance.py`).

2. **End-to-end functional proof** (`test_merge_retarget_then_later_set_sensitivity_raise_reaches_descendant`, `tests/unit/cli/test_merge.py:951`) — PASS, non-vacuous. Setup: `derived` starts at `sensitivity: private` with `provenance: ["sources/absorbed"]` (the ABSORBED id, set BEFORE merge). `private` (rank 1) < `confidential` (rank 2) per `SENSITIVITY_ORDER = ("public", "private", "confidential")` (`okf.py:39`), so the later raise is a genuine raise, not a no-op. Pre-fix, `derived`'s provenance would still name the deleted `sources/absorbed` id and `find_provenance_descendants` would never reach it, so `derived_metadata_final["sensitivity"]` would remain `"private"` — the final assertion `== "confidential"` could actually fail. Test also asserts the intermediate state (`derived_metadata_after_merge["provenance"] == ["sources/survivor"]`) confirming the retarget itself, not just the end state.

3. **Retarget-then-dedupe, first-occurrence-wins, ordering** — PASS. `test_apply_provenance_rewrites_retarget_then_dedupe_matrix` (`tests/unit/bundle/test_provenance.py:417`) parametrizes exactly the spec's cases: `[absorbed]→[survivor]`, `[absorbed,x,survivor]→[survivor,x]`, `[survivor,x,absorbed]→[survivor,x]`, `[absorbed,x,absorbed.md]→[survivor,x]` (`.md`-variant dedupe), `[x,y]` unchanged. Asserts `metadata["provenance"] == after` — an exact ordered list equality, not membership — so an ordering regression (e.g., appending survivor at the end instead of the earlier position) would be caught.

4. **Zero extra bundle walks** — PASS. Counting wrapper `_counting_rglob` (`tests/unit/cli/test_merge.py:935`) is a plain function (`def`, not `def ...: yield from ...`) that appends to `calls` eagerly before returning the generator from `original(...)` — matches the design's required shape, not the vacuous `yield from` form. Production code (`src/openkos/cli/main.py:3805-3833`) builds `other_files` via ONE `rglob` call, and all three scanners (`find_inbound_link_rewrites`, `find_inbound_relation_rewrites`, `find_inbound_provenance_rewrites`) read the same dict — confirmed by direct source inspection, not just the test's own claim.

5. **Unmerge precedence provenance > relations > links** — PASS. Production partitioning at `src/openkos/cli/main.py:4372-4383` computes `provenance_rewrite_files`, then `relation_rewrite_files` excluding those, then `rewritten_files` (links) excluding both — matches design exactly. `test_unmerge_three_way_precedence_provenance_over_relations_over_links` (`tests/unit/cli/test_unmerge.py:357`) asserts byte-identity via `Path.read_bytes()` on the REAL on-disk bundle files pre-merge vs. post-unmerge, not a trusted shared-snapshot argument. Separately, `test_merge_core_provenance_and_relation_snapshots_byte_identical_to_pre_merge` (T4, `tests/unit/cli/test_merge_core.py:534`) reads the survivor's `merged_from` tail off disk via `okf.decode_merged_from(metadata)[-1]` and asserts `entry.provenance_rewrites[0].snapshot == entry.relation_rewrites[0].snapshot == pre_merge_text` — the on-disk ledger, not an in-memory value.

6. **Round-trip** — PASS. `test_merge_then_unmerge_round_trip_covers_all_three_rewrite_kinds` (`tests/unit/cli/test_merge_roundtrip.py:456`) merges then unmerges a bundle with all-three/provenance-only/relations-only/links-only files and asserts full bundle byte-parity (modulo `log.md`) via `_assert_byte_parity_except_log`.

7. **Ledger v1/v2/v3 readability + encode guard** — PASS. `test_decode_merge_ledger_entry_v1_schema_absent_provenance_rewrites_defaults_empty` and the `_v2_schema_absent_provenance_rewrites_defaults_empty` counterpart (`tests/unit/model/test_okf.py:1801`, `:1816`) confirm both older schemas decode with `provenance_rewrites=[]`. `test_encode_merge_ledger_entry_v1_schema_rejects_provenance_rewrites` and the V2 counterpart (`tests/unit/model/test_okf.py:1682`, `:1697`) confirm the encode guard raises `ValueError` for BOTH V1 and V2 carrying non-empty `provenance_rewrites`, matching design's "extend the existing V1 guard to V1 *or* V2."

8. **ADR-0011 status** — PASS. Frontmatter `status: Proposed` (line 5), body `**Status:** Proposed` (line 17), `docs/adr/README.md:49` index row reads `Proposed`. Task 9.3 explicitly confirms this stays untouched by apply, deferring the flip to the archive phase.

9. **PR1 primitives untouched by PR2** — PASS. `git diff main...HEAD -- src/openkos/bundle/provenance.py src/openkos/model/okf.py src/openkos/bundle/merge.py` is empty (0 lines).

## Spec Requirement / Scenario Coverage

| Requirement | Scenario | Status | Evidence |
|---|---|---|---|
| Reversible Inbound-Provenance Rewiring | Merge absorbing a Source retargets a derived object's provenance | PASS | `test_provenance.py` T1 (PR1) |
| " | Merge absorbing a NON-Source concept also retargets | PASS | `test_merge.py:879` (CLI) + PR1 T2 (primitive) |
| " | Third-party file naming both ids collapses to earlier position | PASS | `test_provenance.py:417` matrix |
| " | No additional bundle walk | PASS | `test_merge.py:912` (T5) |
| Retargeted Provenance Reaches Later Sensitivity Propagation | Raise on survivor reaches provenance-retargeted descendant | PASS | `test_merge.py:951` |
| Reversibility Ledger (`merged_from`, v3) | v1 and v2 entries still readable after v3 bump | PASS | `test_okf.py:1801,1816` |
| Unmerge Achieves Round-Trip Parity | Unmerge restores pre-merge provenance exactly | PASS | `test_merge_roundtrip.py:456` |
| " | File touched by all three rewrite kinds reverses under precedence | PASS | `test_unmerge.py:357` |
| " | Absorbed-id is not the LIFO tail | PASS | pre-existing coverage, unchanged by this delta |
| " | Unmerge of a non-merged pair | PASS | pre-existing coverage, unchanged by this delta |

**Total**: 3 requirements (2 ADDED + 1 MODIFIED, plus the modified Unmerge requirement makes it 4 requirement blocks in the delta spec), 12 scenarios — all mapped to a passing runtime test.

## TDD Compliance

| Check | Result |
|---|---|
| TDD Evidence reported | Present in apply-progress ("TDD Cycle Evidence" summary, RED-before-GREEN per phase) |
| All tasks have tests | 46/46 |
| GREEN confirmed | 2565/2565 pass on independent re-run |
| Triangulation | Adequate — dedupe matrix (5 cases), precedence (3 file kinds), v1/v2/v3 ledger (multiple dedicated tests per branch) |
| Assertion quality | No tautologies, no ghost loops, no vacuous setups found in the 9 test files inspected directly |

## Issues

None CRITICAL. None WARNING blocking archive.

**SUGGESTION**: `apply-progress.md` was persisted only to Engram, not to the OpenSpec `openspec/changes/rewrite-provenance-on-merge/` directory (unlike `verify-report.md` here, written per hybrid contract) — pre-existing asymmetry from the apply phase, not a defect in this change's correctness.

**Delivery note**: PR2 diff is 968 total changed lines (904 code+tests / 64 docs+artifacts) against the cached 800-line review budget, under the accepted `delivery_strategy: exception-ok` (`size:exception` already granted for this change per launch prompt). Reported, not blocking.

## Final Verdict

**PASS** — 0 CRITICAL, 0 WARNING (excluding the one non-blocking SUGGESTION above), 12/12 spec scenarios covered by a passing runtime test, all hard-checks independently verified against source and on-disk test evidence rather than trusted from apply-progress claims.
