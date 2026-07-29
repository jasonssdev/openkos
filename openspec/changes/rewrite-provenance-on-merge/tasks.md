# Tasks: Rewrite inbound provenance on merge (issue #230)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | PR1 ~210-260, PR2 ~240-300 (total ~450-560) |
| 400-line budget risk | Low (per PR, against 800-line budget) |
| Chained PRs recommended | Yes |
| Suggested split | PR1 primitives/ledger -> PR2 CLI wiring/unmerge/docs |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `bundle/provenance.py` trio + `okf.py` v3 ledger, fully unit-tested, no CLI wiring | PR 1 | `uv run pytest tests/unit/bundle/test_provenance.py tests/unit/model/test_okf.py tests/unit/bundle/test_merge.py` | N/A — pure functions/codec, no CLI behavior yet | Revert PR1 branch; nothing calls the trio, no live behavior change |
| 2 | `prepare_merge`/`merge_core`/`unmerge` wiring + docs + CLI tests | PR 2 | `uv run pytest tests/unit/cli/test_merge.py tests/unit/cli/test_merge_core.py tests/unit/cli/test_merge_roundtrip.py tests/unit/cli/test_unmerge.py` | `uv run python -m openkos.cli.main merge <survivor> <absorbed>` against a temp bundle fixture, then `unmerge` | Revert PR2 branch only; PR1 primitives remain unused but harmless |

## Phase 1: Ledger v3 primitives (`okf.py`) — PR1

