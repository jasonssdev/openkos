# Archive Report: entity-resolution-merge

**Change**: entity-resolution-merge (MVP-2 slice 3 of 3, entity-resolution mini-chain — final unit) | **Archived**: 2026-07-20 | **Status**: Complete | **Repository**: openkos (main 63a0152, #87) | **Mode**: hybrid

This archive report closes the SDD cycle for the `entity-resolution-merge` change: OpenKOS's first DESTRUCTIVE entity-resolution operation — a confirm-gated, fully REVERSIBLE 2-way `merge` of two concept-ids a human has confirmed are the same real-world entity, plus a first-class `unmerge` verb achieving round-trip parity. This change **completes the entity-resolution mini-chain**: slice 1 (`entity-resolution` — candidate generation) → slice 2 (`entity-resolution-adjudication` — LLM precision layer) → slice 3 (`entity-resolution-merge` — the destructive act itself, this change).

## Change Summary

**Purpose**: slices 1-2 only surface duplicate candidates (read-only); nothing acted on them. This slice closes the boundary problem by uniting the graph, honoring KOM's stated bar (`docs/knowledge-object-model.md:317-328, 255-272`) that merges are reversible with no information loss.

**Scope shipped**:
- `combine_sensitivity(a, b)` + `SENSITIVITY_ORDER = ("public", "private", "confidential")` in `model/okf.py` — the first implementation of KOM's sensitivity high-water-mark rule; fails closed (missing → `private`, malformed/non-str → `confidential`).
- `build_merged_document` + `merged_from` ledger encode/decode (`model/okf.py`): survivor-wins scalars, union-deduped lists, most-recent freshness/timestamp, sensitivity recomputed (never copied).
- New `bundle/merge.py`: pure `plan_merge`/`plan_unmerge` (text-in/out, no I/O) producing `MergePlan`/`UnmergePlan`.
- New `bundle/links.py`: fence-masked, anchor-preserving inbound-link rewrite (`rewrite_inbound_links`) and bounded, exact-offset reversal (`reverse_link_rewrites`) — its own `_LINK_RE`/`_mask_fenced_code_blocks`, deliberately duplicated rather than imported from `graph` (canonical layer must not import derived layer; same precedent as `bundle/index.py`).
- `merge <survivor-id> <absorbed-id>` CLI verb (`cli/main.py`): Phase A pure preview (sensitivity outcome + links to rewrite) → confirm gate (`--auto` > `review:false` > TTY > non-TTY refusal, mirroring `forget`) → Phase B writes index/log, rewritten docs, survivor (ledger persisted), then deletes the absorbed file **last** (crash-safe ordering).
- `unmerge <survivor-id> <absorbed-id>` CLI verb — **two-arg, LIFO-enforced**: reverses only the most-recent unreversed `merged_from` entry, refusing with no write if the supplied `absorbed-id` is not that tail entry's id. Restores survivor/absorbed/index/`log_before` from verbatim snapshots, reverses every recorded link rewrite by exact offset (descending order), then appends a single unmerge audit line to `log.md`.
- Reversibility ledger (`merged_from`, a list, LIFO) embeds the FULL pre-merge snapshot set per entry: `absorbed_snapshot`, `survivor_before` (retaining any prior `merged_from` entries from earlier merges), `index_before`, `log_before`, `link_rewrites: [{file, old_link, new_link}]`, `sensitivity_before`/`sensitivity_after` — because sensitivity high-water-mark and provenance/tag union are lossy and non-invertible, only a verbatim-snapshot ledger makes round-trip parity possible.
- Two property tests (`tests/unit/cli/test_merge_roundtrip.py`): single merge→unmerge byte-parity, and **sequential/LIFO** parity (`merge(A,B)→merge(A,C)→unmerge(A,C)→unmerge(A,B)` restores the bundle to its original pre-any-merge byte state) — both snapshot the FULL bundle directory, not a subset, comparing every key except `log.md`.
- Docs updated: `docs/knowledge-object-model.md`, `docs/cli.md` (including the interleaved-drift limitation note on `unmerge`).
- Two ADRs drafted during design, **promoted Proposed → Accepted during this archive** (deferred task 6.1, see below): ADR-0002 (reversible merge ledger with embedded verbatim snapshots) and ADR-0003 (sensitivity high-water-mark ordering and fail-closed combine).

**Review-caught bugs found and fixed before verify** (from the apply-phase resilience fix batch and earlier review passes on this branch):
1. **Unhashable-list crash** in list-union dedup — fixed by switching to equality-based `in` membership checks (not `set`/hash-based) in `_union_dedup`, so list-valued frontmatter fields containing unhashable items (e.g. nested dicts) no longer crash the merge builder.
2. **Mixed aware/naive timestamp crash** in freshness-most-recent comparison — `_absorbed_is_more_recent` now catches `TypeError` from comparing an aware and a naive datetime and fails closed to survivor-wins rather than raising.
3. **CRITICAL — `merge`'s half-completed-write retry trap**: `_apply_link_rewrite_idempotently` added so a retried `merge` whose Phase B partially wrote some rewritten files does not raise `ValueError` re-reading an already-rewritten file; already-done rewrites are detected and skipped, genuine drift still fails closed.
4. **CRITICAL — `unmerge`'s half-completed-write retry trap** (symmetric to #3): `_reverse_link_rewrite_idempotently` added — if every recorded rewrite's `old_link` already sits at its recorded offset (fully already-reversed), the reverse is a no-op skip instead of raising on retry; genuine drift (neither `old_link` nor `new_link` present) still fails closed with `ValueError`. Locked by `test_retry_after_mid_reverse_failure_completes_the_unmerge`, confirmed genuine RED before the fix (stale "new_link not found at recorded offset" error on retry) and GREEN after.
5. **Link-rewrite reverse-offset fix**: `reverse_link_rewrites` reverses right-to-left (descending recorded offset) so earlier reversals never shift the byte offsets recorded for later ones in the same file.
6. **`unmerge` interleaved-drift warning** (WARNING-severity, by design a documented limitation, not a bug it refuses on): `_expected_post_merge_index_and_log` deterministically reconstructs what `index.md`/`log.md` looked like immediately after the merge being reversed (replaying the same transforms `merge` applied), compares against the current on-disk state, and — if an `ingest`/`forget`/unrelated `merge` ran between the merge and this unmerge — surfaces a preview warning ("changed since the merge... will discard those changes") before the confirm gate. It does **not** refuse; round-trip parity is guaranteed only for a prompt unmerge with no interleaving, and this is now a named spec limitation (Requirement: Unmerge Achieves Round-Trip Parity), not silent data loss.

## Artifacts Archived

| Artifact | Location | Status |
|---|---|---|
| Proposal | `archive/2026-07-20-entity-resolution-merge/proposal.md` | Moved from change folder; Engram `sdd/entity-resolution-merge/proposal` (id 1181) |
| Specification (delta) | `archive/2026-07-20-entity-resolution-merge/specs/entity-resolution-merge/spec.md` | Copied verbatim to main spec tree at `openspec/specs/entity-resolution-merge/spec.md` (new domain); Engram `sdd/entity-resolution-merge/spec` (id 1182) |
| Design | `archive/2026-07-20-entity-resolution-merge/design.md` | Moved from change folder; Engram `sdd/entity-resolution-merge/design` (id 1183) |
| Tasks | `archive/2026-07-20-entity-resolution-merge/tasks.md` | 23/23 checkboxes `[x]` across 6 phases (6.1 — ADR promotion — completed during this archive pass and marked `[x]` in the archived copy, with the reconciliation reason recorded below); Engram `sdd/entity-resolution-merge/tasks` (id 1184) |
| Apply progress | (recorded below) | Engram `sdd/entity-resolution-merge/apply-progress` (id 1185) |
| Verification Report | (recorded below) | Engram `sdd/entity-resolution-merge/verify-report` (id 1197) |

All artifact revisions for this change span Engram observation ids **1181–1197** (proposal 1181, spec 1182, design 1183, tasks 1184 [10 revisions across the apply lifecycle], apply-progress 1185, verify-report 1197 — the intervening ids in the range are prior revisions of the same topic keys, superseded by the final revision cited here).

### Task-Completion-Gate reconciliation (exceptional, explicit)

At verify time, `tasks.md` (both on-disk and the Engram `tasks` observation) showed Phase 6 item **6.1 unchecked** (`- [ ] 6.1 Flip ADR-0002/0003 status Proposed→Accepted post-review (deferred to archive)`) — this was a deliberate, explicitly-planned deferral of the ADR-promotion step to the archive phase, not stale/incomplete implementation work. `verify-report` (Engram id 1197) confirms: "tasks.md on disk: Phase 1-5 all [x], 6.1 (ADR promotion) explicitly [ ] deferred to archive, 6.2/6.3 [x]" and closes with "Ready for sdd-archive; ADR 0002/0003 Proposed→Accepted flip remains the one deliberately deferred task (6.1) to execute during archive." Per the orchestrator's explicit instruction for this archive pass, `sdd-archive` performed the deferred 6.1 work itself during this phase: both ADR files' frontmatter `status:` and body `**Status:**` lines were flipped `Proposed` → `Accepted`, and `docs/adr/README.md`'s index rows for ADR-0002/0003 were updated to `Accepted`. The archived copy of `tasks.md` marks 6.1 `[x]` with a note recording this reconciliation; no other ADR content was altered (ADRs are immutable once Accepted apart from the status flip).

## Spec Merge Summary

| Action | Domain | Details |
|---|---|---|
| **CREATED** | `entity-resolution-merge` | New spec domain created at `openspec/specs/entity-resolution-merge/spec.md` (no prior main spec existed for this capability) |
| Requirement count | 7 requirements + 12 scenarios | Full specification of the confirm-gated destructive merge/unmerge contract: two-distinct-concept-id fusion, frontmatter-conflict resolution, sensitivity high-water-mark recomputation, the full-snapshot `merged_from` reversibility ledger, fence-masked inbound-link rewrite, confirm-gated two-phase execution (Phase A preview / Phase B write), and two-arg LIFO-enforced `unmerge` achieving round-trip parity (including the interleaved-drift warning limitation) |
| Source | Delta spec from change folder | `openspec/changes/entity-resolution-merge/specs/entity-resolution-merge/spec.md` → `openspec/specs/entity-resolution-merge/spec.md` |
| Merge mode | Direct copy (no pre-existing spec; delta already authored in living-spec shape — no ADDED/MODIFIED delta headers to convert) | First spec for this domain; delta becomes canonical main spec verbatim |

No requirements or scenarios were invented, dropped, or altered during merge — the delta spec (Engram id 1182) was already structured as a standalone capability spec (Purpose / Non-Goals / Requirements+Scenarios) and is preserved faithfully in `openspec/specs/entity-resolution-merge/spec.md`, matching the structure of `openspec/specs/entity-resolution/spec.md` and `openspec/specs/entity-resolution-adjudication/spec.md`. Every requirement named in the orchestrator's brief is present verbatim: sensitivity high-water-mark, the `merged_from` ledger full-snapshot set, offset-exact fence-masked inbound-link rewrite, confirm-gated merge Phase A/B, two-arg LIFO `unmerge`, round-trip parity (single + sequential), the interleaved-drift warning limitation, and the Non-Goals list.

## Verification Status

**Final Verdict** (Engram `sdd/entity-resolution-merge/verify-report`, id 1197): **VERIFIED-WITH-NOTES** — 0 CRITICAL, 2 WARNING (both non-blocking, reporting-accuracy only), 0 SUGGESTION.

**Evidence Summary** (independently reproduced at verify time, fresh execution on branch `feat/erm-05-unmerge-parity`, not trusted from the apply report):
- `uv run pytest -q` → **874 passed**, exit 0
- `uv run ruff check .` → All checks passed, exit 0
- `uv run ruff format --check .` → 80 files already formatted, exit 0
- `uv run mypy .` → Success, no issues in 80 source files, exit 0 (**mypy strict clean**)
- `uv run pytest --cov --cov-branch -q` → TOTAL 2134 stmts / 598 branches, **99.19%** (required ≥90%); `bundle/merge.py`/`bundle/links.py`/`bundle/log.py`/`bundle/bundle.py` 100%; `bundle/index.py` 90% (lines 165-176, pre-existing, unrelated); `cli/main.py` 99% (5 missed lines, all pre-existing, none inside the merge/unmerge fix-batch helpers)
- `uv run pytest tests/unit/cli/test_merge_roundtrip.py -v` → **4 passed**: single merge→unmerge byte-parity, sequential LIFO merge→merge→unmerge→unmerge byte-parity, non-tail-refuses, decline-writes-nothing — both central parity tests confirmed to snapshot the FULL bundle directory (every file under `bundle/`), not a subset, comparing all keys except `bundle/log.md`

**All 8 requirement areas verified PASS** against `openspec/specs/entity-resolution-merge/spec.md` (source-inspected + test-covered):
1. Sensitivity HWM (`combine_sensitivity`, `okf.py` L190-224) — PASS.
2. Ledger (`merged_from`, `okf.py` L253-436) — PASS. All required fields present; schema-gated decode; unhashable-list and mixed-tz crashes both fixed and covered.
3. Inbound-link rewrite (`bundle/links.py`) — PASS. Own `_LINK_RE`/`_mask_fenced_code_blocks`, not imported from `graph` (confirmed by the canonical-layer-does-not-import-derived AST guard test); bounded single-occurrence substitution; anchor preserved.
4. `merge` verb (`cli/main.py` L941-1169) — PASS. Guards, confirm-gate precedence, Phase B ordering (index/log → rewrites → survivor+ledger → absorbed deleted last) all confirmed.
5. `unmerge` verb (`cli/main.py` L1173-1403) — PASS. Two-arg LIFO enforcement via `plan_unmerge`; Phase B ordering (index/log restored → reversed links → absorbed recreated → survivor restored last); interleaved drift detected and surfaced as a non-refusing preview warning.
6. Round-trip parity (`tests/unit/cli/test_merge_roundtrip.py`) — PASS, verified by direct re-execution, both single and sequential-LIFO.
7. Layering — PASS. `bundle/links.py`'s duplicate-not-import rationale documented and empirically AST-guard-tested.
8. ADRs and non-goals — PASS. Both ADRs indexed; no embeddings/batch/`--from-adjudicate`/N-way-single-shot code paths found; slices 1-2 code unmodified except additive `main.py` changes.

**Issues at final close**:
- 0 CRITICAL.
- 2 WARNING (both non-blocking, reporting-accuracy only, no functional defect): (a) apply-progress artifacts described the resilience fix batch as "not committed, no PR opened" — `git log`/`git show` confirmed both fix functions and their tests were already part of the committed `ba1992d` commit; only the "no PR opened" part was accurate. (b) apply-progress claimed `test_unmerge.py` "now has 16 tests total"; `pytest --collect-only` found 15 actual test functions — every named scenario is present and passing, a counting discrepancy only.
- 0 SUGGESTION.
- Assertion-quality audit (Strict TDD Step 5f): no tautologies, no unguarded ghost loops found in the two `for`-loops identified in `test_merge.py`/`test_merge_roundtrip.py`; both preceded by explicit set/key-equality assertions guaranteeing non-trivial iteration.

## Delivery History

Delivered as a **5-unit Feature Branch Chain** (tasks forecast, Engram id 1184: 900-1300 estimated changed lines against the 400-line review budget — `ask-on-risk` fired, High risk, chained PRs recommended, resolved to `feature-branch-chain`).

1. **Sub-PR #82** — U1: `combine_sensitivity` + `SENSITIVITY_ORDER` in `model/okf.py`, fully tested (all pairs, missing, malformed/non-str).
2. **Sub-PR #83** — U2: ledger schema + `plan_merge`/`plan_unmerge` (`bundle/merge.py`), library-only, no CLI wiring yet.
3. **Sub-PR #84** — U3: `bundle/links.py` inbound-link rewrite/reverse, fence-masked, anchor-preserving, own duplicated regex/mask (not imported from `graph`).
4. **Sub-PR #85** — U4: `merge` verb + confirm gate wired into `cli/main.py`, Phase A/B ordering, path-traversal and self/unknown-id guards.
5. **Sub-PR #86** — U5: `unmerge` verb, two-arg LIFO enforcement, round-trip parity property tests (single + sequential), plus the post-apply resilience fix batch (idempotent-retry guards for both `merge` and `unmerge`'s link-rewrite steps, and the interleaved-drift warning).

All five sub-PRs landed to `main` via **PR #87** (the tracker/integration PR), merged as commit **63a0152**.

**Repository State**: `main` @ `63a0152`.

## Deferred / Non-Goals (explicit, unchanged from proposal through archive)

Per the proposal's Out of Scope section and the spec's Non-Goals, all confirmed untouched by diff-stat/source inspection at verify time:

- Re-opening slices 1-2 (`resolution/candidates.py`/`resolution/adjudication.py` unchanged).
- Embeddings; any similarity-model change — no embeddings import anywhere in the merge code paths.
- No-confirm automatic merge — `merge` always routes through the confirm-gate precedence (`--auto` > `review:false` > TTY > non-TTY refusal); no code path bypasses it.
- N-way single-shot merge — `merge`/`unmerge` are strictly 2-argument; HIGH groups >2 require sequential pairwise merges (proven reversible by the sequential/LIFO round-trip parity property test).
- Batch/`--from-adjudicate` mode — no such flag or wiring exists; manual pairwise invocation only.
- Changes to `forget` — `forget`'s code path is unmodified; `merge`/`unmerge` independently mirror its Phase A/B shape without altering it.

## Risks & Limitations Recorded

| Risk | Likelihood | Status |
|---|---|---|
| Sensitivity high-water-mark had zero prior code — subtle ordering/unknown-value bugs | Was Med | Mitigated: dedicated parametrized tests for every pair plus missing/malformed/non-str edges; fails closed to `confidential` |
| Link-rewrite over-matches substrings across bundle | Was Med | Mitigated: anchor on exact `(/id.md)` link form, fence-masked, single-occurrence bounded substitution; every rewrite recorded for reversal |
| Frontmatter-conflict rule becomes a load-bearing convention | Med | Pinned explicitly in spec/design; surfaced in the Phase A preview |
| Destructive op with reviewer cognitive load (large multi-file diff) | Was Med-High | Mitigated: delivered as a 5-unit feature-branch chain against the 400-line budget, each unit independently revertible |
| Ledger snapshot drift making unmerge partial | Was Low | Mitigated: full verbatim-snapshot set (absorbed + survivor + index + log) embedded per entry; both fail-closed retry-idempotency fixes and the interleaved-drift warning added post-review |
| `unmerge`'s half-completed-write retry trap (CRITICAL, found and fixed) | Resolved | `_reverse_link_rewrite_idempotently` added; retry after a mid-Phase-B failure now completes cleanly instead of raising a stale drift error; locked by a dedicated regression test |
| `merge`'s half-completed-write retry trap (CRITICAL, found and fixed) | Resolved | `_apply_link_rewrite_idempotently` added; symmetric fix, same pattern |
| Interleaved `ingest`/`forget`/unrelated `merge` between merge and unmerge silently discarding changes | Was unaddressed | Resolved as a **documented, surfaced limitation**, not a silent bug: `unmerge` now warns in its Phase A preview before the confirm gate if `index.md`/`log.md` drifted since the merge, but intentionally does not refuse (round-trip parity is guaranteed only for a prompt unmerge with no interleaving) |
| Apply-progress reporting-accuracy discrepancies (commit-status wording, test-count) | Low | Non-blocking WARNINGs only; independent source/test inspection confirmed no functional gap |

## Archival Actions Completed

**Filesystem**:
- [x] Living spec created at `openspec/specs/entity-resolution-merge/spec.md` (new capability domain; direct copy from the delta spec, format matched against `openspec/specs/entity-resolution/spec.md` and `openspec/specs/entity-resolution-adjudication/spec.md`)
- [x] ADR-0002 and ADR-0003 promoted `Proposed` → `Accepted` (frontmatter `status:` and body `**Status:**` in both files) and `docs/adr/README.md` index rows updated to `Accepted` — the deferred Phase 6.1 task, executed during this archive pass
- [x] Change artifacts (proposal, design, tasks, specs) written to `openspec/changes/archive/2026-07-20-entity-resolution-merge/`, byte-identical to the pre-archive change-folder originals (tasks.md's 6.1 checkbox is the one intentional exception, checked in the archived copy to reflect the reconciliation performed during this same archive pass, with the reason recorded above)
- [x] This archive report written to `openspec/changes/archive/2026-07-20-entity-resolution-merge/archive-report.md`

**Engram**:
- [x] Archive report saved with topic key `sdd/entity-resolution-merge/archive-report`
- [x] All artifact observation IDs recorded above for traceability (proposal 1181, spec 1182, design 1183, tasks 1184, apply-progress 1185, verify-report 1197; full revision range 1181-1197)

## Known Limitation of This Archival Pass

The executor performing this archive had access only to `Read`/`Write`/`Edit`/`Glob` and Engram tools — no shell/`git` tool was available in this session (same constraint as the preceding `entity-resolution-candidates`, `graph-projection`, and `entity-resolution-adjudication` archives). Every artifact was therefore **written as a byte-identical copy** to the archive and living-spec locations rather than moved with `git mv`, and the original `openspec/changes/entity-resolution-merge/` directory could **not** be deleted by this executor. The orchestrator is expected to run the equivalent of `git mv`/`git rm -r openspec/changes/entity-resolution-merge/` after confirming the archived copies are byte-identical to the originals (apart from the intentional `tasks.md` 6.1 checkbox reconciliation), so the rename is preserved as a move in history and the active changes directory no longer lists this change.

## Next Steps

**For the project**:
- The entity-resolution mini-chain is now **fully complete**: slice 1 (`entity-resolution` — candidate generation), slice 2 (`entity-resolution-adjudication` — LLM precision layer), slice 3 (`entity-resolution-merge` — the confirm-gated, reversible destructive merge, this change). All three specs are now living specs under `openspec/specs/`.
- No further slices are planned for this mini-chain. Any future entity-resolution work (e.g. batch/`--from-adjudicate` mode, N-way merge orchestration, embeddings) is a new proposal, not a continuation of this chain.

**For archive verification**:
- No CRITICAL issues remain; the two non-blocking WARNINGs (apply-progress reporting-accuracy discrepancies) are documentation-accuracy notes only and do not affect spec compliance.
- The only outstanding action is the mechanical `git mv`/deletion of the original change folder noted above, owned by the orchestrator (no shell/git tool available to this executor).

## Traceability

This archive report records the final state of the `entity-resolution-merge` change from proposal through verification and archival. The change has been:
- Fully specified (7 requirements, 12 scenarios)
- Fully designed (4 architecture decisions, ledger schema, `combine_sensitivity`, frontmatter-conflict rule, data flow, threat matrix, migration/rollout — 2 ADRs drafted)
- Fully implemented (5-unit feature-branch-chain, sub-PRs #82-#86, landed via #87), including 6 review-caught bugs found and fixed (unhashable-list crash, mixed-tz crash, 2 CRITICAL half-completed-write retry traps in merge/unmerge, the link-rewrite reverse-offset fix, and the unmerge interleaved-drift warning limitation)
- Fully verified (99.19% branch coverage, mypy strict clean, 874/874 tests passing, both round-trip parity property tests — single and sequential/LIFO — pass, all 8 requirement areas PASS, 0 CRITICAL / 2 non-blocking WARNING / 0 SUGGESTION)
- ADR-0002 and ADR-0003 promoted `Proposed` → `Accepted` during this archive pass
- Fully delivered (main @ 63a0152)

The SDD cycle is CLOSED for slice 3. This **completes the entity-resolution mini-chain** (candidates → adjudication → merge), pending the mechanical folder-removal follow-up noted above.

**Archive Date**: 2026-07-20 (ISO format)
**Repository Head**: 63a0152 (main)
**Archival Status**: COMPLETE (content, including the ADR promotion), PENDING (original-folder removal — requires shell/git access not available to this executor)
