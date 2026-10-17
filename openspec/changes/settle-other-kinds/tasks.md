# Tasks: settle-other-kinds

## 1. Plan

- [x] 1.1 Proposal, delta specs, design and ADR-0072 (Proposed).

## 2. Engine

- [ ] 2.1 RED/GREEN: `reconcile_service` exposes `prepare_pair` and `commit_pair(guard=)`; the conflict gate raises `ReconcileConflict`; `reconcile_pair` is unchanged in behaviour.
- [ ] 2.2 RED/GREEN: `resolve_reconciled` resolves the revision row over the pair, as proposed or modified.
- [ ] 2.3 RED/GREEN: the list gives contradiction and revision rows their actions, withheld reasons and freshness.
- [ ] 2.4 RED/GREEN: `application/finding_decisions.py` previews and commits reconcile and decline on the token path; `curate_decisions` dispatches to it and refuses the wrong preview.

## 3. API and local API

- [ ] 3.1 RED/GREEN: `Decision.finding` details, `preview_finding`, `FindingPreview`, the plans; the surface snapshot regenerated (additive).
- [ ] 3.2 RED/GREEN: `POST /v1/decisions/{key}/preview-finding` and the two new error rows; the OpenAPI snapshot regenerated.
- [ ] 3.3 The TypeScript client regenerated; a type-level test narrows the finding plan.

## 4. Docs

- [ ] 4.1 The wording brief: each new action, result and refusal.
- [ ] 4.2 CHANGELOG.

## 5. Archive

- [ ] 5.1 Merge the deltas, move the change to `archive/`, write `archive-report.md`, accept ADR-0072.