- [x] 1.1 RED: `tests/unit/model/test_okf.py` — `ProvenanceRewrite` dataclass round-trips `file`/`snapshot`; `MERGE_LEDGER_SCHEMA_V3` constant exists
- [x] 1.2 GREEN: add `ProvenanceRewrite` dataclass and `MERGE_LEDGER_SCHEMA_V3: Final = "openkos.merge_ledger/v3"` to `src/openkos/model/okf.py`; add `provenance_rewrites` field (default `[]`) to `MergeLedgerEntry`, after `relation_rewrites`
- [x] 1.3 RED: encode guard test — encoding a V1 or V2 entry with non-empty `provenance_rewrites` raises `ValueError` (mirrors existing V1 guard)
- [x] 1.4 GREEN: extend `encode_merge_ledger_entry`'s fail-closed guard to cover V1 *or* V2 with non-empty `provenance_rewrites`; emit `provenance_rewrites` key unconditionally otherwise
- [x] 1.5 RED: decode test — v3 entry with `provenance_rewrites` decodes; v3 entry MISSING `provenance_rewrites` fails closed; malformed item fails closed
- [x] 1.6 GREEN: add `_decode_provenance_rewrite` (mirrors `_decode_relation_rewrite`) and the v3 decode branch (REQUIRED, fails closed) to `decode_merge_ledger_entry`
- [x] 1.7 RED: v1 entry (no `relation_rewrites`, no `provenance_rewrites`) and v2 entry (`relation_rewrites` present, no `provenance_rewrites`) both decode; both default `provenance_rewrites` to `[]`
- [x] 1.8 GREEN: confirm v1/v2 branches ignore an absent `provenance_rewrites` key and default to `[]` (implemented by 1.6's branch table)

## Phase 2: Find/apply/reverse trio (`bundle/provenance.py`) — PR1

- [x] 2.1 RED: `tests/unit/bundle/test_provenance.py` — `find_inbound_provenance_rewrites` records a third-party file citing the absorbed id via `_doc()` helper (mirrors `test_relations.py`)
- [x] 2.2 RED: `find_inbound_provenance_rewrites` excludes `file_id in (survivor_id, absorbed_id)`; skips a file whose frontmatter fails to parse or whose `provenance` is not a list
- [x] 2.3 GREEN: implement `find_inbound_provenance_rewrites(files, *, absorbed_id, survivor_id)` in `src/openkos/bundle/provenance.py`; widen module docstring from "orphan-closure helper for forget" to cover write/reverse
- [x] 2.4 RED: **type-ungated scan** — absorbed concept has `type: Decision` (non-Source); assert `find_inbound_provenance_rewrites` still records the rewrite (dedicated test, not a clause on 2.1)
- [x] 2.5 GREEN: confirm 2.3's implementation performs NO `type` filter on the absorbed concept (P3); this is the requirement's own scenario, verified by 2.4 alone
- [x] 2.6 RED: retarget-then-dedupe matrix, parametrized on `apply_provenance_rewrites`: `[a]`->`[s]`; `[a,x,s]`->`[s,x]`; `[s,x,a]`->`[s,x]`; `[a,x,a.md]`->`[s,x]` (absorbed id repeated, `.md`-variant); `[x,y]` unchanged (no rewrite recorded) — assert exact list AND order
- [x] 2.7 GREEN: implement `apply_provenance_rewrites(text, *, file, survivor_id, absorbed_id, rewrites)` using the retarget-then-dedupe algorithm from design.md (first-occurrence-wins, `_normalize_id` as dedupe key, retained entries keep original string form)
- [x] 2.8 RED: `reverse_provenance_rewrites` — clean case returns snapshot; drifted `text` raises `ValueError`; two recorded rewrites for one file raises `ValueError`; unrecorded file returns `text` unchanged
- [x] 2.9 GREEN: implement `reverse_provenance_rewrites(text, *, file, survivor_id, absorbed_id, rewrites, link_rewrites, relation_rewrites)` — recompute expected post-merge bytes by applying link -> relation -> provenance forward from the snapshot (merge_core's exact chain order), then compare against `text` before allowing reversal

## Phase 3: Merge plan threading (`bundle/merge.py`) — PR1

- [x] 3.1 RED: `tests/unit/bundle/test_merge.py` — `plan_merge` always writes `MERGE_LEDGER_SCHEMA_V3`; `MergePlan`/`UnmergePlan` carry `provenance_rewrites: list[okf.ProvenanceRewrite]`
- [x] 3.2 GREEN: add `provenance_rewrites` to `MergePlan`/`UnmergePlan`; thread `plan_merge`/`plan_unmerge` through it exactly as `relation_rewrites` was threaded, writing v3

## Phase 4: PR1 checkpoint — RED/GREEN closure

- [x] 4.1 Run `uv run pytest tests/unit/bundle/test_provenance.py tests/unit/model/test_okf.py tests/unit/bundle/test_merge.py` — all GREEN
- [x] 4.2 `uv run ruff check . && uv run ruff format --check .` and `uv run mypy .` clean on touched files
- [x] 4.3 Confirm ADR-0011 (`docs/adr/0011-provenance-retarget-on-merge.md`) and its `docs/adr/README.md` index row are already committed with status `Proposed` (created during design phase) — no edits needed here

## Phase 5: `prepare_merge` third scanner (`cli/main.py`) — PR2

- [x] 5.1 RED: `tests/unit/cli/test_merge.py` — `prepare_merge` returns provenance rewrites for a third-party file citing the absorbed id; `PreparedMerge` gains a `provenance_rewrites` field
- [x] 5.2 GREEN: wire `find_inbound_provenance_rewrites` as the third scanner in `prepare_merge`, reading the SAME `other_files` snapshot already built for link/relation scanners; add `provenance_rewrites` to `PreparedMerge`; extend the merge preview to mention provenance retargets
- [x] 5.3 RED: **zero-extra-walk test** — plain-function counting wrapper around `Path.rglob` (NOT a generator/`yield from`, per design.md's precedent at `tests/unit/cli/test_contradictions.py:1041`'s `_counting_build_graph`); assert `rglob(bundle_dir, "*.md")` called exactly once after adding the third scanner
- [x] 5.4 GREEN: confirm `prepare_merge` reuses the existing `other_files` dict for the provenance scan; no new `rglob` call
- [x] 5.5 RED: `touched_files` is the sorted union of link, relation, AND provenance rewrite file sets (three-way union, not two)
- [x] 5.6 GREEN: update `touched_files` computation in `prepare_merge`

## Phase 6: `merge_core` third transform — PR2

- [x] 6.1 RED: `tests/unit/cli/test_merge_core.py` — for a file in `touched_files`, `merge_core` writes bytes reflecting `apply_link_rewrites` -> `apply_relation_rewrites` -> `apply_provenance_rewrites` chained in that order, single atomic write per file
- [x] 6.2 GREEN: add `apply_provenance_rewrites` as the third link in `merge_core`'s per-file transform chain
- [x] 6.3 RED: **snapshot byte-identity (T4)** — a third-party file with an inbound link, a `relations:` entry, AND a `provenance:` entry all pointing to the absorbed id; after merge, read the survivor's `merged_from` tail off disk and assert `entry.provenance_rewrites[0].snapshot == entry.relation_rewrites[0].snapshot`, both equal to the file's captured pre-merge bytes (real temp workspace, not a mocked snapshot)
- [x] 6.4 GREEN: confirm merge_core writes the shared pre-merge snapshot into both `provenance_rewrites` and `relation_rewrites` entries for that file (implementation should already satisfy this from the single-snapshot design; test closes the gap)

## Phase 7: `unmerge` precedence and reversal — PR2

- [x] 7.1 RED: `tests/unit/cli/test_unmerge.py` — a file present ONLY in `provenance_rewrites` restores exclusively via `reverse_provenance_rewrites`
- [x] 7.2 RED: **three-way precedence (provenance > relations > links)** — a file touched by all three rewrite kinds reverses exclusively from its `provenance_rewrites` snapshot, byte-identical to pre-merge; a file in `relation_rewrites` but not `provenance_rewrites` reverses via relation rule; a file in neither reverses via link rule
- [x] 7.3 GREEN: implement precedence partitioning in `unmerge` — `provenance_files = {r.file for r in provenance_rewrites}`; `relation_files = relations - provenance_files`; `link_files = links - provenance_files - relation_files`; call `reverse_provenance_rewrites` with BOTH `link_rewrites` and `relation_rewrites` args for `provenance_files`
- [x] 7.4 RED: **unmerge refuses on drift (T10)** — edit a provenance-rewritten file post-merge, then `unmerge` exits non-zero, no write, bundle snapshot unchanged
- [x] 7.5 GREEN: confirm `reverse_provenance_rewrites`'s drift check (from 2.9) propagates as a clean CLI failure before any write in `unmerge`
- [x] 7.6 RED: **v1/v2 backward compat (T9)** — a hand-built fixture survivor with a v1 entry and one with a v2 entry both unmerge exactly (no regression)
- [x] 7.7 GREEN: confirm `unmerge` reading a decoded v1/v2 entry (empty `provenance_rewrites`) skips the provenance-precedence branch entirely and falls through to existing relation/link reversal

## Phase 8: End-to-end round-trip and functional proof — PR2

- [x] 8.1 RED: `tests/unit/cli/test_merge_roundtrip.py` — merge -> unmerge is byte-identical for a file carrying all three rewrite kinds, AND separately for provenance-only, relations-only, links-only files
- [x] 8.2 GREEN: confirm round-trip parity holds given Phases 5-7 (should require no new production code — this test closes the E2E gap)
- [x] 8.3 RED: **functional defect #230 proof** — after a merge retargets a third party's `provenance` from absorbed to survivor, a later `set-sensitivity <survivor> <higher-level>` (confirmed) resolves that object as a provenance descendant, raises it via `combine_sensitivity`, and lists it in the preview/success message
- [x] 8.4 GREEN: confirm this passes with no `set_sensitivity_cmd` changes (design states it is untouched) — the fix is purely that `provenance` now correctly names the survivor, so existing `find_provenance_descendants` reaches it

## Phase 9: Docs and PR2 checkpoint

- [x] 9.1 Update `docs/cli.md`: merge now retargets `provenance:` third pass (type-ungated), retarget-then-dedupe behavior, unmerge precedence rule
- [x] 9.2 Update `docs/cli.md` with the **documented rollback failure mode**: a reverted v2 reader meeting a v3 ledger entry raises `unsupported merged_from schema version`; recovery is `unmerge` before revert, or hand-edit `schema` to v2 and drop `provenance_rewrites` after revert
- [x] 9.3 Confirm `docs/adr/0011-provenance-retarget-on-merge.md` status stays `Proposed` (frontmatter and body) — do NOT flip to Accepted; that is the archive phase's exclusive responsibility
- [x] 9.4 Run `uv run pytest tests/unit/cli/test_merge.py tests/unit/cli/test_merge_core.py tests/unit/cli/test_merge_roundtrip.py tests/unit/cli/test_unmerge.py` — all GREEN
- [x] 9.5 `uv run ruff check . && uv run ruff format --check .` and `uv run mypy .` clean; `uv run pytest --cov` >= 90% branch coverage on touched files

## PR Assignment

- **PR1** (`feat/provenance-rewrite-primitives` -> `main`): Phases 1-4
- **PR2** (`feat/merge-retargets-provenance` -> PR1 branch): Phases 5-9
