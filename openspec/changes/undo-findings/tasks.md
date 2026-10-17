# Tasks: Undo A Declined Contradiction And A Reconcile From The App

Tracking issue: #1497. Inputs: `proposal.md`, `design.md` (authoritative), the
delta specs under `specs/` and ADR-0074 (Proposed, written with this plan).
Paths are under `src/openkos` unless they start with `tests/`, `docs/`,
`desktop/` or `openspec/`.

PR text names the change in prose ("the `undo-findings` change") and uses
`Refs #1497`.

Spec shorthand. Stable-python-api: `P1` A Declined Contradiction Is Listed And
Reopened Against Its Version; `P2` A Reconcile Is Listed And Undone Against Its
Version; `P3` Undoing Findings Extends v1 Additively. Local-http-api: `H1`
Undoing A Decline Or A Reconcile Is Served Under `/v1`. `D1`..`D6` are the
design decisions.

## Review Workload Forecast

Authored changed lines are additions plus deletions; the generated
`tests/unit/api/api_surface.json`, `tests/unit/localapi/openapi.json` and
`desktop/client/schema.d.ts` are excluded. The ~400-line figure is advisory.

| Slice | Title | Forecast (authored) | Depends on |
|-------|-------|---------------------|------------|
| S1 | Plan: proposal, design, delta specs, tasks, ADR-0074 Proposed | ~500 (docs) | none |
| S2 | Reopen a decline: list, preview, apply, routes, client | ~700 | S1 |
| S3 | Undo a reconcile: note inverse, list, preview, apply, routes, client, wording | ~900 | S2 |
| S4 | CHANGELOG, spec merge, ADR-0074 Accepted, archive | docs | S3 |

Delivery: stacked to `main`, each slice branched from `main` after the previous
one is squash-merged; generated snapshots are regenerated, never hand-merged.

## S1 Plan

- [ ] S1.1 `proposal.md`, `design.md`, `tasks.md` and the two delta specs.
- [ ] S1.2 ADR-0074 (Proposed) and its README row; `test_adr_index.py` passes.

## S2 Reopen a decline (P1, P3, H1)

- [ ] S2.1 RED: application tests for `declined_contradictions`,
  `preview_reopen_contradiction`, `apply_reopen_contradiction` over records
  written by `apply_contradiction_decision`: ordering, refusals (no record, an
  open record, the other discriminator, invalid ids, one page twice), digest
  stability, stale on a rewritten sidecar, busy lock, plan inside the section,
  one commit of the sidecar.
- [ ] S2.2 GREEN: `undo_service` reopen-contradiction (D1, D3-D5).
- [ ] S2.3 Facade: `DeclinedContradiction`, `ContradictionReopenPreview`,
  `ContradictionReopenVersion`, `ContradictionReopened`, `not_declined`;
  exports; surface snapshot (classifier additive). Facade tests over a decline
  made through `preview_finding` / `apply_decision`.
- [ ] S2.4 Routes, `not_declined` row, OpenAPI snapshot, `pnpm --dir desktop
  generate`, client methods, `ERROR_CODES`, client tests.
- [ ] S2.5 Mutation-check the declined-state test, the digest lines and the
  stale comparison.

## S3 Undo a reconcile (P2, P3, H1)

- [ ] S3.1 RED: `reconcile_service.remove_reconciliation_note` (last note,
  middle note, another counterpart's note kept, an edited note left, absent).
- [ ] S3.2 RED: application tests over pages written by `reconcile_service`:
  each mode and direction, byte restoration, a bystander still superseding, a
  human-authored status, an edited note, `mixed`, refusals, stale on an edited
  page and on a bystander flipping the withdrawal, lock, commit paths, the list.
- [ ] S3.3 GREEN: `remove_reconciliation_note`, the public classifier name,
  `undo_service` unreconcile (D2, D4, D5), `WriteFailed` operation
  `unreconcile`.
- [ ] S3.4 Facade, routes, `not_reconciled` row, snapshots, client, tests.
  Facade tests over choices made through `preview_finding` / `apply_decision`.
- [ ] S3.5 `docs/decision-wording.md`: Undo on the contradiction and revision
  confirmations, the two refusals.
- [ ] S3.6 Mutation-check the note shape, the edge filter, the withdrawal line
  and the digest.

## S4 Archive

- [ ] S4.1 CHANGELOG entry.
- [ ] S4.2 Merge the deltas into `openspec/specs/`, name-match every heading,
  check each requirement against shipped code.
- [ ] S4.3 ADR-0074 Accepted in all three places.
- [ ] S4.4 Move the change to `openspec/changes/archive/<date>-undo-findings/`
  with `archive-report.md`; verify the original folder is gone.
- [ ] S4.5 Comment on #1497 with what shipped; close it if no engine work
  remains (the screen adopts the operations in its own change).
