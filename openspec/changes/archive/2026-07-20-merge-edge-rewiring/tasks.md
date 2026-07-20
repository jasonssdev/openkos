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

- [x] 2.1 RED: `tests/unit/model/test_okf.py` — `decode_merge_ledger_entry` accepts a V1 entry (no `relation_rewrites` key) and returns `relation_rewrites=[]` (spec: "Pre-slice-2a v1 ledger entry still unmerges exactly").
- [x] 2.2 RED: `tests/unit/model/test_okf.py` — `decode_merge_ledger_entry` requires `relation_rewrites` on a V2-schema entry, raising `ValueError` on a malformed/missing V2 entry.
- [x] 2.3 RED: `tests/unit/model/test_okf.py` — `encode_merge_ledger_entry`/`encode_merged_from` round-trip a `RelationRewrite(file, snapshot)` list.
- [x] 2.4 GREEN: `src/openkos/model/okf.py` — add `MERGE_LEDGER_SCHEMA_V2`, `RelationRewrite` dataclass, `relation_rewrites: list[RelationRewrite]` field on `MergeLedgerEntry`; update encode/decode to branch V1 (absent→`[]`) vs V2 (required key) vs unsupported schema.
- [x] 2.5 RED: `tests/unit/bundle/test_relations.py` (new) — `find_inbound_relation_rewrites` records one `RelationRewrite` per third-party file whose `relations:` targets `absorbed_id`, snapshot = original full text (spec: "Third-party inbound relations retarget to the survivor").
- [x] 2.6 RED: `tests/unit/bundle/test_relations.py` — `find_inbound_relation_rewrites` skips a file with malformed frontmatter (mirrors old `find_relation_conflicts` skip behavior).
- [x] 2.7 RED: `tests/unit/bundle/test_relations.py` — `apply_relation_rewrites` retargets, drops self-loops, dedupes collisions, re-emits via `encode_relations`; no-op on a file not in `rewrites`.
- [x] 2.8 RED: `tests/unit/bundle/test_relations.py` — `reverse_relation_rewrites` restores the recorded whole-file snapshot exactly.
- [x] 2.9 GREEN: `src/openkos/bundle/relations.py` (new) — implement `find_inbound_relation_rewrites`, `apply_relation_rewrites`, `reverse_relation_rewrites` per D3.
- [x] 2.10 GREEN: `src/openkos/bundle/merge.py` — `plan_merge` accepts/produces `relation_rewrites`, always writes `MERGE_LEDGER_SCHEMA_V2`; `plan_unmerge`/`UnmergePlan` carry `relation_rewrites`.
- [x] 2.11 VERIFY: `uv run pytest tests/unit/model/test_okf.py tests/unit/bundle/test_relations.py tests/unit/bundle/test_merge.py` green; `ruff check` + `mypy --strict` clean.

## Phase 3: PR3 — CLI Wiring, Preview, Round-Trip/LIFO/D5

