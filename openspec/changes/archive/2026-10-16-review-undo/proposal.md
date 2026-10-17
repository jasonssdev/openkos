# Proposal: Undo Every Review Choice From The App, And List Every Combined Page

Tracking issues: #1467 (list every combined page) and #1466 (undo keep separate,
connections and how-quickly settings), both found while building the review
screen (#1463). Governing decisions: ADR-0053 (the two-phase decision surface),
ADR-0060 (the versioned Python API), ADR-0061 (the local API adapter), ADR-0064
(unmerge is two-phase, bound to a version over the ledger), ADR-0065 and ADR-0066
(the desktop web view). New decision record: ADR-0067 (Proposed, written with this
plan). Paths are under `src/openkos` unless they start with `tests/`, `docs/`,
`desktop/` or `openspec/`.

## Intent

`docs/decision-wording.md` gives four kinds of choice an **Undo** on their
confirmation. The desktop app offers one: a combine's **Split again**, and only
for the pages combined while the app has been open, because nothing lists which
pages have a merge ledger. Keep separate, connect and "how quickly it goes out
of date" show no Undo, because `openkos.api` has no operation that reverses them
(rule 4: "offers the undo where one exists"). The terminal can reverse all four
(`unmerge`, `duplicates --reopen`, `unrelate`, `set-volatility`); the app cannot.

## Scope

### In scope

- **#1467, a combined-pages read.** `merged_concepts(workspace)` lists every page
  whose merge ledger records at least one merge, newest combine first, each with
  its `MergeRecord`s (the records `merge_history` returns). It reads the ledger
  sidecars only: no lock, no model, no write. `GET /v1/merged` serves it.
- **#1466, keep separate.** `kept_distinct(workspace)` lists every member set a
  person ruled distinct and has not reopened; `preview_reopen(workspace,
  member_ids)` and `apply_reopen(workspace, version)` reopen one ruling, the API
  form of `duplicates --kept-distinct` and `duplicates --reopen`.
- **#1466, connections.** `preview_unrelate(workspace, source_id,
  relation_type, target_id)` and `apply_unrelate(workspace, version)` remove one
  typed relation, the API form of `unrelate`.
- **#1466, how quickly it goes out of date.** `preview_tier_change(workspace,
  type_name, tier)` and `apply_tier_change(workspace, version)` set one type's
  tier, or, with `tier=None`, remove the type's entry so the engine's default
  applies again. A set-tier decision's preview (`TierPlan`) gains
  `configured_tier`, the type's entry before the change, which is what an undo
  restores.
- **One refusal and one stale type for the three undos,** `NotUndoable` with
  reasons `not_kept_distinct`, `not_related`, `tier_unchanged` and
  `unknown_type`, and `UndoStale`, each mapped to an HTTP row.
- **The local HTTP API:** `GET /v1/merged`, `GET /v1/kept-distinct`,
  `POST /v1/reopen/{preview,apply}`, `POST /v1/unrelate/{preview,apply}`,
  `POST /v1/tiers/{preview,apply}`, their error rows, the regenerated OpenAPI
  snapshot and the regenerated TypeScript client.
- **The review screen:** the combined pages come from `GET /v1/merged`, so Split
  again survives a restart; the confirmations of keep separate, connect and
  set-tier offer **Undo**.
- **Docs:** the wording brief (the new copy, and the #1466/#1467 gaps closed),
  the CHANGELOG, ADR-0067.

### Out of scope

- **The CLI.** No verb changes; `docs/cli.md` does not change.
- **A settings screen and a page view.** "Pages you kept separate" with **Ask me
  again**, a page's connections with **Remove** and the tier setting under the
  library's settings need screens MVP 6 has not built. The API operations they
  will use ship here; the screens do not.
- **The queue.** An undo resolves no row and proposes none. A reopened group
  returns to To review when the queue is next refreshed; the app refreshes it
  after a reopen (design D7).
- **Derived indexes.** No undo refreshes them (statement 5 of the facade).

## Approach

A new application service, `application/undo_service.py`, owns the three
previews and applies; each re-plans from current bytes under one lock and
compares a content digest, as ADR-0064 decided for unmerge. The combined-pages
read joins `unmerge_service`. `openkos.api` maps them in `_undo.py` and
`_unmerge.py`; `openkos.localapi` adds the routes. The desktop model and wording
gain the undo transitions. Design records the decisions; ADR-0067 records why the
undos are keyed by what the choice wrote rather than by its decision.

## Rollback

Every change is additive within `API_VERSION` 1 and every slice is one squash
commit, so any slice reverts on its own. The app degrades to today's behavior
when an undo route is absent: no undo is offered.

## Risks

- **An undo after a later change.** A connection removed after another verb
  rewrote the page, or a tier restored after someone else set it, would undo more
  than the person meant. The version digests the bytes each undo rests on, so
  any change between preview and apply is `UndoStale`; and the app previews and
  applies in one click right after the choice.
- **Reopening a ruling a group decision recorded.** A group decision records
  rulings for the members left unmarked. They are listed by `kept_distinct`
  like any other and reopening one affects only that member set.
