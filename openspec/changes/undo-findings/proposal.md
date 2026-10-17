# Proposal: Undo A Declined Contradiction And A Reconcile From The App

Tracking issue: #1497 (the engine and API half; the review screen is rebuilt
separately and picks these operations up). Governing decisions: ADR-0060 (the
versioned Python API), ADR-0061 (the local API adapter), ADR-0067 (undoing a
review choice is a two-phase operation keyed by what the choice wrote) and
ADR-0072 (contradictions and revisions are previewed as findings and applied
as decisions). New decision record: ADR-0074 (Proposed, written with this
plan). Paths are under `src/openkos` unless they start with `tests/`, `docs/`,
`desktop/` or `openspec/`.

## Intent

ADR-0072 lets the app settle a contradiction or a revision: decline it, or
reconcile the pair (`reconciled_with`, `supersedes` or `revises`). The wording
brief words those confirmations without an Undo, because `openkos.api` has no
way back. The CLI reverses a decline (`contradictions --reopen`); a reconcile
has no reversal anywhere, and its refusal tells the person to edit the pages or
revert with git.

## Scope

### In scope

- **Reopen a declined contradiction,** following ADR-0067's shape:
  `declined_contradictions` lists every declined finding;
  `preview_reopen_contradiction` / `apply_reopen_contradiction` are keyed by
  the pair and the merged-body discriminator (`merged_absorbed_id`), the
  identity the decline sidecar is keyed on. The apply is the CLI's
  `contradictions --reopen` write (`apply_contradiction_decision`, state
  `open`).
- **Undo a reconcile:** `reconciled_pairs` lists every pair carrying one
  reconcile resolution; `preview_unreconcile` / `apply_unreconcile` are keyed
  by the pair (a pair holds at most one resolution, so the pair names it). The
  undo removes what `reconcile` wrote: the resolution edge or edges, the
  `## Reconciliation` note it appended to each page, and, for a `supersedes`
  edge, the deprecated-status export, withdrawn exactly as `unrelate`
  withdraws it (its prepare is reused). One `log.md` line records the undo.
- **Refusals the app can word:** `NotUndoable` gains `not_declined` and
  `not_reconciled`; a moved digest is the existing `UndoStale`.
- **The local HTTP API:** `GET /v1/declined-contradictions`,
  `POST /v1/contradictions/reopen/{preview,apply}`, `GET /v1/reconciled`,
  `POST /v1/unreconcile/{preview,apply}`, the two new error rows (409), the
  regenerated OpenAPI snapshot and TypeScript client (with client methods and
  tests).
- **Docs:** the wording brief (the Undo copy for the five confirmations and the
  two refusals), the CHANGELOG, ADR-0074.

### Out of scope

- The review screen (`desktop/ui/`), rebuilt in parallel; it adopts these
  operations there.
- A CLI verb for undoing a reconcile. The API operation is the reversal; the CLI
  refusal text is unchanged.
- Re-offering the finding at once. Like every ADR-0067 undo, no queue row is
  resolved or proposed: a reopened or unreconciled contradiction comes back the
  next time contradictions or revisions are checked (served from the persisted
  verdict when its pages are unchanged).
- Supersede through `unrelate`. `unrelate` removes the edge and withdraws the
  out-of-date mark, but leaves both reconciliation notes ("Superseded by ...")
  in place, so it is not the inverse of a supersede the app made; the reconcile
  undo reuses its withdrawal and removes the notes too.

## Approach

`application/undo_service.py` gains the two kinds beside the three ADR-0067
undos, with the same preview / locked re-plan / digest / one-commit shape.
`reconcile_service` gains the inverse of its note builder (remove the note it
wrote, recognised by its anchor) and a public name for its resolution-state
classifier, so the undo reads a pair exactly as `reconcile` does. The facade
(`api/_undo.py`), the routes and the client follow the existing reopen and
unrelate code line for line.

## Affected areas

- `application/undo_service.py`, `application/reconcile_service.py`,
  `application/write_gate.py` (`WriteFailed` operation `unreconcile`).
- `api/_undo.py`, `api/__init__.py`, `tests/unit/api/api_surface.json`.
- `localapi/_routes.py`, `_models.py`, `_errors.py`,
  `tests/unit/localapi/openapi.json`.
- `desktop/client/index.ts`, `errors.ts`, `schema.d.ts` (generated).
- `docs/decision-wording.md`, `CHANGELOG.md`, `docs/adr/`.

## Risks and rollback

- **A note edited by hand.** The undo removes a note only in the shape
  `reconcile` writes it (heading, anchor, one sentence). A note edited around
  its anchor is left as the person left it, and the preview says which pages
  change. Rollback of any undo is `git revert` of its one commit.
- **Concurrent API changes** (ingest, settings) touch the same exports, route
  table and snapshots; each slice rebases on `main` and regenerates the
  snapshots rather than merging generated files by hand.
- All additions are additive within `API_VERSION` 1 (the surface classifier
  decides); reverting a slice removes only its own names.
