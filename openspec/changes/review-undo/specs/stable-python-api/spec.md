# Delta for stable-python-api

## ADDED Requirements

### Requirement: Every Combined Page Is Listable

`openkos.api` MUST export `merged_concepts(workspace)`, returning a tuple of
`MergedConcept`, one per page whose committed merge ledger records at least one
merge and whose concept file exists. A `MergedConcept` MUST carry `concept_id`
and `merges`, the tuple `merge_history` returns for that page (newest first).
The pages MUST be ordered by their newest merge, newest first, then by Concept
ID. A ledger whose page no longer exists MUST be left out, and a ledger that
cannot be read MUST be skipped rather than fail the list. The read MUST NOT take
the workspace lock, build a model client or write anything.

#### Scenario: Two combined pages are listed newest combine first

- GIVEN page `a` absorbed `b`, and later page `c` absorbed `d`
- WHEN `merged_concepts` is called
- THEN it returns `c` then `a`, each with the records `merge_history` returns
  for it, and the workspace is unchanged

#### Scenario: A page absorbed after its own combine is left out

- GIVEN page `m` absorbed `l`, and then page `t` absorbed `m`
- WHEN `merged_concepts` is called
- THEN it lists `t` only

#### Scenario: A workspace with no combine lists nothing

- GIVEN a workspace where nothing was ever merged
- WHEN `merged_concepts` is called
- THEN it returns an empty tuple

### Requirement: A Kept-Separate Ruling Is Listed And Reopened Against Its Version

`openkos.api` MUST export `kept_distinct(workspace)`, returning a tuple of
`KeptDistinct` (`member_ids`, sorted, and `decided_at`), one per identity ruling
whose state is declined, newest first; it MUST NOT take the lock, build a client
or write.

`openkos.api` MUST export `preview_reopen(workspace, member_ids)` and
`apply_reopen(workspace, version, *, wait_seconds=0.0, on_warning=None)`. The
preview MUST return a `ReopenPreview` carrying the sorted members and a
`ReopenVersion`, whose `content_digest` covers the member set and the bytes of
the sidecar holding the ruling, or `NotUndoable("not_kept_distinct")` when no
declined ruling over exactly that member set exists (an id that is not a valid
Concept ID, or fewer than two distinct members, included). The preview MUST NOT
take the lock, build a client or write.

The apply MUST take the workspace lock once, waiting at most `wait_seconds`
(`WorkspaceBusyError` beyond it, nothing written), re-plan from current bytes,
return `NotUndoable` when the re-plan refuses and `UndoStale` when the digest
differs, and otherwise rewrite the ruling with state `open`, commit only the
sidecar it wrote, and return `Reopened` (`member_ids`, `commit_sha`, `None`
when the commit could not be made, reported once through `on_warning`). It MUST
NOT resolve or propose a queue row.

#### Scenario: A keep-separate decision is reopened

- GIVEN a pair kept separate through `apply_decision`
- WHEN `preview_reopen` then `apply_reopen` are called with the pair
- THEN `kept_distinct` no longer lists it, the ruling's state is `open`, and the
  commit holds only the sidecar

#### Scenario: A pair never kept separate is refused

- GIVEN two pages with no ruling
- WHEN `preview_reopen` is called with them
- THEN it returns `NotUndoable("not_kept_distinct")` and nothing is written

#### Scenario: A ruling changed after the preview is stale

- GIVEN a reopen preview, and the same ruling reopened and kept separate again
  by the CLI afterwards
- WHEN `apply_reopen` is called with the preview's version
- THEN it returns `UndoStale` and nothing is written

### Requirement: A Typed Relation Is Removed Against Its Version

`openkos.api` MUST export `preview_unrelate(workspace, source_id,
relation_type, target_id)` and `apply_unrelate(workspace, version, *,
wait_seconds=0.0, on_warning=None)`. The preview MUST plan the removal of the
`{target, type}` entry from the source page's relations exactly as `unrelate`
does, and return an `UnrelatePreview` carrying the edge, whether the target's
deprecated-status export is withdrawn (`withdraws_deprecation`) and an
`UnrelateVersion`, whose `content_digest` covers the edge, the source page's
bytes, and the target page's bytes when the export is withdrawn.
It MUST return `NotUndoable("not_related")` when the source holds no such
relation, either page no longer exists, an id is not a valid Concept ID, the two
ids are the same page, or the type is not a relation type. It MUST NOT take the
lock, build a client or write.