- [x] 3.1 RED: `tests/unit/cli/test_merge_roundtrip.py` — `test_merge_then_unmerge_rematerializes_dropped_self_loop_and_deduped_collision`: merge→unmerge byte-parity for a survivor with outbound relations (re-materializes dropped self-loop and deduped collision) (spec: "Unmerge restores every touched file, including drops/dedupes"). Already passed pre-PR3 (PR1's `build_merged_document`/`merge_relations` + existing verbatim `survivor_before` restore already provide this) — kept as durable regression coverage.
- [x] 3.2 RED: `tests/unit/cli/test_merge_roundtrip.py` — `test_overlapping_third_party_relation_lifo_restores_each_intermediate_state`: **overlapping-LIFO** — two sequential merges (`merge S B`, then `merge S C`) both retarget the SAME third-party file's `relations:`; `unmerge S C` then `unmerge S B` restores that file to each exact intermediate byte state (F1, then F0) (spec: "LIFO unmerge across overlapping third-party files"; design D4 proof). Genuine RED pre-wiring.
- [x] 3.3 RED: `tests/unit/cli/test_unmerge.py` — `test_unmerge_skips_link_reverse_for_file_also_present_in_relation_rewrites`: **D5 double-reverse trap** — a third-party file with BOTH an inbound body-link and an inbound relation to absorbed, using different-length survivor/absorbed ids so the retarget shifts frontmatter length and invalidates the recorded link offset; unmerge SKIPS `reverse_link_rewrites` for that file. Verified via a temporary-removal experiment that the un-skipped version crashes: `ValueError: cannot reverse link rewrite: new_link '/concepts/keep.md' not found at recorded offset 118 in text`.
- [x] 3.4 RED: `tests/unit/cli/test_unmerge.py` — `test_unmerge_v1_ledger_entry_without_relation_rewrites_key_still_unmerges`: pre-slice-2a v1 `merged_from` entry (no `relation_rewrites` key) still decodes and unmerges survivor/absorbed/catalog exactly as before (spec: "Pre-slice-2a v1 ledger entry still unmerges exactly"). Already passed pre-PR3 — kept as durable regression coverage.
- [x] 3.5 RED: `tests/unit/cli/test_merge.py` — `test_preview_surfaces_relation_drop_dedupe_and_retarget_bullets`: confirm preview shows `- drop self-loop: {t} ({type})`, `~ dedupe collision: {t} ({type})`, and `~ bundle/{f} (retarget relation to survivor)` non-silently before any write. Genuine RED pre-wiring.
- [x] 3.6 GREEN: `src/openkos/cli/main.py::merge` — scans third-party files via `find_inbound_relation_rewrites` (same `other_files` snapshot as `find_inbound_link_rewrites`, captured before any write), applies via `apply_relation_rewrites`, passes `relation_rewrites` into `plan_merge`, and extends the confirm preview with drop/dedupe/retarget bullets.
- [x] 3.7 GREEN: `src/openkos/cli/main.py::unmerge` — reverses `relation_rewrites` (whole-file restore) via `bundle_relations.reverse_relation_rewrites` and applies the D5 link-skip rule: `relation_rewrite_files` computed and subtracted from the link-rewrite file set before any `reverse_link_rewrites` call.
- [x] 3.8 REGRESSION: existing merge/unmerge byte-parity tests (ADR-0002) run unmodified and stay green (44 passed across `test_merge.py`/`test_merge_roundtrip.py`/`test_unmerge.py`).
- [x] 3.9 VERIFY: `uv run pytest` full suite → 959 passed (954 baseline + 5 new, 0 regressions); `uv run pytest --cov` → 98.99% (>= 90% gate); `ruff check .` clean; `mypy` strict clean (84 files); `ruff format --check .` clean on all PR3-touched files (2 pre-existing PR2 files remain unformatted, confirmed out of scope via `git stash`).
- [x] 3.10 MERGE-READINESS GATE: PR1 (9589598) + PR2 (3627cba) committed on the chain; PR3 implemented/verified on `feat/mer-03-cli-wiring-roundtrip`, left UNCOMMITTED for orchestrator review per instruction. Tracker ready for merge-to-main once PR3 lands.

## Phase 4: Cleanup

- [x] 4.1 Updated `docs/adr/README.md` index with the ADR-0005 row.
- [x] 4.2 Swept `src/openkos/` for leftover guard-era comments/dead `RelationConflict` references: removed the stale "`unmerge` ... is a later unit and is NOT implemented here" line and reworded "a future `unmerge`" in `cli/main.py::merge`'s docstring (unmerge is now a real, implemented command). Remaining `RelationConflict`/`find_relation_conflicts` mentions in `bundle/relations.py`/`test_relations.py` docstrings are intentional historical rationale, left as-is.

## PR3 Correction Batch (bounded review fix)

- [x] C.1 CRITICAL RED→GREEN: `tests/unit/cli/test_unmerge.py::test_unmerge_relation_drift_fails_closed_no_write` — a legitimate edit to a relation-rewritten third-party file, made after `merge` and before `unmerge`, was silently CLOBBERED (exit 0, no warning) because `reverse_relation_rewrites` ignored its `text` argument and unconditionally returned the recorded snapshot, and `cli/main.py::unmerge` never read/compared the file's current bytes. Confirmed RED pre-fix (`exit_code == 0`). Fixed by making `reverse_relation_rewrites` drift-aware and fail-closed, symmetric with `reverse_link_rewrites`: it now takes `survivor_id`/`absorbed_id`/`link_rewrites` too, recomputes the file's EXPECTED post-merge content (forward-replays the recorded link rewrite, if any — design D5 — then the relation retarget, onto the recorded pre-merge snapshot) and raises `ValueError` on mismatch; `unmerge` now reads each relation-rewritten file's current on-disk text and passes it in, and the resulting `ValueError` is caught by Phase A's existing try/except, refusing the whole unmerge before any write.
- [x] C.2 REGRESSION GUARD: `tests/unit/bundle/test_relations.py::test_reverse_relation_rewrites_no_false_drift_when_file_also_link_rewritten` — proves the drift check does NOT false-positive on a design-D5 file (both an inbound link and an inbound relation to the absorbed id): the expected-content recomputation must forward-replay the link rewrite before the relation retarget, not just the relation retarget alone.
- [x] C.3 SUGGESTION coverage: `tests/unit/cli/test_merge.py::test_preview_bullets_match_committed_survivor_content` — couples the preview's drop-self-loop/dedupe-collision bullets to the actual post-write survivor document, guarding the preview recompute against future divergence. Passes on current code (regression insurance, not a new-code differentiator).
- [x] C.4 FORMATTING: ran `ruff format` on `tests/unit/bundle/test_relations.py` and `tests/unit/model/test_okf.py` (pre-existing PR2 drift) so `ruff format --check .` is clean repo-wide.
- [x] C.5 VERIFY: `uv run pytest` full suite → 963 passed (959 PR3 baseline + 4 new: C.1's RED test + `test_reverse_relation_rewrites_fails_closed_on_drifted_current_text` unit test, C.2, C.3); `uv run pytest --cov` → 98.99% (`bundle/relations.py` 100%); `ruff check .` clean; `ruff format --check .` clean (85 files); `mypy` strict clean (84 files).
