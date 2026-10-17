# Tasks: A Changed Decision Can Be Checked Again, And A Decision About A Missing Page Is Never Offered

Tracking issue: #1475 (items 1 and 2; item 3 stays with #1477). Inputs:
`proposal.md`, `design.md` (authoritative) and the delta specs under `specs/`.
PR text names the change in prose ("the `changed-rows-recheck` change") and uses
`Refs #1475` on every slice; no slice closes #1475, because item 3 remains.

## Review Workload Forecast

Authored changed lines are additions plus deletions; the generated
`tests/unit/api/api_surface.json`, `tests/unit/localapi/openapi.json` and
`desktop/client/schema.d.ts` are excluded. The ~400-line figure is advisory.

| Slice | Title | Forecast (authored) | Depends on |
|-------|-------|---------------------|------------|
| S1 | Plan, preview refusal, refresh retires rows naming a missing page | ~350 code + docs | none |
| S2 | Review screen: Check again, refresh around checks and on visit, wording brief | ~350 | S1 |
| S3 | Archive: merge delta specs, archive report | docs | S2 |

Delivery strategy: stacked-to-main; each slice branches from `main` after the
previous one is squash-merged. S1 and S2 shipped as one PR (#1491): `main` moved
four times while S1 waited on CI, each needing a rebase and a fresh run, so the
screen slice was folded in rather than queued behind another round.

## 1. Engine and API (S1)

- [x] 1.1 RED: `preview_decision` on a stale identity row returns a plan (test
      expects `Stale("row_inputs_changed")` for merge and keep-distinct, and
      `NotApplied("skipped")` for skip).
- [x] 1.2 GREEN: `curate_decisions.preview_decision` refuses a stale row after
      the action checks (design D1).
- [x] 1.3 RED: refresh leaves open an identity row and a relation row naming a
      missing concept on a truncated pass; a contradiction naming an absorbed
      concept stays open.
- [x] 1.4 GREEN: `pending_queue.retire_open_naming_absent`, called by
      `queue_refresh.refresh_decision_queue` before the producers; the counts on
      `RefreshOutcome` and `openkos.api.KindRefresh.retired_missing` (design D5).
- [x] 1.5 Regenerate `api_surface.json`, the OpenAPI snapshot and
      `desktop/client/schema.d.ts`; the surface classifier passes (additive).
- [x] 1.6 Gates: ruff check, ruff format --check, mypy, pytest --cov, eval
      self-tests, `pnpm check:schema`.

## 2. Review screen (S2)

- [x] 2.1 RED: a changed decision offers Check again and Decide later only.
- [x] 2.2 RED: Check again refreshes and lists again; gone, fresh and still
      changed outcomes (design D3).
- [x] 2.3 RED: a check refreshes before quoting and after the run (design D2);
      Check all is shown for a changed identity decision.
- [x] 2.4 RED: a stale apply whose decision is now changed is not previewed
      again.
- [x] 2.5 RED: `visit()` refreshes before listing (design D6).
- [x] 2.6 GREEN: `wording.ts` (Check again, the changed notice, the consent
      heading by kind), `model.ts` (`visit`, `recheck`, `startCheck(kind)`),
      `main.ts` (`visit`), `render.ts` (Check all's condition and line only).
- [x] 2.7 `docs/decision-wording.md` and the CHANGELOG.
- [x] 2.8 Gates: Python gates, `pnpm typecheck`, `pnpm test`, `pnpm build`,
      `pnpm check:schema`.

## 3. Archive (S3)

- [x] 3.1 Merge every delta into its main spec, each heading exactly once.
- [x] 3.2 Move the change to `openspec/changes/archive/` and write
      `archive-report.md`.