The apply MUST take the lock once as `apply_reopen` does, re-plan, return
`NotUndoable` or `UndoStale` as it does, and otherwise write the source page,
the target's withdrawal when planned and the `log.md` line, commit only those
paths, and return `Unrelated` (the edge, `commit_sha`,
`committed_confidential`). It MUST NOT resolve or propose a queue row.

#### Scenario: A connection made through a decision is removed

- GIVEN a relation written by applying a relation-type decision
- WHEN `preview_unrelate` then `apply_unrelate` are called with its edge
- THEN the source page no longer holds the relation, `log.md` gains one
  line, and the commit holds the source page and `log.md`

#### Scenario: A relation that is not there is refused

- GIVEN a page with no `references` relation to another
- WHEN `preview_unrelate` is called for that edge
- THEN it returns `NotUndoable("not_related")` and nothing is written

#### Scenario: A page edited after the preview is stale

- GIVEN an unrelate preview, and the source page edited afterwards
- WHEN `apply_unrelate` is called with the preview's version
- THEN it returns `UndoStale` and nothing is written

### Requirement: A Type's Tier Is Set Or Restored Against Its Version

`openkos.api` MUST export `VolatilityTier` (`"static"`, `"slow"`,
`"volatile"`), `preview_tier_change(workspace, type_name, tier)` and
`apply_tier_change(workspace, version, *, wait_seconds=0.0, on_warning=None)`.
With a tier the change sets the type's `type_tiers` entry as `set-volatility`
does; with `None` it removes the entry so the engine's default applies, leaving
every other line of `openkos.yaml` byte-identical. The preview MUST return a
`TierChangePreview` carrying the type, the requested tier, `configured_tier`
(the entry now, `None` when there is none) and a `TierChangeVersion`, whose
`content_digest` covers the type, the requested tier and the bytes of
`openkos.yaml`; `NotUndoable("unknown_type")` for a type the engine does not
know; `NotUndoable("tier_unchanged")` when the entry already is the requested
one (absent, for `None`). A tier outside `VolatilityTier` MUST raise
`ValueError`. The preview MUST NOT take the lock, build a client or write.

The apply MUST take the lock once, re-plan, return `NotUndoable` or `UndoStale`
as the other undos do, refuse with `Refused` when `openkos.yaml` carries an
uncommitted edit to the same entry (as a set-tier decision does), and otherwise
write `openkos.yaml`, commit only that change, and return `TierChanged` (the
type, the tier, `commit_sha`). It MUST NOT resolve or propose a queue row.

A set-tier decision's `TierPlan` MUST carry `configured_tier`, the type's entry
before the change (`None` when it had none), so a client can restore it.

#### Scenario: A tier set through a decision is restored by removing the entry

- GIVEN a type with no `type_tiers` entry, set to `volatile` through a decision
  whose preview reported `configured_tier` `None`
- WHEN `preview_tier_change(workspace, type, None)` then `apply_tier_change` are
  called
- THEN `openkos.yaml` has no entry for the type and every other line is
  byte-identical to before the decision

#### Scenario: A tier that already holds is refused

- GIVEN a type whose entry is `slow`
- WHEN `preview_tier_change` is called with `slow`
- THEN it returns `NotUndoable("tier_unchanged")` and nothing is written

#### Scenario: A configuration changed after the preview is stale

- GIVEN a tier-change preview, and `openkos.yaml` edited afterwards
- WHEN `apply_tier_change` is called with the preview's version
- THEN it returns `UndoStale` and nothing is written

### Requirement: Undo And The Combined-Pages Read Extend v1 Additively

Every name this change adds (`merged_concepts`, `MergedConcept`,
`kept_distinct`, `KeptDistinct`, `preview_reopen`, `apply_reopen`,
`ReopenPreview`, `ReopenVersion`, `Reopened`, `preview_unrelate`,
`apply_unrelate`, `UnrelatePreview`, `UnrelateVersion`, `Unrelated`,
`preview_tier_change`, `apply_tier_change`, `TierChangePreview`,
`TierChangeVersion`, `TierChanged`, `VolatilityTier`, `NotUndoable`,
`UndoStale`) and the field `TierPlan.configured_tier` MUST be classified
additive by the surface classifier, and `API_VERSION` MUST stay 1.

#### Scenario: The undo surface diff is additive

- GIVEN the regenerated `api_surface.json`
- WHEN the classifier compares it with the snapshot on `main`
- THEN every change is additive and `API_VERSION` is 1
