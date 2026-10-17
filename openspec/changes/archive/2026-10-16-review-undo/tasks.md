# Tasks: Undo Every Review Choice From The App, And List Every Combined Page

Tracking issues: #1467 and #1466. Inputs: `proposal.md`, `design.md`
(authoritative), the delta specs under `specs/` and ADR-0067 (Proposed, written
with this plan). Paths are under `src/openkos` unless they start with `tests/`,
`docs/`, `desktop/` or `openspec/`.

PR text names the change in prose ("the `review-undo` change") and uses
`Refs #1466` / `Refs #1467`; the desktop slice, which finishes both, uses
`Closes #1467` and `Closes #1466` on separate lines.

Spec shorthand. Stable-python-api: `P1` Every Combined Page Is Listable; `P2` A
Kept-Separate Ruling Is Listed And Reopened; `P3` A Typed Relation Is Removed;
`P4` A Type's Tier Is Set Or Restored; `P5` Undo And The Combined-Pages Read
Extend v1 Additively. Local-http-api: `H1` Operations (MODIFIED), `H2` Outcomes
(MODIFIED), `H3` The Undo Versions Travel Unchanged. Desktop-review: `R1` A
Choice Is Previewed (MODIFIED), `R2` A Combine Is Undone With Split Again
(MODIFIED), `R3` Keep Separate, A Connection And A Setting Are Undone. `D1`..`D9`
are the design decisions.

## Review Workload Forecast

Authored changed lines are additions plus deletions; the generated
`tests/unit/api/api_surface.json`, `tests/unit/localapi/openapi.json` and
`desktop/client/schema.d.ts` are excluded. The ~400-line figure is advisory: no
test or doc is cut to fit it.

