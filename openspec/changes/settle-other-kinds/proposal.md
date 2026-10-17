# Proposal: Settle Contradictions And Revisions From The App, And Say Why A File Was Refused

Tracking issue: #1476 (the engine and API half; the screen is a later change).
Governing decisions: ADR-0053 (the two-phase decision surface), ADR-0060 (the
versioned Python API), ADR-0061 (the local API adapter), ADR-0064 and ADR-0067
(two-phase operations bound to a version). New decision record: ADR-0072
(Proposed, written with this plan). Follow-ups filed: #1496 (add a refused file
from the app, which needs ingest through the API) and #1497 (undo a declined
contradiction and a reconcile). Paths are under `src/openkos` unless they start
with `tests/`, `docs/`, `desktop/` or `openspec/`.

## Intent

To review lists `contradiction`, `revision` and `watch_refusal` rows with no
action, and the app says "This can't be settled in the app yet." The CLI settles
the first two without a model: `contradictions --decline` records a decline, and
`reconcile` records how two pages relate (`reconciled_with`, `supersedes` or
`revises`), including the `--from-findings` walk over revision findings. The app
reaches the engine only through `openkos.api`, which exposes none of it. The
third kind resolves through `openkos ingest`, which runs extraction with a model
and has no API yet.

## Scope

### In scope

- **Actions on the list,** from what the CLI offers, relative to the decision's
  `targets` order (first, second):
  - a typed-edge `contradiction`: `decline`, `reconcile`, `supersede`
    (the first replaces the second), `supersede_reversed`, `skip`;
  - a merged-body `contradiction` (found inside a combined page): `decline`,
    `skip`; `reconcile` is withheld with the new reason `merged_content`,
    because the pair names a page that no longer exists, and Split again
    already reaches it;
  - a `revision` whose dates order the pair: the one write the
    `--from-findings` walk offers (`supersede…` for a reversal, `revise…` for a
    refinement, held by the later Decision), `reconcile`, `skip`; one whose
    dates do not: all four directed writes, `reconcile`, `skip`;
  - a `watch_refusal`: no action (see Out of scope).
- **Freshness** computed for contradiction and revision rows, with the rules
  their CLI readers use, so a changed finding is shown as changed and refused.
- **Typed details** on a new defaulted `Decision.finding` field:
  `ContradictionDetail`, `RevisionDetail`, `RefusedFileDetail`. No confidence.
- **A finding preview,** `preview_finding(workspace, decision_key, action)`,
  returning `FindingPreview` with a `ReconcilePlan` or a `DeclinePlan` and the
  same `DecisionVersion`; `apply_decision` applies it. `preview_decision` given a
  finding action returns `NotApplied("wrong_preview")`, and `preview_finding` given any other action the same.
- **The engine:** a narrow `application/finding_decisions.py` that builds the
  previews and commits them on the token path, reusing
  `reconcile_service` (split into a public prepare and commit, behaviour
  unchanged) and `contradictions_service.apply_contradiction_decision`.
- **The queue:** a reconcile also resolves the open revision row over the
  pair (today only the contradiction row), `as_proposed` when the edge and the
  holder are the proposal's, `modified` otherwise; this reaches the CLI's
  `reconcile` too.
- **The local HTTP API:** `POST /v1/decisions/{key}/preview-finding`, the new
  refusal rows, the regenerated OpenAPI snapshot and TypeScript client.
- **Docs:** the wording brief (the copy for each new action, result and
  refusal), the CHANGELOG, ADR-0072.

### Out of scope

- **Screens.** `desktop/ui/` does not change; a later change wires the screen.
- **Adding a refused file.** The remedy is an ingest, which calls a model and has
  no API operation; it needs its own quote and consent (#1496). The detail says
  why the file was refused, so the app can name what the person can do in their
  folder.
- **Declining a revision.** The CLI has no such decline; `reconcile` ("both still
  stand") is the CLI's way to answer no, and it keeps the pair out of later
  passes.
- **`revise` on a contradiction.** The two-id `reconcile --revision` exists, but a
  refinement does not settle two pages that say incompatible things; the app
  offers the three CLI resolutions a contradiction walk names.
- **Undo** of a decline or a reconcile (#1497).
- **Derived indexes.** No apply refreshes them (statement 5 of the facade).
- **The CLI.** No verb changes; `docs/cli.md` does not change.

## Approach

The list gains actions, freshness and details for the two finding kinds. Their
previews build from the row's own payload: a reconcile plans through
`reconcile_service.prepare_pair` (Phase A, no write) and versions the two pages'
bytes with the planned edge; a decline versions the pair and the pages' bytes.
The apply re-plans from current bytes, compares the version, and commits inside
one commit section that re-checks the row, through the same write cores the CLI
uses, so the queue resolves exactly as it does for `reconcile` and
`contradictions --decline`. ADR-0072 records why findings preview through their
own function and route within v1.

## Rollback

Additive within `API_VERSION` 1; each slice is one squash commit and reverts
cleanly. The one behaviour that reaches the CLI (a reconcile resolving the
revision row) is a queue-cache change: reverting it leaves the row for the next
revisions pass to retire, as today.
