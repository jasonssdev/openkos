# Delta for stable-python-api

## ADDED Requirements

### Requirement: A Declined Contradiction Is Listed And Reopened Against Its Version

`openkos.api` MUST export `declined_contradictions(workspace)`, returning a
tuple of `DeclinedContradiction` (`first_id` and `second_id`, sorted,
`merged_absorbed_id`, `decided_at`), one per contradiction decision record
whose state is declined, newest first; it MUST NOT take the lock, build a
client, open the findings store or write.

`openkos.api` MUST export `preview_reopen_contradiction(workspace, first_id,
second_id, merged_absorbed_id=None)` and `apply_reopen_contradiction(workspace,
version, *, wait_seconds=0.0, on_warning=None)`. The preview MUST return a
`ContradictionReopenPreview` carrying the sorted pair, the discriminator and a
`ContradictionReopenVersion`, whose `content_digest` covers the pair, the
discriminator and the bytes of the sidecar holding the record, or
`NotUndoable("not_declined")` when no declined record for exactly that pair and
discriminator exists (an id that is not a valid Concept ID included). The pair MAY be given in either order. The preview MUST NOT
take the lock, build a client or write.

The apply MUST take the workspace lock once, waiting at most `wait_seconds`
(`WorkspaceBusyError` beyond it, nothing written), re-plan from current bytes,
return `NotUndoable` when the re-plan refuses and `UndoStale` when the digest
differs, and otherwise rewrite the record with state `open` (the write
`contradictions --reopen` makes), commit only the sidecar it wrote, and return
`ContradictionReopened` (`first_id`, `second_id`, `merged_absorbed_id`,
`commit_sha`, `None` when the commit could not be made, reported once through
`on_warning`). It MUST NOT resolve or propose a queue row.

#### Scenario: A declined contradiction is reopened

- GIVEN a contradiction declined through `apply_decision`
- WHEN `preview_reopen_contradiction` then `apply_reopen_contradiction` are
  called with its pair
- THEN `declined_contradictions` no longer lists it, its record's state is
  `open`, and the commit holds only the sidecar

#### Scenario: The merged-body finding is a different record

- GIVEN the typed-edge finding over a pair declined, and no decline for the
  merged-body finding over the same pair
- WHEN `preview_reopen_contradiction` is called with the pair and the absorbed
  id
- THEN it returns `NotUndoable("not_declined")` and nothing is written

#### Scenario: A record changed after the preview is stale

- GIVEN a reopen preview, and another decline recorded in the same sidecar
  afterwards
- WHEN `apply_reopen_contradiction` is called with the preview's version
- THEN it returns `UndoStale` and nothing is written

### Requirement: A Reconcile Is Listed And Undone Against Its Version

`openkos.api` MUST export `reconciled_pairs(workspace)`, returning a tuple of
`ReconciledPair` (`first_id` and `second_id`, sorted, `relation_type` one of
`reconciled_with`, `supersedes`, `revises`, and `holder_id` and `target_id`,
both `None` for `reconciled_with`), one per pair of existing pages whose
relations hold exactly one reconcile resolution between them, ordered by pair.
A pair holding disagreeing resolutions MUST be left out. The read MUST NOT take
the lock, build a client or write.

`openkos.api` MUST export `preview_unreconcile(workspace, first_id, second_id)`
and `apply_unreconcile(workspace, version, *, wait_seconds=0.0,
on_warning=None)`. The preview MUST return an `UnreconcilePreview` carrying the
sorted pair, the resolution's `relation_type`, `holder_id` and `target_id`,
`withdraws_deprecation` (the superseded page stops being marked out of date),
`pages_changed` (the pages whose files change) and an `UnreconcileVersion`, or
`NotUndoable("not_reconciled")` when the pair holds no resolution, holds
disagreeing ones, names a page that does not exist, names one page twice or
names an invalid id. The pair MAY be given in either order. The preview MUST NOT
take the lock, build a client or write. The version's `content_digest` MUST
cover the pair, the resolution, both pages' bytes and whether the mark is
withdrawn.

The apply MUST take the workspace lock once, re-plan, and return `NotUndoable`
or `UndoStale` with nothing written as the reopen apply does. Otherwise it MUST
write the inverse of the reconcile:

- every edge of the resolution's type between the two pages removed, as
  `unrelate` removes an edge;
- for `supersedes`, the superseded page's deprecated-status export withdrawn
  exactly when `unrelate` would withdraw it (no other page still supersedes it,
  and its status was not authored by a person);
- on each page, the `## Reconciliation` note anchored on the other page removed
  when it has the shape `reconcile` writes (heading, anchor, one sentence); a
  note edited around its anchor MUST be left unchanged;
- on a page rewritten for the undo, a `relations` list left empty dropped, as
  `unrelate` drops it;
- one `log.md` line;

then commit only the pages it changed and `log.md` in one commit, and return
`Unreconciled` (`first_id`, `second_id`, `relation_type`, `commit_sha`,
`committed_confidential`). It MUST NOT resolve or propose a queue row and MUST
NOT refresh a derived index.

#### Scenario: A supersede made through the app is undone

- GIVEN two pages in the form the engine writes them (`status: stable`), and
  a contradiction between them settled with `supersede` through `apply_decision`
- WHEN `preview_unreconcile` then `apply_unreconcile` are called with its pair
- THEN neither page holds a `supersedes` edge or a reconciliation note, the
  superseded page is no longer marked deprecated, both pages' bytes are the ones
  they had before the supersede, and one commit holds both pages and `log.md`

#### Scenario: A symmetric reconcile is undone on both pages

- GIVEN two pages reconciled with `reconciled_with`
- WHEN the reconcile is undone
- THEN neither page holds a `reconciled_with` edge to the other or a note
  anchored on it, and `reconciled_pairs` no longer lists the pair

#### Scenario: A page still superseded by another keeps its mark

- GIVEN page `b` superseded by `a` through a reconcile and by `c` through
  `relate`
- WHEN the reconcile between `a` and `b` is undone
- THEN the preview says the mark is not withdrawn, and `b` stays deprecated

#### Scenario: A pair never reconciled is refused

- GIVEN two pages with no resolution between them
- WHEN `preview_unreconcile` is called with them
- THEN it returns `NotUndoable("not_reconciled")` and nothing is written

#### Scenario: A page edited after the unreconcile preview is stale

- GIVEN an unreconcile preview, and one of the pages edited afterwards
- WHEN `apply_unreconcile` is called with the preview's version
- THEN it returns `UndoStale` and nothing is written

### Requirement: Undoing Findings Extends v1 Additively

The names this change adds (`declined_contradictions`, `DeclinedContradiction`,
`preview_reopen_contradiction`, `apply_reopen_contradiction`,
`ContradictionReopenPreview`, `ContradictionReopenVersion`,
`ContradictionReopened`, `reconciled_pairs`, `ReconciledPair`,
`preview_unreconcile`, `apply_unreconcile`, `UnreconcilePreview`,
`UnreconcileVersion`, `Unreconciled`) and the reasons `not_declined` and
`not_reconciled` on `NotUndoable` MUST be classified as additive by the surface
snapshot's classifier, and `API_VERSION` MUST stay 1.

#### Scenario: The findings-undo surface diff is additive

- GIVEN the regenerated surface snapshot
- WHEN the classifier compares it with the previous one
- THEN every line is additive and `API_VERSION` is unchanged