| Slice | Title | Forecast (authored) | Depends on |
|-------|-------|---------------------|------------|
| S1 | Plan: proposal, design, delta specs, tasks, ADR-0067 Proposed | ~900 (docs) | none |
| S2 | Combined pages: `merged_concepts`, `GET /v1/merged`, client method (#1467) | ~350 | S1 |
| S3 | Reopen: `kept_distinct`, `preview_reopen`, `apply_reopen`, `NotUndoable`, `UndoStale`, routes | ~600 | S2 |
| S4 | Unrelate: `preview_unrelate`, `apply_unrelate`, routes | ~450 | S3 |
| S5 | Tier change: `clear_type_tier`, `preview_tier_change`, `apply_tier_change`, `TierPlan.configured_tier`, routes | ~550 | S4 |
| S6 | Review screen: combined pages from the read, Undo for the three kinds, wording | ~600 | S5 |
| S7 | Wording brief, CHANGELOG, spec merge, ADR-0067 Accepted, archive | docs | S6 |

Delivery strategy: auto-chain, stacked-to-main (each slice branches from `main`
after the previous one is squash-merged and regenerates the snapshots; generated
JSON and `schema.d.ts` are never hand-merged).

## S1 Plan

- [x] S1.1 Write `proposal.md`, `design.md`, `tasks.md` and the three delta specs.
- [x] S1.2 Write ADR-0067 (Proposed) and its README index row;
  `uv run pytest tests/unit/test_adr_index.py` passes.

## S2 Combined pages (P1, P5, H1; #1467)

- [x] S2.1 RED: application test for `unmerge_service.merged_concepts` (two pages
  newest first, a page absorbed after its own combine left out, an unreadable
  ledger skipped, no ledgers lists nothing, nothing written).
- [x] S2.2 GREEN: `merged_concepts` in `application/unmerge_service.py` (D1).
- [x] S2.3 RED then GREEN: facade `MergedConcept`, `merged_concepts` over real
  merges made through the decision surface; no lock, no client.
- [x] S2.4 `GET /v1/merged` with `MergedConceptList`; both route tables; OpenAPI
  snapshot; `api_surface.json` (classifier additive); `pnpm --dir desktop
  generate`; `client.mergedConcepts`.
- [x] S2.5 Mutation-check the page-exists filter and the ordering.

## S3 Reopen (P2, P5, H1-H3)

- [x] S3.1 RED: application tests for `kept_distinct`, `preview_reopen` and
  `apply_reopen` over a pair kept separate through the decision surface:
  refusals, digest stability, stale on a rewritten sidecar, busy lock, one
  commit of the sidecar only.
- [x] S3.2 GREEN: `application/undo_service.py` (D2-D5, reopen part).
- [x] S3.3 Facade `_undo.py`: `KeptDistinct`, `ReopenPreview`, `ReopenVersion`,
  `Reopened`, `NotUndoable`, `UndoStale`; exports; surface snapshot.
- [x] S3.4 Routes, error rows (`not_kept_distinct`), `_EchoRequest` apply body,
  OpenAPI snapshot, client methods and `ERROR_CODES`.
- [x] S3.5 Check whether a queue refresh re-offers a reopened pair (D7) and
  record the result here. Confirmed: after `apply_reopen`, one
  `refresh_decision_queue` lists the pair's identity decision again
  (`test_a_reopened_pair_is_offered_again_by_a_queue_refresh`).
- [x] S3.6 Mutation-check the declined-state test, the digest lines and the stale
  comparison. One survivor, kept on purpose: `len(members) >= 2` is redundant
  with the ruling lookup (no writer records a one-member ruling), and guards
  the first-member index.

## S4 Unrelate (P3, P5, H1-H3)

- [x] S4.1 RED: application tests over a relation written by a relation-type
  decision: removal, `not_related` refusals (absent edge, missing page, invalid
  id, same page, invalid type), stale on an edited source, commit paths.
- [x] S4.2 GREEN: `undo_service` unrelate (D4, D5).
- [x] S4.3 Facade, routes, `not_related` row, snapshots, client.
- [x] S4.4 Mutation-check the digest lines and the refusal mapping.

## S5 Tier change (P4, P5, H1-H3)

- [x] S5.1 RED: `config.clear_type_tier` text-surgery tests (entry removed,
  every other byte kept, absent entry unchanged, un-editable shapes refused).
- [x] S5.2 RED: `plan_config_edit` with `tier=None`.
- [x] S5.3 RED: application tests: restore by removal after a set-tier decision,
  restore to an earlier explicit tier, `tier_unchanged`, `unknown_type`, stale on
  an edited config, uncommitted same-entry edit refused.
- [x] S5.4 GREEN: `clear_type_tier`, `plan_config_edit`, `undo_service` tier
  change, `TierDecisionPreview.configured_tier` and `TierPlan.configured_tier`
  (D6).
- [x] S5.5 Facade, `VolatilityTier`, routes, `tier_unchanged` and `unknown_type`
  rows, snapshots, client.
- [x] S5.6 Mutation-check `clear_type_tier`, the unchanged check and the digest.

## S6 Review screen (R1-R3; Closes #1467, #1466)

- [x] S6.1 RED: model tests: the combined list read on load, Undo after keep
  separate / connect / set-tier (preview then apply, versions echoed, refresh
  after reopen, done line, Undo gone), stale and refused undos.
- [x] S6.2 RED: wording tests for the undone lines and the new problems.
- [x] S6.3 GREEN: `desktop/ui/review/model.ts`, `wording.ts`, `render.ts`
  ("Pages you combined").
- [x] S6.4 Node gates: `typecheck`, `test`, `check:schema`, `build`.
- [x] S6.5 Mutation-check the undo dispatch and the refresh-after-reopen.

## S7 Archive

- [x] S7.1 `docs/decision-wording.md`: the new copy, the #1466/#1467 gaps closed
  (landed with S6, beside the code that shows it).
- [x] S7.2 CHANGELOG entry.
- [x] S7.3 Merge the deltas into `openspec/specs/`, name-match every heading,
  check each requirement against shipped code.
- [x] S7.4 ADR-0067 Accepted in all three places.
- [x] S7.5 Move the change to `openspec/changes/archive/2026-10-16-review-undo/`
  with `archive-report.md`; verify on `origin/main` that the original folder is
  gone.
