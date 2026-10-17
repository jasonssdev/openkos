# Proposal: A Changed Decision Can Be Checked Again, And A Decision About A Missing Page Is Never Offered

Tracking issue: #1475, items 1 and 2, found on the first hands-on run of the
review screen against a copy of a real workspace. Item 3 (the order of the
questions for pages of different kinds) is out of scope and stays with the
design-system restyle (#1477). Governing decisions: ADR-0053 (the two-phase
decision surface), ADR-0059 (judging is quote-bound and never merges), ADR-0060
(the versioned Python API), ADR-0066 (the review screen is a tested view model).
Paths are under `src/openkos` unless they start with `tests/`, `docs/`,
`desktop/` or `openspec/`.

## Intent

**A changed row loops forever.** `list_decisions` lists a row whose inputs moved
since it was computed with `fresh=False` and, for a judged-same pair, the actions
`merge`, `keep_distinct` and `skip`. `preview_decision` returns a plan for it;
`apply_decision` then answers `Stale("row_inputs_changed")` every time, because
the commit re-reads the row inside its section and finds its inputs changed. The
app re-previews on `Stale`, so the person loops on "Changed". Preview and apply
disagree about the same row.

There is also no way back. Measured on a copy of the reporter's workspace: the
identity quote already counts the changed pair among the groups to judge, and a
run re-judges it and persists a fresh verdict. The row still lists as changed
afterwards, because the run's write-through to an open row is amend-only by
design and refuses a row computed from other inputs ("replacing a row whose
inputs moved is a producer's job"), and the app never runs the model-free
refresh that is that producer. A refresh right after the run replaces the row,
and the pair is then fresh, judged same and mergeable. Check all is also only
shown while an identity pair is unjudged, so a list holding only changed rows
offers no check at all.

**Rows naming pages that no longer exist.** 28 of the 33 open rows on that copy
name a page absent from the bundle, absorbed by merges made with a build that
predates #1280. Retirement of rows naming an absorbed page runs only inside a
merge (`queue_resolution._retire_naming`), so a queue written before it, or
edited by hand, never heals, and the screen shows "A page that couldn't be read"
and offers a decision about nothing.

## Scope

### In scope

- **Preview agrees with apply.** `preview_decision` answers a row whose inputs
  changed with the same `Stale("row_inputs_changed")` the apply returns, for every
  write action; skip still answers `NotApplied("skipped")`. `list_decisions` is
  unchanged: the row stays listed, `fresh=False`, with its actions (ADR-0053:
  list, never hide).
- **Refresh retires rows naming a missing page.** `refresh_decision_queue`
  retires as `stale` every open `identity` and `relation_type` row whose targets
  name a concept absent from the bundle, whether or not the identity pass is
  complete, inside a commit section. `KindRefresh` gains `retired_missing`, the
  count. Contradiction rows are left alone: a contradiction found while combining
  names the absorbed page on purpose, and is still settled by reconcile or
  unmerge.
- **The review screen.** A changed decision offers **Check again** and
  **Decide later**, never a choice it cannot apply. Check again refreshes the
  queue (no AI model), reloads, and, when the decision is still changed, asks the
  person's agreement to check that kind once, as Check all does. Every check
  refreshes the queue before it quotes and after it runs. Check all is shown when
  an identity decision is unjudged or changed. The screen refreshes the queue on
  each visit, so rows naming a missing page are gone before they are listed. A
  stale apply whose decision is now changed is shown as changed, not previewed
  again.
- **Docs:** the wording brief (Check again, the changed notice), the CHANGELOG.

### Out of scope

- **Item 3 of #1475**, the question order for pages of different kinds (#1477).
- **The CLI.** `curate` keeps its own walk; no verb changes.
- **A one-item judging quote.** Check again quotes the kind, as Check all does;
  the quote already includes a changed group the candidate search still finds.
  A changed group the search no longer reaches (past its cap) stays listed as
  changed and can only be decided later; see design D4.
- **The judging run's write-through** stays amend-only (judging-application-service);
  the refresh is the publisher.

## Approach

`curate_decisions.preview_decision` checks the row's freshness after the action
checks. `queue_refresh.refresh_decision_queue` calls a new
`pending_queue.retire_open_naming_absent` before the producers run, with an
absence predicate the application supplies. `openkos.api.KindRefresh` carries the
count; the OpenAPI snapshot and the TypeScript client are regenerated. The
desktop review model gains `visit()` and `recheck()`, and `startCheck` takes the
kind. Design records the decisions.

## Rollback

Every change is additive within `API_VERSION` 1 and each slice is one squash
commit: reverting it restores the previous behaviour. The retirement only moves
open rows to `stale`; nothing is deleted, and a producer pass recomputes any row
still true over the pages that exist.
