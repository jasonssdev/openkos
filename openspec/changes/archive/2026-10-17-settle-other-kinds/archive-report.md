# Archive Report: settle-other-kinds

**Archived**: 2026-10-17 (with the last code slice, B2)
**Archive folder**: `openspec/changes/archive/2026-10-17-settle-other-kinds/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issue**: #1476 (the engine and API half; it stays open for the screen). Follow-ups filed: #1496 (add a refused file from the app, which needs ingest through the API) and #1497 (undo a declined contradiction and a reconcile).

## Final State At Close

Each slice is one squash-merged PR, stacked to `main` in order.

| Slice | PR | Authored lines (additions + deletions; generated `api_surface.json`, `openapi.json`, `schema.d.ts` excluded) |
|-------|----|------------------------------------------------------------------------------------------------------------|
| Plan, ADR-0072 Proposed | #1498 | about 680 (docs) |
| A: `reconcile_service.prepare_pair` / `commit_pair`, `ReconcileConflict`; a reconcile resolves the revision row | #1512 | about 376, 195 of them tests |
| B1: finding actions, withheld reasons and freshness on the list; `curate_decisions.preview_finding` and the token-path commits; `wrong_preview` and `already_reconciled` | #1516 | about 1,240, 636 of them tests |
| B2: `openkos.api.preview_finding`, `FindingPreview`, `ReconcilePlan`, `DeclinePlan`, `Decision.finding` and its three details; `POST /v1/decisions/{key}/preview-finding`; the TypeScript client; the wording brief; this archive | this PR | about 850, 514 of them tests, plus this archive |

- **Shipped surface** (all additive, `API_VERSION` unchanged at 1, classifier output `additive` only): `preview_finding`, `FindingPreview`, `ReconcilePlan`, `DeclinePlan`, `ContradictionDetail`, `RevisionDetail`, `RefusedFileDetail`, the field `Decision.finding`, the `Action` members `decline`, `reconcile`, `supersede`, `supersede_reversed`, `revise`, `revise_reversed`, the `NotApplied.reason` values `already_reconciled` and `wrong_preview`, and the `WithheldAction.reason` value `merged_content`. `DecisionPreview.plan`, `Decision.detail` and `ApplyOutcome` are unchanged. Over HTTP: `POST /v1/decisions/{key}/preview-finding` with its own `FindingPlan` union, and the rows `wrong_preview` (422) and `already_reconciled` (409). The client gains `previewFinding` and the two codes.
- **Engine**: the finding previews and commits live in `application/curate_decisions.py` beside every other kind's; `reconcile_service` is split into a public prepare and commit with an in-section guard; `queue_resolution.resolve_reconciled` resolves the revision row (`as_proposed` or `modified`); `decision_revision.proposed_revision` reads a stored revision payload, direction from dates only; `revisions.recorded_inputs_fresh` is the strict freshness rule over a queue row's recorded digests.
- **Found on real data**: a merged-body contradiction is stored with the survivor twice as `pair_ids`; the reader accepts that shape only when `merged_absorbed_id` is set. Smoke-tested on a scratch copy of a real workspace: both of its merged-body contradictions listed with decline and skip, were declined through the API and committed, and `openkos contradictions --declined` lists them.
- **ADR-0072**: Accepted, in all three places (frontmatter, the `**Status:**` body line, the `docs/adr/README.md` row).
- **Not changed, on purpose**: `desktop/ui/` (a later change wires the screen); the CLI verbs and `docs/cli.md`; a refused file has no action (#1496); no revision decline (the CLI has none; `reconcile` is the answer "no").

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| curate-decisions | Updated | 2 MODIFIED replaced in place (Each Kind Carries Its Own Action Set; Staleness Is Marked, Never Hidden), 1 ADDED appended (A Finding Is Settled Through The Write Path Its CLI Verb Uses). |
| stable-python-api | Updated | 2 ADDED appended (Contradictions And Revisions Are Previewed As Findings And Applied As Decisions; Findings Extend v1 Additively). The Purpose names the finding preview. |
| local-http-api | Updated | 1 ADDED appended (A Finding Preview Is Served Beside The Decision Preview). |
| pending-work | Updated | 1 ADDED appended (A Reconcile Resolves The Revision Row Over Its Pair). |

The deltas' "(Previously: ...)" notes were not carried into the living specs.

**Overlap with `changed-rows-recheck`** (#1475, archived in #1501 first): its archive MODIFIED "Staleness Is Marked, Never Hidden" too. This change's MODIFIED text keeps its preview paragraph and both of its scenarios, generalised to the finding preview, and replaces only the freshness paragraph, so the living requirement holds both changes.

### Heading Name-Match

Every `### Requirement:` and `#### Scenario:` heading of each delta was compared as an exact line against its living spec after the merge: each appears exactly once. Before the merge each MODIFIED heading matched exactly once and no ADDED heading existed. No REMOVED or RENAMED headings. No living spec has a duplicate requirement heading.

## Verification

- Strict TDD: RED observed for every unit (reconcile split, revision resolution, the findings surface, the local API routes, the client, and the merged-body payload shape once the fixture matched real data).
- Mutations caught: the revision resolution's edge check, the version's plan line, the in-section row guard, the contradiction digest check, the API's `finding` wiring.
- Gates on the final tree: `ruff check .`, `ruff format --check .`, `mypy .`, `OPENKOS_REQUIRE_API_BASE_REF=1 pytest --cov` (13,849 passed, 96.76% coverage), `evals/run_self_tests.py` (58 of 58), `pnpm --dir desktop` typecheck, test, `check:schema`.
